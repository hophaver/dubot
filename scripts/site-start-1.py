#!/usr/bin/env python3
"""Start the preview script on the remote host in background; stream output until it reports ready."""
import sys

from _site_ssh import DIRECTORY, PREVIEW_LOG, SCRIPT_PREVIEW, preflight, stream_until_sentinel


def main() -> int:
    if not preflight():
        return 1
    return stream_until_sentinel(
        f"cd {DIRECTORY} && (nohup sh {SCRIPT_PREVIEW} > {PREVIEW_LOG} 2>&1 &) && sleep 2 && tail -f {PREVIEW_LOG}"
    )


if __name__ == "__main__":
    sys.exit(main())
