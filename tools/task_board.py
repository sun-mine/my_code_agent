CATEGORY = "task"
TASK_TOOLS = [
    {"name": "task_create", "description": "Create persistent task.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
    {"name": "task_get", "description": "Get task by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "task_update", "description": "Update task status/deps.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "add_blocked_by": {"type": "array", "items": {"type": "integer"}}, "remove_blocked_by": {"type": "array", "items": {"type": "integer"}}}, "required": ["task_id"]}},
    {"name": "task_list", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}}},
]

def task_handlers_factory(tmgr):
    return {
        "task_create": lambda **kw: tmgr.create(kw["subject"], kw.get("description", "")),
        "task_get": lambda **kw: tmgr.get(kw["task_id"]),
        "task_update": lambda **kw: tmgr.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("remove_blocked_by")),
        "task_list": lambda **kw: tmgr.list_all(),
    }

def get_tools():
    return TASK_TOOLS

def create_handlers(**ctx):
    tmgr = ctx.get("task_manager")
    return task_handlers_factory(tmgr)
