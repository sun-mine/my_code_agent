import subprocess
import os
import time
import uuid
import tempfile
from typing import Optional, Dict, Any
from langchain_core.tools import tool

# Global dictionary to keep track of background processes
_BACKGROUND_PROCESSES: Dict[str, Dict[str, Any]] = {}

@tool
def execute_command(command: str, timeout: int = 120, run_in_background: bool = False) -> str:
    """
    Executes a shell command in the terminal.
    - If run_in_background is False (default): it blocks and waits for the output up to `timeout` seconds.
    - If run_in_background is True: it starts the command asynchronously, writes output to a temp file, and returns a job_id immediately.
      You can then use the `get_background_status` tool to check on its progress and output.
    Use this tool when you need to run code, test scripts, or execute any terminal command.
    """
    if run_in_background:
        job_id = str(uuid.uuid4())[:8]
        out_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{job_id}.out")
        
        try:
            # Start process in background
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=out_file,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid  # To allow killing process groups if needed
            )
            
            _BACKGROUND_PROCESSES[job_id] = {
                "process": process,
                "out_file_path": out_file.name,
                "command": command,
                "start_time": time.time()
            }
            return f"Background job started successfully with job_id: '{job_id}'. Output is being written to {out_file.name}. Use `get_background_status` tool to check on it."
            
        except Exception as e:
            return f"Failed to start background command: {str(e)}"

    # Synchronous execution
    try:
        result = subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout
        )
        
        output = result.stdout
        if result.stderr:
            output += "\n--- STDERR ---\n" + result.stderr
            
        if not output.strip():
            output = f"Command executed successfully with exit code {result.returncode} (No output)."
            
        return output
    except subprocess.TimeoutExpired:
        return f"Command execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Failed to execute command: {str(e)}"

@tool
def get_background_status(job_id: str, kill: bool = False) -> str:
    """
    Checks the status and reads the latest output of a background command started by `execute_command(..., run_in_background=True)`.
    If `kill` is True, it will forcefully terminate the background process.
    """
    if job_id not in _BACKGROUND_PROCESSES:
        return f"Error: No active or recent background job found with job_id '{job_id}'."
        
    job_info = _BACKGROUND_PROCESSES[job_id]
    process: subprocess.Popen = job_info["process"]
    out_file_path: str = job_info["out_file_path"]
    
    if kill:
        if process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), 9)
                return f"Job '{job_id}' has been forcefully killed."
            except Exception as e:
                return f"Attempted to kill job '{job_id}' but encountered an error: {str(e)}"
        return f"Job '{job_id}' is already finished, cannot kill."
        
    status = process.poll()
    status_str = "RUNNING" if status is None else f"FINISHED (exit code: {status})"
    runtime = time.time() - job_info["start_time"]
    
    # Read output
    try:
        with open(out_file_path, "r") as f:
            output = f.read()
            if not output.strip():
                output = "(No output yet)"
            elif len(output) > 20000:
                output = "...[Trunacted]...\n" + output[-20000:] # Return last 20k chars
    except Exception as e:
        output = f"(Error reading output file: {str(e)})"
        
    report = f"Job ID: {job_id}\nStatus: {status_str}\nRuntime: {runtime:.1f}s\nCommand: {job_info['command']}\n\n--- LATEST OUTPUT ---\n{output}"
    return report
