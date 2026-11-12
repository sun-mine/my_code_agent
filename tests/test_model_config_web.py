import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from components import config


_MODEL_ENV_NAMES = {
    "LLM_PROVIDER",
    "AGENT_WORKDIR",
    "GITHUB_MCP_TOKEN",
    *(f"{profile}_{suffix}"
      for profile in ("OLLAMA", "DEEPSEEK", "KIMI", "ANTHROPIC", "CUSTOM")
      for suffix in ("BASE_URL", "API_KEY", "MODEL", "AUTH_MODE", "MODELS")),
}


@pytest.fixture(autouse=True)
def isolated_model_environment(monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in _MODEL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def model_api(tmp_path, monkeypatch):
    settings = {
        "LLM_PROVIDER": "kimi",
        "KIMI_API_KEY": "kimi-secret",
        "KIMI_MODELS": "shared-model,kimi-long",
        "DEEPSEEK_API_KEY": "deepseek-secret",
        "DEEPSEEK_MODEL": "shared-model",
        "DEEPSEEK_MODELS": "deepseek-long",
        "ANTHROPIC_MODEL": "missing-key-model",
        "ANTHROPIC_MODELS": "missing-key-long",
        "OLLAMA_MODELS": "ollama-explicit",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)

    # api_server loads PROJECT_ROOT/.env on first import. Point that one-time
    # load at this empty directory so local credentials cannot affect the test.
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    import api_server
    from api.routes import models as model_routes

    cfg = config.Config(workdir=tmp_path)
    old_client = Mock(name="old_client")
    agent = SimpleNamespace(client=old_client, model=cfg.model)
    team = SimpleNamespace(client=old_client, model=cfg.model, threads={})
    agent_config = {"workdir": str(tmp_path), "provider": cfg.provider, "model": cfg.model}
    history = [{"role": "user", "content": "keep this"}]
    created_clients = []

    def create_client(**kwargs):
        client = Mock(name=f"client_{len(created_clients)}")
        client.kwargs = kwargs
        created_clients.append(client)
        return client

    class OllamaResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({"models": [
                {"name": "ollama-tagged", "capabilities": ["completion"]},
                {"name": "ollama-legacy"},
                {"name": "ollama-embedding", "capabilities": ["embedding"]},
            ]}).encode()

    ollama_requests = []

    def open_ollama(url, timeout):
        ollama_requests.append((url, timeout))
        return OllamaResponse()

    monkeypatch.setattr("urllib.request.urlopen", open_ollama)
    monkeypatch.setattr(model_routes, "Anthropic", create_client)
    monkeypatch.setattr(api_server, "_cfg", cfg)
    monkeypatch.setattr(api_server, "agent_instance", agent)
    monkeypatch.setattr(api_server, "team_manager", team)
    monkeypatch.setattr(api_server, "agent_config", agent_config)
    monkeypatch.setattr(api_server, "conversation_history", history)
    monkeypatch.setattr(api_server, "_running_ctx", None)
    api_server.app.config.update(TESTING=True)
    return SimpleNamespace(
        api_server=api_server,
        client=api_server.app.test_client(),
        cfg=cfg,
        agent=agent,
        team=team,
        agent_config=agent_config,
        history=history,
        old_client=old_client,
        created_clients=created_clients,
        ollama_requests=ollama_requests,
        model_routes=model_routes,
    )


def data(response):
    return response.get_json()["data"]


def runtime_snapshot(state):
    return {
        "cfg": tuple(getattr(state.cfg, name) for name in (
            "provider", "model", "base_url", "api_key", "auth_mode", "available_models",
        )),
        "agent_client": state.agent.client,
        "agent_model": state.agent.model,
        "team_client": state.team.client,
        "team_model": state.team.model,
        "agent_config": state.agent_config.copy(),
        "history": list(state.history),
    }


def assert_runtime_unchanged(state, before):
    assert runtime_snapshot(state) == before
    assert state.api_server.conversation_history is state.history
    state.old_client.close.assert_not_called()


def selected_model(response):
    payload = data(response)
    return {name: payload[name] for name in ("model", "provider")}


def test_bearer_client_and_derived_client_keep_the_selected_profile_key(monkeypatch):
    from anthropic import Anthropic

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "selected-kimi-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unrelated-sdk-key")
    model = config.Config()
    client = Anthropic(**model.client_kwargs())
    derived = client.with_options(timeout=1)

    try:
        for actual in (client, derived):
            headers = actual.default_headers
            assert headers["Authorization"] == "Bearer selected-kimi-key"
            assert "unrelated-sdk-key" not in repr(headers)
    finally:
        derived.close()
        client.close()


