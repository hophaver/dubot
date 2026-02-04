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


def setup_ssh_keys():
    """Set up SSH keys for passwordless login to SITE_UPDATE_HOST."""
    key_file = os.path.expanduser("~/.ssh/id_ed25519")
    if not os.path.exists(key_file):
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key_file, "-N", ""], check=True)
    try:
        subprocess.run(["ssh-copy-id", f"{USER}@{HOST}"], check=True)
        print(f"✓ SSH keys set up for {USER}@{HOST}")
        return True
    except subprocess.CalledProcessError:
        print(f"✗ Could not copy SSH key. Run manually: ssh-copy-id {USER}@{HOST}")
        return False


def run_remote():
    """Run the configured script on the remote host via SSH."""
    cmd = f"ssh {USER}@{HOST} 'cd {DIRECTORY} && sh {SCRIPT}'"
    print(f"Running: {cmd}")
    subprocess.run(cmd, shell=True)


def main():
    if not all([HOST, USER, DIRECTORY, SCRIPT]):
        print("Missing SITE_UPDATE_HOST, SITE_UPDATE_USER, SITE_UPDATE_DIRECTORY or SITE_UPDATE_SCRIPT in .env")
        return
    setup_ssh_keys()
    run_remote()


if __name__ == "__main__":
    main()
