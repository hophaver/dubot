"""Temporary files for adaptive DM image+text pipeline (draft → prompt → final)."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Optional

# Dedicated directory under data/ (created on demand).
TEMP_ROOT = Path("data") / "temp_dm_image_flow"

DRAFT_NAME = "draft.txt"
PROMPT_NAME = "image_prompt.txt"
FINAL_NAME = "final.txt"


def ensure_temp_root() -> Path:
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return TEMP_ROOT


def create_session_dir() -> Path:
    ensure_temp_root()
    sid = f"{uuid.uuid4().hex}"
    d = TEMP_ROOT / sid
    d.mkdir(parents=False, exist_ok=False)
    return d


def _write_sync(path: Path, text: str) -> None:
    path.write_text(text or "", encoding="utf-8")


def _read_sync(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _unlink_tree_sync(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def clear_all_temp_sessions_sync() -> int:
    """Remove all session subdirs under TEMP_ROOT. Returns count removed."""
    ensure_temp_root()
    n = 0
    for child in list(TEMP_ROOT.iterdir()):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            n += 1
        elif child.is_file():
            try:
                if child.exists():
                    child.unlink()
            except OSError:
                pass
            n += 1
    return n


async def write_text(path: Path, text: str) -> None:
    await asyncio.to_thread(_write_sync, path, text)


async def read_text(path: Path) -> str:
    return await asyncio.to_thread(_read_sync, path)


async def remove_session_dir(path: Optional[Path]) -> None:
    if path is None or not path.is_dir():
        return
    await asyncio.to_thread(_unlink_tree_sync, path)


async def clear_all_temp_sessions() -> int:
    return await asyncio.to_thread(clear_all_temp_sessions_sync)
