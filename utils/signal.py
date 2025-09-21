import subprocess
from typing import Optional
def send_signal_group(message: str, sender: str, group: str, signal_cli_bin: str = "signal-cli") -> Optional[int]:
    if not sender or not group:
        return None
    try:
        cmd = [signal_cli_bin, "-u", sender, "send", "-g", group, message]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).returncode
    except Exception:
        return None
