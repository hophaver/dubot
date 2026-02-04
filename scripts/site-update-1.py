#!/usr/bin/env python3
"""SSH key setup and remote script run for site update. Reads SITE_UPDATE_* from .env."""

import os
import subprocess
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

HOST = os.environ.get("SITE_UPDATE_HOST", "").strip()
USER = os.environ.get("SITE_UPDATE_USER", "").strip()
DIRECTORY = os.environ.get("SITE_UPDATE_DIRECTORY", "").strip()
SCRIPT = os.environ.get("SITE_UPDATE_SCRIPT", "").strip()
SCRIPT_PREVIEW = os.environ.get("SITE_UPDATE_SCRIPT_PREVIEW", "preview.sh").strip() or "preview.sh"
PASSWORD = os.environ.get("SITE_UPDATE_SSH_PASSWORD", "").strip()

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]


def _ssh_env():
    """Env dict for subprocess; adds SSHPASS when password is set."""
    env = os.environ.copy()
    if PASSWORD:
        env["SSHPASS"] = PASSWORD
    return env


def _ssh_cmd(remote_cmd):
    """Build ssh command (with sshpass when password set)."""
    base = ["ssh"] + SSH_OPTS + [f"{USER}@{HOST}", remote_cmd]
    if PASSWORD:
        return ["sshpass", "-e"] + base
    return base


def setup_ssh_keys():
    """Set up SSH keys for passwordless login to SITE_UPDATE_HOST. Skipped when using password auth."""
    if PASSWORD:
        return True
    key_file = os.path.expanduser("~/.ssh/id_ed25519")
    if not os.path.exists(key_file):
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key_file, "-N", ""], check=True)
    try:
        subprocess.run(["ssh-copy-id"] + SSH_OPTS + [f"{USER}@{HOST}"], check=True)
        print(f"✓ SSH keys set up for {USER}@{HOST}")
        return True
    except subprocess.CalledProcessError:
        print(f"✗ Could not copy SSH key. Run manually: ssh-copy-id {USER}@{HOST}")
        return False


SENTINEL_LINE = "Run ./stop.sh to stop."
PREVIEW_LOG = "/tmp/site-preview.log"


def run_remote():
    """Run SCRIPT, git pull, then SCRIPT_PREVIEW in background; stream output and close SSH when sentinel seen."""
    # Start preview in background with nohup so it survives SSH disconnect; stream via tail
    remote_cmd = (
        f"cd {DIRECTORY} && sh {SCRIPT} && git pull && "
        f"(nohup sh {SCRIPT_PREVIEW} > {PREVIEW_LOG} 2>&1 &) && sleep 2 && tail -f {PREVIEW_LOG}"
    )
    cmd = _ssh_cmd(remote_cmd)
    print(f"Running: ssh {USER}@{HOST} '...'")
    proc = subprocess.Popen(
        cmd,
        env=_ssh_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            print(line, flush=True)
            if SENTINEL_LINE in line:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0 if SENTINEL_LINE in "".join(lines) else (proc.returncode or 1)


def main():
    if not all([HOST, USER, DIRECTORY, SCRIPT]):
        print("Missing SITE_UPDATE_HOST, SITE_UPDATE_USER, SITE_UPDATE_DIRECTORY or SITE_UPDATE_SCRIPT in .env")
        return
    if PASSWORD and not _have_sshpass():
        print("SITE_UPDATE_SSH_PASSWORD is set but sshpass not found. Install sshpass or use key-based auth.")
        return
    setup_ssh_keys()
    run_remote()


def _have_sshpass():
    try:
        subprocess.run(["which", "sshpass"], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


if __name__ == "__main__":
    main()
