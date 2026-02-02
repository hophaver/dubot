"""Check, start, and stop Ollama server (used by main startup and /ollama-on, /ollama-off)."""
import sys
import subprocess
import urllib.request
from typing import Tuple


def _get_base_url() -> str:
    from integrations import OLLAMA_URL
    return (OLLAMA_URL or "http://localhost:11434").rstrip("/")


def check_ollama_running() -> bool:
    """Return True if Ollama API responds."""
    try:
        urllib.request.urlopen(f"{_get_base_url()}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def start_ollama() -> Tuple[bool, str]:
    """Start ollama serve in background. Return (success, message)."""
    if check_ollama_running():
        return True, "Ollama is already running."
    try:
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            ["ollama", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        return True, "Ollama started in background."
    except FileNotFoundError:
        return False, "Ollama not found (install ollama and ensure it's on PATH)."
    except Exception as e:
        return False, str(e)[:200]


def stop_ollama() -> Tuple[bool, str]:
    """Stop Ollama server. Return (success, message)."""
    if not check_ollama_running():
        return True, "Ollama was not running."
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/IM", "ollama.exe", "/F"], capture_output=True, timeout=10)
        else:
            subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True, timeout=5)
            subprocess.run(["pkill", "-x", "ollama"], capture_output=True, timeout=5)
        return True, "Ollama stopped."
    except Exception as e:
        return False, str(e)[:200]
