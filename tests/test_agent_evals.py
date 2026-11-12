import json
import sys
from pathlib import Path

import pytest

from evals import runner


def test_manifest_is_frozen_and_has_fixed_splits():
    manifest = runner.load_manifest()

    assert len(manifest["tasks"]) == 20
    assert sum(task["split"] == "dev" for task in manifest["tasks"]) == 12
    assert sum(task["split"] == "heldout" for task in manifest["tasks"]) == 8
    assert all(len(task["task_hash"]) == 64 for task in manifest["tasks"])
    assert all(len(task["scoring_hash"]) == 64 for task in manifest["tasks"])


@pytest.mark.parametrize("task", runner.load_manifest()["tasks"], ids=lambda task: task["id"])
def test_offline_oracle_validates_each_task(task):
    result = runner.run_task(
        task,
        live=False,
        profile="baseline",
        limits={},
        config=None,
    )

    assert result["status"] == "passed", result["error"]
    assert result["harness_pass"] is True
    assert result["completion"] is None
    assert result["token_usage"] is None


def test_scoring_rejects_modified_public_tests(tmp_path):
    task = runner.load_manifest()["tasks"][0]
    repo = runner.create_task_repo(task, tmp_path)
    original_tests = runner._file_hashes(repo)
    runner._write_files(repo, task["solution_files"])
    (repo / "tests/test_public.py").write_text("# skipped\n")
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()

    passed, evidence = runner._score(task, repo, evaluator, original_tests)

    assert passed is False
    assert evidence["tests_unchanged"] is False


def test_offline_cli_never_requests_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner,
        "_model_config",
        lambda: pytest.fail("offline mode attempted to configure a model"),
    )

    assert runner.main([
        "--task", "repair-001-clamp",
        "--output", str(tmp_path / "results"),
        "--run-name", "test-offline",
    ]) == 0
    row = json.loads((tmp_path / "results/results.jsonl").read_text())
    assert row["mode"] == "offline_oracle"
    assert row["model"] is None


def test_live_requires_explicit_environment(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "MODEL", "MODEL_ID",
        "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="--live requires"):
        runner._model_config()


def test_model_credentials_are_removed_before_subprocesses(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-token")

    runner._scrub_model_credentials()

    assert "ANTHROPIC_API_KEY" not in runner.os.environ
    assert "ANTHROPIC_AUTH_TOKEN" not in runner.os.environ
    assert runner._safe_endpoint("https://user:pass@example.test:8443/private?key=x") == "https://example.test:8443"


def test_profile_tool_sets_are_bounded():
    baseline, _ = runner._minimal_tools("baseline")
    retrieval, _ = runner._minimal_tools("retrieval")

    assert {tool["name"] for tool in baseline} == {
        "read_file", "write_file", "edit_file", "bash",
    }
    assert {tool["name"] for tool in retrieval} == {
        "read_file", "write_file", "edit_file", "bash", "lookup_symbol",
    }


def test_local_git_repository_offline_integration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calc.py").write_text("def add(left, right):\n    return left - right\n")
    (source / "tests").mkdir()
    (source / "tests/test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n"
        "class TestAdd(unittest.TestCase):\n"
        "    def test_adds(self): self.assertEqual(add(2, 3), 5)\n\n"
        "if __name__ == '__main__': unittest.main()\n"
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "eval@example.invalid"],
        ["git", "config", "user.name", "Eval"],
        ["git", "add", "."],
        ["git", "commit", "-q", "-m", "buggy base"],
    ):
        assert runner._run(command, source)["exit_code"] == 0
    base_sha = runner._run(["git", "rev-parse", "HEAD"], source)["stdout"].strip()

    acceptance = tmp_path / "acceptance"
    acceptance.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from calc import add\n"
        "assert add(-2, 7) == 5\n"
    )
    oracle = tmp_path / "oracle.patch"
    oracle.write_text(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(left, right):\n"
        "-    return left - right\n"
        "+    return left + right\n"
    )
    manifest_path = tmp_path / "repo_tasks.json"
    manifest_path.write_text(json.dumps({
        "version": "test",
        "tasks": [{
            "id": "real-repo-add",
            "repo_path": str(source),
            "base_sha": base_sha,
            "issue": "add subtracts instead of adding",
            "public_test_command": f"{sys.executable} -m unittest discover -s tests -q",
            "protected_paths": ["tests"],
            "acceptance_script": str(acceptance),
            "oracle_patch": str(oracle),
            "require_public_failure": True,
        }],
    }))

    output = tmp_path / "results"
    assert runner.main([
        "--repo-manifest", str(manifest_path),
        "--output", str(output),
        "--run-name", "repo-integration",
    ]) == 0
    result = json.loads((output / "results.jsonl").read_text())

    assert result["status"] == "passed", result["error"]
    assert result["mode"] == "offline_repo_oracle"
    assert result["completion"] is None
    assert (source / "calc.py").read_text().endswith("return left - right\n")
    assert runner._run(["git", "status", "--porcelain"], source)["stdout"] == ""
