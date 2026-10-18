#!/usr/bin/env python3
"""Run SITE_UPDATE_SCRIPT (stop script) on the remote host."""
import subprocess
import sys

from _site_ssh import DIRECTORY, HOST, SCRIPT, USER, preflight, ssh_cmd, ssh_env


def main() -> int:
    if not preflight("SITE_UPDATE_SCRIPT"):
        return 1
    print(f"Running: ssh {USER}@{HOST} 'cd {DIRECTORY} && sh {SCRIPT}'")
    return subprocess.run(ssh_cmd(f"cd {DIRECTORY} && sh {SCRIPT}"), env=ssh_env()).returncode


if __name__ == "__main__":
    sys.exit(main())
