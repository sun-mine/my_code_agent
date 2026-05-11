"""Tool modules auto-discovery.

Each tool module should export:
  - CATEGORY: str              # "core" | "task" | "memory" | "safety" | "team" | "worktree" | "system"
  - get_tools() -> list[dict]  # tool definitions
  - create_handlers(**ctx) -> dict  # name->callable handlers
"""

import importlib
import pkgutil
import os

_TOOL_MODULES = None
_TOOLS_CACHE = None
_CATEGORY_CACHE = {}

# Always-loaded core modules
_CORE_MODULES = {"base", "todo"}


def _discover_modules():
    global _TOOL_MODULES
    if _TOOL_MODULES is not None:
        return _TOOL_MODULES
    _TOOL_MODULES = []
    package_path = os.path.dirname(__file__)
    for _, name, is_pkg in pkgutil.iter_modules([package_path]):
        if name == "__init__":
            continue
        if is_pkg:
            continue
        try:
            mod = importlib.import_module(f".{name}", package=__package__)
            _TOOL_MODULES.append(mod)
        except Exception:
            pass
    return _TOOL_MODULES


def _module_category(mod) -> str:
    """Resolve module category from its CATEGORY attr, or infer from name."""
    cat = getattr(mod, "CATEGORY", None)
    if cat:
        return cat
    # Infer: module basename, except base->core, todo->core
    name = mod.__name__.rsplit(".", 1)[-1]
    return "core" if name in _CORE_MODULES else name


def get_core_tools():
    """Return only core tools (always loaded, small footprint)."""
    tools = []
    for mod in _discover_modules():
        if _module_category(mod) == "core":
            if hasattr(mod, "get_tools"):
                tools.extend(mod.get_tools())
    return tools


def get_tools_by_categories(categories: list) -> list:
    """Return tools for specific categories."""
    tools = []
    cat_set = set(categories)
    for mod in _discover_modules():
        if _module_category(mod) in cat_set:
            if hasattr(mod, "get_tools"):
                tools.extend(mod.get_tools())
    return tools


def get_all_tools():
    """Aggregate all tool definitions from all modules."""
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    tools = []
    for mod in _discover_modules():
        if hasattr(mod, "get_tools"):
            tools.extend(mod.get_tools())
    _TOOLS_CACHE = tools
    return tools


def get_all_categories() -> dict:
    """Return {category: [tool_names]} for all discovered modules."""
    global _CATEGORY_CACHE
    if _CATEGORY_CACHE:
        return dict(_CATEGORY_CACHE)
    result = {}
    for mod in _discover_modules():
        if not hasattr(mod, "get_tools"):
            continue
        cat = _module_category(mod)
        names = [t["name"] for t in mod.get_tools()]
        result.setdefault(cat, []).extend(names)
    _CATEGORY_CACHE = result
    return dict(result)


def create_all_handlers(**ctx):
    """Create handlers from all tool modules with given context dependencies."""
    handlers = {}
    for mod in _discover_modules():
        if hasattr(mod, "create_handlers"):
            try:
                handlers.update(mod.create_handlers(**ctx))
            except Exception:
                pass
    return handlers


def create_handlers_for_categories(categories: list, **ctx):
    """Create handlers only for specific categories."""
    handlers = {}
    cat_set = set(categories)
    for mod in _discover_modules():
        if _module_category(mod) not in cat_set:
            continue
        if hasattr(mod, "create_handlers"):
            try:
                handlers.update(mod.create_handlers(**ctx))
            except Exception:
                pass
    return handlers


def invalidate_cache():
    global _TOOLS_CACHE, _CATEGORY_CACHE
    _TOOLS_CACHE = None
    _CATEGORY_CACHE = {}
