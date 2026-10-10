"""Short-lived Codex hook: select the active transcript and launch one sidecar."""

import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if os.name != "nt":
        return 0

    plugin_root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1]))
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    data_dir = codex_home / "metrics-bar"
    data_dir.mkdir(parents=True, exist_ok=True)
    pointer = data_dir / "active.json"
    temp = data_dir / f"active.{os.getpid()}.tmp"
    selected = {
        "session_id": event.get("session_id"),
        "transcript_path": event.get("transcript_path"),
    }
    temp.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, pointer)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        task = subprocess.run(
            ["schtasks.exe", "/Run", "/TN", "Codex Metrics Bar"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            timeout=3,
            check=False,
        )
        if task.returncode == 0:
            return 0
    except (OSError, subprocess.TimeoutExpired):
        pass

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = str(pythonw if pythonw.is_file() else sys.executable)
    subprocess.Popen(
        [interpreter, str(plugin_root / "overlay.py"), "--data-dir", str(data_dir)],
        cwd=str(plugin_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
