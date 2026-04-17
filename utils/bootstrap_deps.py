"""Install missing dependencies at startup (core + optional)."""

import subprocess
import sys


def _install_if_missing(import_name: str, package_spec: str) -> bool:
    try:
        __import__(import_name)
        return True
    except ImportError:
        pass
    print(f"Installing missing package: {package_spec}", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_spec])
        return True
    except (subprocess.CalledProcessError, OSError) as e:
        print(
            f"⚠️ Could not auto-install dependency `{package_spec}` ({e}). "
            f"Run: {sys.executable} -m pip install {package_spec}",
            flush=True,
        )
        return False


def ensure_discord_dependencies() -> None:
    """Ensure core Discord runtime dependency exists before importing discord."""
    _install_if_missing("discord", "discord.py>=2.3.0")


def ensure_trader_dependencies() -> None:
    """httpx (Trader API) and aiohttp (optional inbound webhook)."""
    _install_if_missing("httpx", "httpx>=0.27.0")
    _install_if_missing("aiohttp", "aiohttp>=3.9.0")


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
