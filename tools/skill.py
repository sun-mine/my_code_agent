CATEGORY = "system"
SKILL_TOOL = {
    "name": "load_skill",
    "description": "Load specialized knowledge by name.",
    "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
}

def skill_handler(skills_loader, **kw):
    return skills_loader.load(kw["name"])

def get_tools():
    return [SKILL_TOOL]

def create_handlers(**ctx):
    loader = ctx.get("skill_loader")
    return {"load_skill": lambda **kw: skill_handler(loader, **kw)}