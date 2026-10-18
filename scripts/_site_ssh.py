"""Shared SSH helpers for the site-*.py scripts (not runnable via /run: leading underscore). Reads SITE_UPDATE_* from .env."""

import os
import shutil
import subprocess
import sys
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
SENTINEL_LINE = "Run ./stop.sh to stop."
PREVIEW_LOG = "/tmp/site-preview.log"


def ssh_env():
    """Env dict for subprocess; adds SSHPASS when password is set."""
    env = os.environ.copy()
    if PASSWORD:
        env["SSHPASS"] = PASSWORD
    return env


def ssh_cmd(remote_cmd, extra_opts=()):
    """Build ssh command (with sshpass when password set)."""
    base = ["ssh", *SSH_OPTS, *extra_opts, f"{USER}@{HOST}", remote_cmd]
    return ["sshpass", "-e", *base] if PASSWORD else base


def ensure_auth() -> bool:
    """Make sure we can log in. Key auth: installs a key with ssh-copy-id only when run from a terminal."""
    if PASSWORD:
        return True
    probe = subprocess.run(ssh_cmd("true", ("-o", "BatchMode=yes")), env=ssh_env(), capture_output=True)
    if probe.returncode == 0:
        return True
    if not sys.stdin.isatty():
        print(f"✗ Key-based SSH to {USER}@{HOST} failed. Run once in a terminal: ssh-copy-id {USER}@{HOST}")
        return False
    key_file = os.path.expanduser("~/.ssh/id_ed25519")
    if not os.path.exists(key_file):
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key_file, "-N", ""], check=True)
    try:
        subprocess.run(["ssh-copy-id", *SSH_OPTS, f"{USER}@{HOST}"], check=True)
        print(f"✓ SSH keys set up for {USER}@{HOST}")
        return True
    except subprocess.CalledProcessError:
        print(f"✗ Could not copy SSH key. Run manually: ssh-copy-id {USER}@{HOST}")
        return False


def preflight(*required: str) -> bool:
    """Check required SITE_UPDATE_* settings, sshpass availability, and SSH auth."""
    values = {"SITE_UPDATE_HOST": HOST, "SITE_UPDATE_USER": USER, "SITE_UPDATE_DIRECTORY": DIRECTORY}
    values.update({k: os.environ.get(k, "").strip() for k in required})
    missing = [k for k, v in values.items() if not v]
    if missing:
        print(f"Missing {', '.join(missing)} in .env")
        return False
    if PASSWORD and not shutil.which("sshpass"):
        print("SITE_UPDATE_SSH_PASSWORD is set but sshpass not found. Install sshpass or use key-based auth.")
        return False
    return ensure_auth()


def stream_until_sentinel(remote_cmd: str) -> int:
    """Run remote_cmd, echo its output, and disconnect once SENTINEL_LINE appears."""
    print(f"Running: ssh {USER}@{HOST} '...'")
    proc = subprocess.Popen(
        ssh_cmd(remote_cmd), env=ssh_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    seen = False
    try:
        for line in proc.stdout:
            print(line.rstrip(), flush=True)
            if SENTINEL_LINE in line:
                seen = True
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0 if seen else (proc.returncode or 1)
