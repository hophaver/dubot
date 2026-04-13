"""Install missing optional dependencies at startup (e.g. news RSS)."""

import subprocess
import sys


def ensure_news_dependencies() -> None:
    """If feedparser or beautifulsoup4 are missing, pip install them (matches requirements.txt)."""
    to_install = []
    try:
        import feedparser  # noqa: F401
    except ImportError:
        to_install.append("feedparser>=6.0.0")
    try:
        import bs4  # noqa: F401
    except ImportError:
        to_install.append("beautifulsoup4>=4.12.0")
    if not to_install:
        return
    print(f"Installing missing packages: {', '.join(to_install)}", flush=True)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *to_install],
        )
    except (subprocess.CalledProcessError, OSError) as e:
        print(
            f"⚠️ Could not auto-install news dependencies ({e}). "
            f"Run: pip install {' '.join(to_install)}",
            flush=True,
        )
