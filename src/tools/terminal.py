import subprocess
from langchain_core.tools import tool

@tool
def execute_command(command: str) -> str:
    """
    Executes a shell command in the terminal and returns its output (stdout and stderr).
    Use this tool when you need to run code, test scripts, or execute any terminal command.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=120  # Limit execution time to avoid hanging
        )
        
        output = result.stdout
        if result.stderr:
            output += "\n--- STDERR ---\n" + result.stderr
            
        if not output.strip():
            output = f"Command executed successfully with exit code {result.returncode} (No output)."
            
        return output
    except subprocess.TimeoutExpired:
        return "Command execution timed out after 120 seconds."
    except Exception as e:
        return f"Failed to execute command: {str(e)}"
