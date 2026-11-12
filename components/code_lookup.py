"""Bounded Python symbol lookup; references are text candidates, not a call graph."""
import ast
import os
import re
from pathlib import Path

SKIP_DIRS = {"node_modules", "__pycache__", "venv", "env", "dist", "build", "site-packages"}


def lookup_symbol(root: Path, symbol: str, path: str = ".") -> dict:
    if not isinstance(symbol, str) or not re.fullmatch(r"[\w]+(?:\.[\w]+)*", symbol):
        raise ValueError("symbol must be a Python identifier or dotted name")
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Path escapes workspace")
    if not target.exists():
        raise ValueError(f"Path not found: {path}")
    files = []
    if target.is_file():
        files = [target]
    else:
        for directory, dirs, names in os.walk(target, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
            files.extend(Path(directory) / name for name in sorted(names) if name.endswith(".py"))
            if len(files) > 800:
                break
    result = {"symbol": symbol, "definitions": [], "references": [], "imports": [],
              "scanned_files": 0, "truncated": len(files) > 800, "parse_errors": []}
    leaf = symbol.rsplit(".", 1)[-1]
    reference = re.compile(rf"\b{re.escape(leaf)}\b")
    for file in files[:800]:
        if file.suffix != ".py" or file.is_symlink() or not file.resolve().is_relative_to(root):
            continue
        if file.stat().st_size > 500_000:
            result["truncated"] = True
            continue
        relative = file.relative_to(root).as_posix()
        try:
            source = file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            result["truncated"] = True
            continue
        result["scanned_files"] += 1
        if not reference.search(source):
            continue
        definitions, imports = [], []

        def visit(node, parents=()):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = ".".join((*parents, node.name))
                if qualified == symbol or ("." not in symbol and node.name == symbol):
                    definitions.append({"path": relative, "line": node.lineno,
                                        "end_line": node.end_lineno, "name": qualified[:500],
                                        "kind": "class" if isinstance(node, ast.ClassDef) else "function"})
                parents = (*parents, node.name)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append({"path": relative, "line": node.lineno,
                                "text": (ast.get_source_segment(source, node) or "")[:500]})
            for child in ast.iter_child_nodes(node):
                visit(child, parents)

        try:
            visit(ast.parse(source))
        except SyntaxError as exc:
            result["parse_errors"].append({"path": relative, "line": exc.lineno})
        definition_lines = {item["line"] for item in definitions}
        references = [{"path": relative, "line": line, "text": text.strip()[:240]}
                      for line, text in enumerate(source.splitlines(), 1)
                      if line not in definition_lines and reference.search(text)]
        for key, values, limit in (("definitions", definitions, 30),
                                   ("references", references, 50), ("imports", imports, 30)):
            remaining = limit - len(result[key])
            result[key].extend(values[:remaining])
            result["truncated"] |= len(values) > remaining
    return result
