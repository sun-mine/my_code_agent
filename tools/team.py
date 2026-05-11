CATEGORY = "team"
TEAM_TOOLS = [
    {"name": "spawn_teammate", "description": "Spawn autonomous teammate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send message to teammate.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string"}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read lead inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "idle", "description": "Enter idle state.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim task by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
]

def team_handlers_factory(team, bus, tmgr):
    return {
        "spawn_teammate": lambda **kw: team.spawn(kw["name"], kw["role"], kw["prompt"]),
        "list_teammates": lambda **kw: team.list_all(),
        "send_message": lambda **kw: bus.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
        "read_inbox": lambda **kw: json.dumps(bus.read_inbox("lead"), indent=2),
        "broadcast": lambda **kw: bus.broadcast("lead", kw["content"], team.member_names()),
        "idle": lambda **kw: "Lead does not idle.",
        "claim_task": lambda **kw: tmgr.claim(kw["task_id"], "lead"),
    }

TEAM_HANDLERS = team_handlers_factory

def get_tools():
    return TEAM_TOOLS

def create_handlers(**ctx):
    team = ctx.get("team_manager")
    bus = ctx.get("message_bus")
    tmgr = ctx.get("task_manager")
    return team_handlers_factory(team, bus, tmgr)