from tools import base


def test_list_files_skips_dependency_and_cache_directories(tmp_path, monkeypatch):
    source = tmp_path / "src"
    dependency = tmp_path / "frontend" / "node_modules" / "package"
    cache = source / "__pycache__"
    source.mkdir()
    dependency.mkdir(parents=True)
    cache.mkdir()
    (source / "main.py").write_text("print('hello')")
    (dependency / "index.js").write_text("export default {}")
    (cache / "main.cpython-312.pyc").write_bytes(b"cached")
    monkeypatch.setattr(base, "_workdir", tmp_path.resolve())

    result = base.run_list_files(".").splitlines()

    assert result == ["src/main.py"]


def test_list_files_allows_explicit_dependency_paths(tmp_path, monkeypatch):
    dependency = tmp_path / "frontend" / "node_modules" / "package"
    dependency.mkdir(parents=True)
    (dependency / "index.js").write_text("export default {}")
    monkeypatch.setattr(base, "_workdir", tmp_path.resolve())

    dependency_result = base.run_list_files("frontend/node_modules", depth=3)
    package_result = base.run_list_files("frontend/node_modules/package")

    expected = "frontend/node_modules/package/index.js"
    assert expected in dependency_result.splitlines()
    assert package_result == expected
