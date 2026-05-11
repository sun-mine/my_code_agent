CATEGORY = "team"
import json, uuid

shutdown_reqs = {}
plan_reqs = {}

def handle_shutdown(bus, teammate):
    req_id = str(uuid.uuid4())[:8]
    shutdown_reqs[req_id] = {"target": teammate, "status": "pending"}
    bus.send("lead", teammate, "Please shutdown.", "shutdown_request", {"request_id": req_id})
    return f"Shutdown request {req_id} sent to {teammate}"

def handle_plan_review(bus, request_id, approve, feedback=""):
    req = plan_reqs.get(request_id)
    if not req:
        return f"Unknown plan {request_id}"
    req["status"] = "approved" if approve else "rejected"
    bus.send("lead", req["from"], feedback, "plan_approval_response", {"request_id": request_id, "approve": approve})
    return f"Plan {req['status']}"

PROTOCOL_TOOLS = [
    {"name": "shutdown_request", "description": "Request teammate shutdown.", "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "plan_approval", "description": "Approve/reject plan.", "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}}, "required": ["request_id", "approve"]}},
]

PROTOCOL_HANDLERS = {
    "shutdown_request": lambda bus, **kw: handle_shutdown(bus, kw["teammate"]),
    "plan_approval": lambda bus, **kw: handle_plan_review(bus, kw["request_id"], kw["approve"], kw.get("feedback", "")),
}

def get_tools():
    return PROTOCOL_TOOLS

def create_handlers(**ctx):
    bus = ctx.get("message_bus")
    def _make(handler):
        return lambda **kw: handler(bus, **kw)
    return {name: _make(h) for name, h in PROTOCOL_HANDLERS.items()}