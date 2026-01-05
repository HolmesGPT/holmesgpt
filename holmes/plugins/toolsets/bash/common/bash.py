import subprocess

from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.memory_limit import check_oom_and_append_hint, get_ulimit_prefix


def execute_bash_command(cmd: str, timeout: int, params: dict) -> StructuredToolResult:
    try:
        protected_cmd = get_ulimit_prefix() + cmd
        process = subprocess.run(
            protected_cmd,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )

        stdout = process.stdout.strip() if process.stdout else ""
        stdout = check_oom_and_append_hint(stdout, process.returncode)
        result_data = f"{cmd}\n{stdout}"

        if process.returncode == 0:
            status = (
                StructuredToolResultStatus.SUCCESS
                if stdout
                else StructuredToolResultStatus.NO_DATA
            )
            error = None
        else:
            status = StructuredToolResultStatus.ERROR
            error = f'Error: Command "{cmd}" returned non-zero exit status {process.returncode}'

        return StructuredToolResult(
            status=status,
            error=error,
            data=result_data,
            params=params,
            invocation=cmd,
            return_code=process.returncode,
        )
    except subprocess.TimeoutExpired:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Error: Command '{cmd}' timed out after {timeout} seconds.",
            params=params,
        )
    except FileNotFoundError:
        # This might occur if /bin/bash is not found, or command is not found
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error="Error: Bash executable or command not found.",
            params=params,
        )
    except Exception as e:
        return StructuredToolResult(
            status=StructuredToolResultStatus.ERROR,
            error=f"Error executing command '{cmd}': {str(e)}",
            params=params,
        )
