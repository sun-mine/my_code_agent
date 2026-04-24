from src.security.permissions import SecurityArbiter, PermissionMode
arbiter = SecurityArbiter()
import os
import subprocess
import shlex
from langchain_core.tools import tool

@tool
def write_file(filepath: str, content: str) -> str:
    """
    Writes content to a file at the specified filepath using command line (echo / cat).
    Use this tool when you need to create or update a file.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        # 使用命令行工具 cat 和管道写入文件，避免繁琐的转义
        process = subprocess.Popen(
            f"cat > {shlex.quote(filepath)}", 
            shell=True, 
            stdin=subprocess.PIPE, 
            text=True,
            encoding='utf-8'
        )
        process.communicate(input=content)
        if process.returncode == 0:
            return f"Successfully wrote to {filepath} using CLI"
        return f"Command line write failed with code {process.returncode}"
    except Exception as e:
        return f"Failed to CLI write to {filepath}: {str(e)}"

@tool
def read_file(filepath: str) -> str:
    """
    Reads the full contents of a file using 'cat' command.
    """
    try:
        result = subprocess.run(f"cat {shlex.quote(filepath)}", shell=True, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            content = result.stdout
            if len(content) > 5000:
                content = content[:5000] + f"\n\n...[文件内容过长，已被截断，隐藏了 {len(content)-5000} 个字符。如需查看更多请使用 grep 或截取命令]..."
            return content
        return f"CLI read failed: {result.stderr}"
    except Exception as e:
        return f"Failed to CLI read {filepath}: {str(e)}"

@tool
def replace_in_file(filepath: str, old_string: str, new_string: str) -> str:
    """
    Replaces exactly one matching occurrence of a string in an existing file using standard CLI commands.
    """
    try:
        # 通过命令行读取
        read_res = subprocess.run(f"cat {shlex.quote(filepath)}", shell=True, capture_output=True, text=True, encoding='utf-8')
        if read_res.returncode != 0:
            return f"CLI read failed: {read_res.stderr}"
        
        content = read_res.stdout
        if old_string not in content:
            return "Edit failed: 'old_string' not found."
            
        new_content = content.replace(old_string, new_string, 1)
        
        # 通过命令行写入
        process = subprocess.Popen(
            f"cat > {shlex.quote(filepath)}", 
            shell=True, 
            stdin=subprocess.PIPE, 
            text=True,
            encoding='utf-8'
        )
        process.communicate(input=new_content)
        
        if process.returncode == 0:
            return f"Successfully updated {filepath} using CLI."
        return f"CLI update failed with code {process.returncode}"
    except Exception as e:
        return f"Failed to CLI edit {filepath}: {str(e)}"
