CATEGORY = "system"
COMPRESS_TOOL = {
    "name": "compress",
    "description": "Manually compress conversation context.",
    "input_schema": {"type": "object", "properties": {}}
}

def compress_handler(**kw):
    return "Compressing..."

def get_tools():
    return [COMPRESS_TOOL]

def create_handlers(**ctx):
    return {"compress": lambda **kw: compress_handler(**kw)}