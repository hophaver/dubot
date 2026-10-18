"""Project paths anchored to the repo root (independent of the process cwd)."""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")


def root_path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def migrate_root_file(name: str) -> str:
    """Return data/<name>; move a legacy <root>/<name> there once if present."""
    new = data_path(name)
    old = root_path(name)
    if os.path.exists(old) and not os.path.exists(new):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.move(old, new)
        print(f"📦 Moved {name} -> data/{name}")
    return new
