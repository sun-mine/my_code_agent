import json
import threading
import time
from queue import Queue
from anthropic import Anthropic
from components.message_bus import MessageBus
from components.task_manager import TaskManager
from components.config import Config

class TeammateManager:
    def __init__(self, bus: MessageBus, task_mgr: TaskManager, client: Anthropic, model: str, config: Config):
        self.bus = bus
        self.task_mgr = task_mgr
        self.client = client
        self.model = model
        self.config = config
        self.team_dir = config.team_dir
        self.team_dir.mkdir(exist_ok=True)
        self.config_path = self.team_dir / "config.json"
        self.team_config = self._load_config()
        self.threads = {}
        self.poll_interval = 5
        self.idle_timeout = 60

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save_config(self):
        self.config_path.write_text(json.dumps(self.team_config, indent=2))

    def _find_member(self, name: str) -> dict:
        for m in self.team_config["members"]:
            if m["name"] == name:
                return m
        return None

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find_member(name)
        if member and member["status"] not in ("idle", "shutdown"):
            return f"Error: '{name}' is currently {member['status']}"
        if member:
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.team_config["members"].append(member)
        self._save_config()
        t = threading.Thread(target=self._loop, args=(name, role, prompt), daemon=True)
        self.threads[name] = t
        t.start()
        return f"Spawned '{name}' (role: {role})"

    def _loop(self, name: str, role: str, prompt: str):
        from tools.base import BASE_HANDLERS
        team_name = self.team_config["team_name"]
        sys = f"You are '{name}', role: {role}, team: {team_name}. Use tools to complete tasks. Use idle when done."
        msgs = [{"role": "user", "content": prompt}]
        tools = [
            {"name": "bash", "description": "Run command.", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}}, "required": ["to", "content"]}},
            {"name": "idle", "description": "Signal no more work.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "claim_task", "description": "Claim task.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
        ]
        while True:
            # 工作阶段
            for _ in range(50):
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    msgs.append({"role": "user", "content": json.dumps(msg)})
                try:
                    resp = self.client.messages.create(model=self.model, system=sys, messages=msgs, tools=tools, max_tokens=8000)
                except Exception:
                    self._set_status(name, "idle")
                    return
                msgs.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason != "tool_use":
                    break
                results = []
                idle_now = False
                for blk in resp.content:
                    if blk.type == "tool_use":
                        if blk.name == "idle":
                            idle_now = True
                            out = "Entering idle."
                        elif blk.name == "send_message":
                            out = self.bus.send(name, blk.input["to"], blk.input["content"])
                        elif blk.name == "claim_task":
                            out = self.task_mgr.claim(blk.input["task_id"], name)
                        else:
                            h = BASE_HANDLERS.get(blk.name, lambda **kw: "Unknown")
                            out = h(**blk.input)
                        results.append({"type": "tool_result", "tool_use_id": blk.id, "content": str(out)})
                msgs.append({"role": "user", "content": results})
                if idle_now:
                    break
            # 空闲轮询
            self._set_status(name, "idle")
            resume = False
            for _ in range(self.idle_timeout // max(1, self.poll_interval)):
                time.sleep(self.poll_interval)
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        msgs.append({"role": "user", "content": json.dumps(msg)})
                    resume = True
                    break
                unclaimed = self.task_mgr.scan_unclaimed()
                if unclaimed:
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)
                    if len(msgs) <= 3:
                        msgs.insert(0, {"role": "user", "content": f"<identity>You are '{name}', role: {role}, team: {team_name}.</identity>"})
                        msgs.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})
                    msgs.append({"role": "user", "content": f"<auto-claimed>Task #{task['id']}: {task['subject']}"})
                    msgs.append({"role": "assistant", "content": f"Claimed #{task['id']}."})
                    resume = True
                    break
            if not resume:
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")

    def _set_status(self, name: str, status: str):
        m = self._find_member(name)
        if m:
            m["status"] = status
            self._save_config()

    def list_all(self) -> str:
        if not self.team_config["members"]:
            return "No teammates."
        lines = [f"Team: {self.team_config['team_name']}"]
        for m in self.team_config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        return [m["name"] for m in self.team_config["members"]]