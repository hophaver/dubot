#!/usr/bin/env python3
"""Run SITE_UPDATE_SCRIPT, git pull, then start the preview script; stream output until it reports ready."""
import sys

from _site_ssh import DIRECTORY, PREVIEW_LOG, SCRIPT, SCRIPT_PREVIEW, preflight, stream_until_sentinel


def main() -> int:
    if not preflight("SITE_UPDATE_SCRIPT"):
        return 1
    # Start preview with nohup so it survives the SSH disconnect; stream via tail.
    return stream_until_sentinel(
        f"cd {DIRECTORY} && sh {SCRIPT} && git pull && "
        f"(nohup sh {SCRIPT_PREVIEW} > {PREVIEW_LOG} 2>&1 &) && sleep 2 && tail -f {PREVIEW_LOG}"
    )


if __name__ == "__main__":
    sys.exit(main())
