import os
from pathlib import Path

def load_prompt(prompt_name: str) -> str:
    """
    加载指定名称的 Prompt 文件内容
    
    Args:
        prompt_name (str): Prompt文件名称（不带后缀，默认读取 .md）
        
    Returns:
        str: Prompt 内容
    """
    # 假设该文件存放在 src/utils 下，相对路径计算项目根目录下的 prompts 文件夹
    base_dir = Path(__file__).resolve().parent.parent.parent
    prompt_path = base_dir / "prompts" / f"{prompt_name}.md"
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt '{prompt_name}' 对应的文件不存在: {prompt_path}")
        
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read().strip()
