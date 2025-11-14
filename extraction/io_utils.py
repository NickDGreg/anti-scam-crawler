"""Utility helpers for filesystem interactions and run bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import secrets
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


@dataclass(slots=True)
class RunPaths:
    """Convenience container for directories related to a single run."""

    run_id: str
    step_name: str
    base_dir: Path
    step_dir: Path

    def build_path(self, filename: str) -> Path:
        """Return an absolute path inside the step directory."""
        path = self.step_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    return f"{timestamp}-{suffix}"


def prepare_run_directories(run_id: str, step_name: str) -> RunPaths:
    ensure_data_dir()
    base_dir = DATA_DIR / run_id
    base_dir.mkdir(parents=True, exist_ok=True)
    step_dir = base_dir / step_name
    step_dir.mkdir(parents=True, exist_ok=True)
    return RunPaths(run_id=run_id, step_name=step_name, base_dir=base_dir, step_dir=step_dir)


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def save_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def sanitize_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()) or "artifact"
    cleaned = cleaned.strip("-_")
    return cleaned or "artifact"


def relative_artifact_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path.resolve())
