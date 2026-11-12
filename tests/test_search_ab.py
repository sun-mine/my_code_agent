import hashlib
import json

import pytest

from evals import search_ab


def test_schedule_has_32_balanced_runs_with_reversed_localization_order():
    tasks = search_ab.load(search_ab.TASKS)
    rows = search_ab.scheduled_runs(tasks)

    assert len(rows) == 32
    for task in tasks["localization"]:
        orders = []
        for repeat in (1, 2):
            selected = sorted(
                (row for row in rows if row["kind"] == "loc"
                 and row["task_id"] == task["id"] and row["repeat"] == repeat),
                key=lambda row: row["order"],
            )
            assert len(selected) == 2
            orders.append(tuple(row["profile"] for row in selected))
        assert orders[0] in {("A", "B"), ("B", "A")}
        assert orders[1] == tuple(reversed(orders[0]))


@pytest.mark.parametrize(
    "path",
    ["/absolute.py", "../outside.py", "pkg/../outside.py", r"pkg\module.py"],
)
def test_validate_paths_rejects_unsafe_snapshot_names(path):
    with pytest.raises(ValueError, match="Invalid snapshot path"):
        search_ab.validate_paths({path: "content"})


@pytest.mark.parametrize(
    "location",
    [
        {"path": "src/service.py"},
        {"path": "src/service.py", "symbol": "Other.fetch", "line": 10},
        {"path": "src/service.py", "symbol": "Service.fetch", "line": 99},
    ],
)
def test_location_scoring_requires_exact_symbol_and_definition_line(location):
    task = {"targets": [["src/service.py", "Service.fetch"]]}
    definitions = {("src/service.py", "Service.fetch"): (10, 14)}

    score = search_ab.score_locations(task, [[location]], definitions)

    assert score["all_targets_found"] is False
    assert score["recall_at_5"] == 0


def test_location_scoring_accepts_an_exact_location():
    task = {"targets": [["src/service.py", "Service.fetch"]]}
    definitions = {("src/service.py", "Service.fetch"): (10, 14)}

    score = search_ab.score_locations(
        task,
        [[{"path": "src/service.py", "symbol": "Service.fetch", "line": 12}]],
        definitions,
    )

    assert score["all_targets_found"] is True
    assert score["recall_at_5"] == 1


def _repair_case(tmp_path):
    public = (
        "import unittest\n"
        "from source.unit_00 import clamp\n\n"
        "class ClampTest(unittest.TestCase):\n"
        "    def test_upper_bound(self):\n"
        "        self.assertEqual(clamp(9, 0, 5), 5)\n\n"
        "if __name__ == '__main__': unittest.main()\n"
    )
    files = {
        "source/__init__.py": "",
        "source/unit_00.py": "def clamp(value, low, high):\n    return value\n",
        "source/unit_01.py": "VALUE = 'decoy'\n",
        "tests/test_public.py": public,
    }
    fixed = (
        "def clamp(value, low, high):\n"
        "    if low > high:\n"
        "        raise ValueError('invalid bounds')\n"
        "    return max(low, min(value, high))\n"
    )
    task = {
        "files": files,
        "solution_files": {"source/unit_00.py": fixed},
        "acceptance": public.replace("test_upper_bound", "test_hidden_bound"),
    }
    repo = tmp_path / "repo"
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    return task, repo, evaluator, fixed


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("unittest.py", "raise AssertionError('must never shadow stdlib')\n"),
        ("tests/test_public.py", "# disabled\n"),
        ("source/unit_01.py", "VALUE = 'tampered'\n"),
    ],
)
def test_repair_scoring_rejects_files_outside_the_target(path, content, tmp_path):
    task, repo, evaluator, _ = _repair_case(tmp_path)
    original = search_ab.workspace_hashes(repo)
    (repo / path).write_text(content)

    score = search_ab.score_repair(task, repo, evaluator, original)

    assert score["patch_accepted"] is False
    assert score["changed_paths"] == [path]
    assert score["test_evidence"]["unauthorized_changes"] == [path]


