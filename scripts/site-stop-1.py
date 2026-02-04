#!/usr/bin/env python3
"""Run stop.sh on the remote host. Reads SITE_UPDATE_* from .env."""

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
    """Set up SSH keys for passwordless login. Skipped when using password auth."""
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


def run_remote():
    """Run stop.sh on the remote host."""
    remote_cmd = f"cd {DIRECTORY} && sh {SCRIPT}"
    cmd = _ssh_cmd(remote_cmd)
    print(f"Running: ssh {USER}@{HOST} 'cd {DIRECTORY} && sh {SCRIPT}'")
    result = subprocess.run(cmd, env=_ssh_env())
    return result.returncode


def _have_sshpass():
    try:
        subprocess.run(["which", "sshpass"], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def main():
    if not all([HOST, USER, DIRECTORY, SCRIPT]):
        print("Missing SITE_UPDATE_HOST, SITE_UPDATE_USER, SITE_UPDATE_DIRECTORY or SITE_UPDATE_SCRIPT in .env")
        return
    if PASSWORD and not _have_sshpass():
        print("SITE_UPDATE_SSH_PASSWORD is set but sshpass not found. Install sshpass or use key-based auth.")
        return
    setup_ssh_keys()
    run_remote()


if __name__ == "__main__":
    main()
