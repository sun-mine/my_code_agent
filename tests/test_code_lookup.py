import pytest

from components.code_lookup import lookup_symbol


def test_cross_file_definitions_references_and_imports(tmp_path):
    (tmp_path / "service.py").write_text("class Service:\n    async def fetch(self):\n        return 1\n")
    (tmp_path / "consumer.py").write_text("from service import Service\n\nasync def run():\n    return await Service().fetch()\n")
    result = lookup_symbol(tmp_path, "fetch")
    assert result["definitions"] == [{"path": "service.py", "line": 2,
                                       "end_line": 3, "name": "Service.fetch", "kind": "function"}]
    assert result["references"][0]["path"] == "consumer.py"
    assert result["references"][0]["line"] == 4
    assert result["imports"][0]["text"] == "from service import Service"
    assert lookup_symbol(tmp_path, "Service.fetch")["definitions"] == result["definitions"]


def test_generated_files_and_symlinks_are_not_read(tmp_path):
    outside = tmp_path.parent / "external.py"
    outside.write_text("def secret(): pass\n")
    (tmp_path / "link.py").symlink_to(outside)
    generated = tmp_path / "venv"
    generated.mkdir()
    (generated / "module.py").write_text("def secret(): pass\n")
    assert lookup_symbol(tmp_path, "secret")["definitions"] == []
    with pytest.raises(ValueError, match="escapes"):
        lookup_symbol(tmp_path, "secret", "../external.py")


def test_invalid_syntax_retains_reference_candidates_and_reports_parse_error(tmp_path):
    (tmp_path / "broken.py").write_text("def fetch(:\n")
    result = lookup_symbol(tmp_path, "fetch")
    assert result["definitions"] == []
    assert result["parse_errors"] == [{"path": "broken.py", "line": 1}]
    assert result["references"][0]["line"] == 1


def test_failed_edit_returns_actual_source_without_modifying_file(tmp_path):
    from tools import base
    previous = base.get_workdir()
    base.set_workdir(tmp_path)
    try:
        (tmp_path / "source.py").write_text("actual = 1\n")
        result = base.run_edit("source.py", "guessed = 1", "actual = 2")
        assert result.is_error
        assert "actual = 1" in result
        assert (tmp_path / "source.py").read_text() == "actual = 1\n"
    finally:
        base.set_workdir(previous)