def test_repair_scoring_accepts_the_oracle_change_only(tmp_path):
    task, repo, evaluator, fixed = _repair_case(tmp_path)
    original = search_ab.workspace_hashes(repo)
    (repo / "source/unit_00.py").write_text(fixed)

    score = search_ab.score_repair(task, repo, evaluator, original)

    assert score["patch_accepted"] is True
    assert score["changed_paths"] == ["source/unit_00.py"]
    assert score["test_evidence"]["tests_unchanged"] is True


def _frozen_bundle(base):
    output = base / "output"
    root = base / "root"
    output.mkdir()
    root.mkdir()
    tasks = {"localization": [], "repairs": []}
    snapshot = {"project": {"src/app.py": "VALUE = 1\n"}}
    fixtures = {"repair-1": {"files": {"source/unit_00.py": "VALUE = 0\n"}}}
    implementation = root / "implementation.py"
    implementation.write_text("VERSION = 1\n")
    index = b"frozen index bytes"
    (output / "index.json.gz").write_bytes(index)
    search_ab.save(output / "tasks.json", tasks)
    search_ab.save(output / "source-snapshot.json", snapshot)
    search_ab.save(output / "repair-fixtures.json", fixtures)
    search_ab.save(output / "protocol.json", {
        "task_hash": search_ab.digest(tasks),
        "source_hashes": {"project": search_ab.digest(snapshot["project"])},
        "repair_fixtures_hash": search_ab.digest(fixtures),
        "index_sha256": hashlib.sha256(index).hexdigest(),
        "implementation_hashes": {
            "implementation.py": hashlib.sha256(implementation.read_bytes()).hexdigest(),
        },
    })
    return output, root


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("snapshot", "Frozen source changed"),
        ("fixture", "Frozen repair fixtures changed"),
        ("index", "Frozen index changed"),
        ("implementation", "Evaluation implementation changed"),
    ],
)
def test_validate_frozen_detects_every_frozen_input_change(target, message, monkeypatch, tmp_path):
    output, root = _frozen_bundle(tmp_path)
    monkeypatch.setattr(search_ab, "ROOT", root)
    search_ab.validate_frozen(output, check_models=False)

    if target == "snapshot":
        value = json.loads((output / "source-snapshot.json").read_text())
        value["project"]["src/app.py"] = "VALUE = 2\n"
        search_ab.save(output / "source-snapshot.json", value)
    elif target == "fixture":
        value = json.loads((output / "repair-fixtures.json").read_text())
        value["repair-1"]["files"]["source/unit_00.py"] = "VALUE = 2\n"
        search_ab.save(output / "repair-fixtures.json", value)
    elif target == "index":
        (output / "index.json.gz").write_bytes(b"changed")
    else:
        (root / "implementation.py").write_text("VERSION = 2\n")

    with pytest.raises(ValueError, match=message):
        search_ab.validate_frozen(output, check_models=False)


def _result(task_id, profile, protocol="frozen", **extra):
    return {
        "kind": "loc",
        "task_id": task_id,
        "repeat": 1,
        "profile": profile,
        "protocol_hash": protocol,
        **extra,
    }


def test_valid_pairs_keeps_only_complete_healthy_pairs_and_counts_failures():
    schedule = [
        {"kind": "loc", "task_id": task, "repeat": 1, "profile": profile}
        for task in ("complete", "infra", "missing")
        for profile in ("A", "B")
    ]
    rows = [
        _result("complete", "A"),
        _result("complete", "B"),
        _result("infra", "A", infrastructure_error="provider_error"),
        _result("infra", "B"),
        _result("missing", "A"),
    ]

    result = search_ab.valid_pairs(rows, schedule, "frozen")

    assert {(row["task_id"], row["profile"]) for row in result["rows"]} == {
        ("complete", "A"), ("complete", "B")
    }
    assert result["missing"] == 1
    assert result["infra"] == 1
    assert result["complete"] is False
    assert result["scheduled"] == 6


def test_valid_pairs_rejects_mixed_protocol_rows():
    schedule = [{"kind": "loc", "task_id": "task", "repeat": 1, "profile": "A"}]

    with pytest.raises(ValueError, match="mixed-protocol"):
        search_ab.valid_pairs([_result("task", "A", protocol="other")], schedule, "frozen")
