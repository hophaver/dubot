#!/usr/bin/env python3
"""Test SSH connection to SITE_UPDATE_HOST. Does not run stop.sh. Reads SITE_UPDATE_* from .env."""

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
PASSWORD = os.environ.get("SITE_UPDATE_SSH_PASSWORD", "").strip()

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]


def _have_sshpass():
    try:
        subprocess.run(["which", "sshpass"], capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False


def main():
    if not HOST or not USER:
        print("Missing SITE_UPDATE_HOST or SITE_UPDATE_USER in .env")
        return 1
    if PASSWORD and not _have_sshpass():
        print("SITE_UPDATE_SSH_PASSWORD is set but sshpass not found. Install sshpass.")
        return 1
    if PASSWORD:
        env = os.environ.copy()
        env["SSHPASS"] = PASSWORD
        cmd = ["sshpass", "-e", "ssh"] + SSH_OPTS + [f"{USER}@{HOST}", "echo ok"]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
    else:
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [f"{USER}@{HOST}", "echo ok"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    if result.returncode == 0:
        print(f"✓ SSH OK to {USER}@{HOST}")
        return 0
    print(f"✗ SSH failed (exit {result.returncode})")
    if result.stderr:
        print(result.stderr.strip())
    return result.returncode


if __name__ == "__main__":
    exit(main())
