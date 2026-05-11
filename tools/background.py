CATEGORY = "system"

BG_TOOLS = [
    {"name": "background_run", "description": "Run command in background.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
]

def bg_run_handler(bg, **kw):
    return bg.run(kw["command"], kw.get("timeout", 120))

def bg_check_handler(bg, **kw):
    return bg.check(kw.get("task_id"))

BG_HANDLERS = {
    "background_run": bg_run_handler,
    "check_background": bg_check_handler,
}

def get_tools():
    return BG_TOOLS

def create_handlers(**ctx):
    bg = ctx.get("background")
    return {
        "background_run": lambda **kw: bg_run_handler(bg, **kw),
        "check_background": lambda **kw: bg_check_handler(bg, **kw),
    }