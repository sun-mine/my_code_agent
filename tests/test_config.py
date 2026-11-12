import os

import pytest

from components import config


_PROFILES = ("OLLAMA", "DEEPSEEK", "KIMI", "ANTHROPIC", "CUSTOM")
_ENV_NAMES = {
    "LLM_PROVIDER",
    "AGENT_WORKDIR",
    "AGENT_VERIFY_COMMAND",
    "GITHUB_MCP_TOKEN",
    "A2A_REVIEW_URL",
    "A2A_REVIEW_TOKEN",
    "API_HOST",
    "API_PROXY_TARGET",
    "API_CORS_ORIGINS",
    "AGENT_MODE",
    *(
        f"{profile}_{suffix}"
        for profile in _PROFILES
        for suffix in ("BASE_URL", "API_KEY", "MODEL", "AUTH_MODE", "MODELS")
    ),
    "API_PORT",
    "FRONTEND_PORT",
    "TOOL_APPROVAL_TIMEOUT",
    "TOKEN_THRESHOLD",
    "GITHUB_MCP_TIMEOUT",
    "A2A_REVIEW_TIMEOUT",
    "AGENT_MAX_ROUNDS",
    "AGENT_MAX_TOOL_CALLS",
    "AGENT_MAX_SECONDS",
    "AGENT_MAX_TOKENS",
    "AGENT_MAX_REPAIR_ATTEMPTS",
}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # dotenv writes directly into os.environ; use a disposable mapping so its
    # writes cannot leak into later test modules when monkeypatch restores keys.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_load_environment_uses_project_root_and_preserves_process_values(tmp_path, monkeypatch):
    project = tmp_path / "application"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".env").write_text(
        "LLM_PROVIDER=kimi\nKIMI_API_KEY=from-project\nKIMI_MODEL=project-model\n"
    )
    (elsewhere / ".env").write_text(
        "LLM_PROVIDER=deepseek\nKIMI_API_KEY=from-cwd\nKIMI_MODEL=cwd-model\n"
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", project)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("KIMI_API_KEY", "from-process")

    config.load_environment()

    assert os.environ["LLM_PROVIDER"] == "kimi"
    assert os.environ["KIMI_MODEL"] == "project-model"
    assert os.environ["KIMI_API_KEY"] == "from-process"


def test_fresh_environment_defaults_to_local_ollama():
    model = config.Config()

    assert (model.provider, model.base_url, model.model) == (
        "ollama",
        "http://127.0.0.1:11434",
        "qwen2.5:3b",
    )
    assert model.api_key == "ollama"
    assert model.available_models == ("qwen2.5:3b",)


def test_custom_profile_uses_explicit_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("CUSTOM_BASE_URL", "https://proxy.example/anthropic")
    monkeypatch.setenv("CUSTOM_API_KEY", "proxy-token")
    monkeypatch.setenv("CUSTOM_MODEL", "proxy-model")

    model = config.Config()

    assert model.provider == "custom"
    assert model.model == "proxy-model"
    assert model.api_key == "proxy-token"


def test_model_override_allows_a_dedicated_anthropic_review_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "review-key")

    model = config.Config(provider="anthropic", model_override="review-model")

    assert model.provider == "anthropic"
    assert model.model == "review-model"
    assert model.available_models == ("review-model",)


def test_selected_profile_is_isolated_and_models_are_deduplicated(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "selected-token")
    monkeypatch.setenv("KIMI_MODEL", "kimi-primary")
    monkeypatch.setenv("KIMI_MODELS", "kimi-primary, kimi-long, kimi-long")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unselected-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sdk-environment-token")

    model = config.Config()

    assert model.provider == "kimi"
    assert model.api_key == "selected-token"
    assert model.available_models == ("kimi-primary", "kimi-long")
    assert model.client_kwargs() == {
        "base_url": "https://api.moonshot.cn/anthropic",
        "api_key": "selected-token",
        "default_headers": {"Authorization": "Bearer selected-token"},
    }


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        config.Config(provider="private-provider")


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("CUSTOM_BASE_URL", "Missing CUSTOM_BASE_URL"),
        ("CUSTOM_MODEL", "Missing CUSTOM_MODEL"),
        ("CUSTOM_API_KEY", "Missing CUSTOM_API_KEY"),
    ],
)
def test_client_requirements_are_checked_only_when_building_kwargs(
    monkeypatch, missing, message
):
    values = {
        "CUSTOM_BASE_URL": "https://proxy.example/anthropic",
        "CUSTOM_MODEL": "proxy-model",
        "CUSTOM_API_KEY": "proxy-token",
    }
    values[missing] = ""
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    model = config.Config(provider="custom")

    with pytest.raises(ValueError, match=message):
        model.client_kwargs()


