"""Finish-tool hook config for automation runs.

Requires the agent to end by calling the finish tool; otherwise the stop hook
denies the stop and the run cannot complete. Mirrors the preset sdk_main.py
finish-tool hook wiring so the same runtime guarantees hold.
"""
import json
import os
from pathlib import Path


def finish_tool_required_hook_config(script_dir: str) -> dict:
    run_id = os.environ.get("AUTOMATION_RUN_ID", "unknown")
    runtime_dir = Path(script_dir) / ".openhands_automation_runtime" / run_id
    marker = runtime_dir / "finish_tool_used"
    py = str(Path(script_dir) / ".venv" / "bin" / "python")

    def sh(code: str) -> str:
        return f"{py} -c {json.dumps(code)}"

    mark_cmd = sh(
        "from pathlib import Path\n"
        f"path = Path({str(marker)!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "path.write_text('finish\\n')\n"
    )
    reset_cmd = sh(
        "from pathlib import Path\n"
        f"path = Path({str(marker)!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "path.unlink(missing_ok=True)\n"
    )
    cleanup_cmd = sh(
        "import shutil\n"
        "from pathlib import Path\n"
        f"shutil.rmtree(Path({str(runtime_dir)!r}), ignore_errors=True)\n"
    )
    stop_cmd = sh(
        "import json\nimport sys\nfrom pathlib import Path\n"
        f"if Path({str(marker)!r}).is_file():\n    sys.exit(0)\n"
        "print(json.dumps({'decision': 'deny', 'reason': 'finish tool was not used', "
        "'additionalContext': 'The task appears complete, but automation runs must end "
        "by calling the finish tool. Please call the finish tool now with the final task outcome.'}))\n"
        "sys.exit(2)\n"
    )

    return {
        "pre_tool_use": [],
        "post_tool_use": [
            {
                "matcher": "/(?:finish|FinishTool)/",
                "hooks": [
                    {
                        "type": "command",
                        "name": "mark-finish-tool-used",
                        "command": mark_cmd,
                        "tools": [],
                        "timeout": 5,
                        "max_iterations": 3,
                        "async_": False,
                    }
                ],
            }
        ],
        "user_prompt_submit": [],
        "session_start": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "name": "reset-finish-tool-marker",
                        "command": reset_cmd,
                        "tools": [],
                        "timeout": 5,
                        "max_iterations": 3,
                        "async_": False,
                    }
                ],
            }
        ],
        "session_end": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "name": "cleanup-finish-tool-marker",
                        "command": cleanup_cmd,
                        "tools": [],
                        "timeout": 5,
                        "max_iterations": 3,
                        "async_": False,
                    }
                ],
            }
        ],
        "stop": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "name": "require-finish-tool",
                        "command": stop_cmd,
                        "tools": [],
                        "timeout": 5,
                        "max_iterations": 3,
                        "async_": False,
                    }
                ],
            }
        ],
    }
