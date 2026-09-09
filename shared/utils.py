"""File I/O, date and text helpers."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def save_json(data, filepath: Path) -> None:
    """
    Write JSON atomically.

    The state file records which postings have already been announced. A run
    killed mid-write left truncated JSON, which reset the state and would have
    re-announced everything on the next run.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(filepath.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filepath)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_json(filepath: Path, default=None):
    filepath = Path(filepath)
    if not filepath.exists():
        return {} if default is None else default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Could not read {filepath}: {e}")
        return {} if default is None else default


def get_today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for fingerprinting."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