def test_model_catalog_lists_real_models_across_providers(model_api):
    response = model_api.client.get("/api/models")

    assert response.status_code == 200
    payload = data(response)
    assert (payload["current_provider"], payload["current"]) == ("kimi", "kimi-k3")
    by_key = {(item["provider"], item["id"]): item for item in payload["models"]}
    assert set(by_key) == {
        ("ollama", "ollama-explicit"),
        ("ollama", "ollama-tagged"),
        ("ollama", "ollama-legacy"),
        ("deepseek", "shared-model"),
        ("deepseek", "deepseek-long"),
        ("kimi", "kimi-k3"),
        ("kimi", "shared-model"),
        ("kimi", "kimi-long"),
        ("anthropic", "missing-key-model"),
        ("anthropic", "missing-key-long"),
    }
    assert all(item["label"] == item["id"] for item in by_key.values())
    assert all(item["provider"] == item["provider"].lower() for item in by_key.values())
    assert by_key[("deepseek", "shared-model")]["configured"] is True
    assert by_key[("kimi", "shared-model")]["configured"] is True
    assert by_key[("anthropic", "missing-key-model")]["configured"] is False
    assert model_api.ollama_requests == [("http://127.0.0.1:11434/api/tags", 2)]


def test_omitted_provider_selects_same_named_model_from_current_provider(model_api):
    cfg_identity = id(model_api.cfg)
    response = model_api.client.post("/api/model", json={"model": "shared-model"})

    assert response.status_code == 200
    assert selected_model(response) == {"model": "shared-model", "provider": "kimi"}
    assert id(model_api.cfg) == cfg_identity
    assert model_api.cfg.provider == "kimi"
    assert model_api.agent.client is model_api.created_clients[0]
    assert model_api.team.client is model_api.created_clients[0]
    model_api.old_client.close.assert_called_once_with()


def test_cross_provider_switch_updates_runtime_without_losing_history(model_api):
    cfg_identity = id(model_api.cfg)
    history_identity = id(model_api.history)
    expected = config.Config(
        workdir=model_api.cfg.workdir, provider="deepseek", model_override="shared-model",
    )

    response = model_api.client.post(
        "/api/model", json={"model": "shared-model", "provider": "deepseek"},
    )

    assert response.status_code == 200
    assert selected_model(response) == {"model": "shared-model", "provider": "deepseek"}
    assert id(model_api.cfg) == cfg_identity
    names = ("model", "provider", "base_url", "api_key", "auth_mode", "available_models")
    assert tuple(getattr(model_api.cfg, name) for name in names) == tuple(
        getattr(expected, name) for name in names
    )
    new_client = model_api.created_clients[0]
    assert new_client.kwargs == expected.client_kwargs()
    assert (model_api.agent.client, model_api.agent.model) == (new_client, "shared-model")
    assert (model_api.team.client, model_api.team.model) == (new_client, "shared-model")
    assert model_api.agent_config == {
        "workdir": str(model_api.cfg.workdir),
        "provider": "deepseek",
        "model": "shared-model",
    }
    assert id(model_api.api_server.conversation_history) == history_identity
    assert model_api.history == [{"role": "user", "content": "keep this"}]
    model_api.old_client.close.assert_called_once_with()


def test_incomplete_and_unknown_models_cannot_be_selected(model_api):
    before = runtime_snapshot(model_api)

    incomplete = model_api.client.post(
        "/api/model", json={"model": "missing-key-model", "provider": "anthropic"},
    )
    unknown_model = model_api.client.post(
        "/api/model", json={"model": "not-listed", "provider": "deepseek"},
    )
    unknown_provider = model_api.client.post(
        "/api/model", json={"model": "shared-model", "provider": "unknown"},
    )

    assert [r.status_code for r in (incomplete, unknown_model, unknown_provider)] == [400, 400, 400]
    assert model_api.created_clients == []
    assert_runtime_unchanged(model_api, before)


def test_running_agent_or_live_team_thread_blocks_switch(model_api, monkeypatch):
    before = runtime_snapshot(model_api)
    monkeypatch.setattr(model_api.api_server, "_running_ctx", object())
    busy = model_api.client.post(
        "/api/model", json={"model": "deepseek-long", "provider": "deepseek"},
    )

    monkeypatch.setattr(model_api.api_server, "_running_ctx", None)
    model_api.team.threads["idle-worker"] = SimpleNamespace(is_alive=lambda: True)
    live_team = model_api.client.post(
        "/api/model", json={"model": "deepseek-long", "provider": "deepseek"},
    )

    assert (busy.status_code, live_team.status_code) == (409, 409)
    assert model_api.created_clients == []
    assert_runtime_unchanged(model_api, before)


def test_client_creation_failure_is_atomic_and_does_not_expose_secrets(model_api, monkeypatch):
    before = runtime_snapshot(model_api)

    def fail_client(**_):
        raise RuntimeError("backend rejected deepseek-secret")

    monkeypatch.setattr(model_api.model_routes, "Anthropic", fail_client)
    response = model_api.client.post(
        "/api/model", json={"model": "deepseek-long", "provider": "deepseek"},
    )

    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "deepseek-secret" not in body
    assert "kimi-secret" not in body
    assert "backend rejected" not in body
    assert_runtime_unchanged(model_api, before)
