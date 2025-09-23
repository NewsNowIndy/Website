import subprocess, shlex, logging
from typing import Optional

log = logging.getLogger(__name__)

def send_signal_group(message: str, sender: str, group_id: str, cli_bin: str, timeout: int = 25, config_dir: str | None = None) -> tuple[int, str, str]:
    """
    Send a message to a Signal group via signal-cli.
    Returns (returncode, stdout, stderr). Logs on failure.
    """
    if not (message and sender and group_id and cli_bin):
        log.error("Signal: missing required params (have sender=%r, group_id=%r, cli_bin=%r)", bool(sender), bool(group_id), bool(cli_bin))
        return (2, "", "missing params")

    cmd = [cli_bin]
    if config_dir:
        cmd += ["--config", config_dir]
    cmd += ["-u", sender, "send", "-g", group_id, "-m", message]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            log.error("Signal send failed rc=%s\ncmd=%s\nstdout=%s\nstderr=%s",
                      res.returncode, " ".join(shlex.quote(c) for c in cmd), res.stdout.strip(), res.stderr.strip())
        else:
            log.info("Signal sent OK: %s", message[:120].replace("\n"," "))
        return (res.returncode, res.stdout, res.stderr)
    except FileNotFoundError:
        log.exception("Signal CLI not found at %r", cli_bin)
        return (127, "", f"signal-cli not found: {cli_bin}")
    except subprocess.TimeoutExpired:
        log.exception("Signal CLI timed out")
        return (124, "", "timeout")
    except Exception as e:
        log.exception("Signal CLI unexpected error: %s", e)
        return (1, "", str(e))
