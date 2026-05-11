CATEGORY = "core"

TODO_TOOL = {
    "name": "TodoWrite",
    "description": "Update task tracking list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "activeForm": {"type": "string"}
                    },
                    "required": ["content", "status", "activeForm"]
                }
            }
        },
        "required": ["items"]
    }
}

def todo_handler(todo_mgr, **kw):
    return todo_mgr.update(kw["items"])

def get_tools():
    return [TODO_TOOL]

def create_handlers(**ctx):
    todo_mgr = ctx.get("todo_manager")
    return {"TodoWrite": lambda **kw: todo_handler(todo_mgr, **kw)}