def test_client_auth_mode_is_checked_only_when_building_kwargs(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "selected-token")
    monkeypatch.setenv("KIMI_AUTH_MODE", "unsupported")

    model = config.Config(provider="kimi")

    with pytest.raises(ValueError, match="AUTH_MODE"):
        model.client_kwargs()


def test_server_settings_do_not_require_a_selected_model_credential(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")

    server = config.Config()

    assert server.port == 8000
    assert server.mode == "auto"


def test_server_settings_parse_ports_cors_approval_and_mode(monkeypatch):
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "8123")
    monkeypatch.setenv("FRONTEND_PORT", "5199")
    monkeypatch.setenv("API_PROXY_TARGET", "https://proxy.example/api/")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://one.example, https://two.example")
    monkeypatch.setenv("TOOL_APPROVAL_TIMEOUT", "7.5")
    monkeypatch.setenv("AGENT_MODE", "plan")

    server = config.Config()

    assert server.host == "127.0.0.1"
    assert server.port == 8123
    assert server.frontend_port == 5199
    assert server.proxy_target == "https://proxy.example/api"
    assert server.cors_origins == ["https://one.example", "https://two.example"]
    assert server.approval_timeout == 7.5
    assert server.mode == "plan"


@pytest.mark.parametrize("name", ["API_PORT", "FRONTEND_PORT", "TOOL_APPROVAL_TIMEOUT"])
def test_invalid_server_numbers_raise_conversion_errors(monkeypatch, name):
    monkeypatch.setenv(name, "not-a-number")

    with pytest.raises(ValueError):
        config.Config()


def test_all_run_budgets_are_loaded_into_config(tmp_path, monkeypatch):
    values = {
        "TOKEN_THRESHOLD": "9001",
        "GITHUB_MCP_TIMEOUT": "1.5",
        "A2A_REVIEW_TIMEOUT": "2.5",
        "AGENT_MAX_ROUNDS": "3",
        "AGENT_MAX_TOOL_CALLS": "4",
        "AGENT_MAX_SECONDS": "5.5",
        "AGENT_MAX_TOKENS": "6",
        "AGENT_MAX_REPAIR_ATTEMPTS": "0",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    loaded = config.Config(workdir=tmp_path)

    assert loaded.token_threshold == 9001
    assert loaded.github_mcp_timeout == 1.5
    assert loaded.a2a_review_timeout == 2.5
    assert loaded.run_limits == config.RunLimits(
        max_rounds=3,
        max_tool_calls=4,
        max_seconds=5.5,
        max_tokens=6,
        max_repair_attempts=0,
    )


@pytest.mark.parametrize(
    "name",
    [
        "TOKEN_THRESHOLD",
        "GITHUB_MCP_TIMEOUT",
        "A2A_REVIEW_TIMEOUT",
        "AGENT_MAX_ROUNDS",
        "AGENT_MAX_TOOL_CALLS",
        "AGENT_MAX_SECONDS",
        "AGENT_MAX_TOKENS",
        "AGENT_MAX_REPAIR_ATTEMPTS",
    ],
)
def test_invalid_budget_numbers_raise_conversion_errors(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "not-a-number")

    with pytest.raises(ValueError):
        config.Config(workdir=tmp_path)


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_positive_budget_rejects_non_positive_values(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("AGENT_MAX_SECONDS", raw)

    with pytest.raises(ValueError, match="max_seconds"):
        config.Config(workdir=tmp_path)

