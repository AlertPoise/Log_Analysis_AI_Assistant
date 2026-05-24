"""Report writing helpers for UEBA baseline acceptance runs."""

from pathlib import Path
from typing import Any
import json

from .config import AcceptanceConfig


RUN_STATE_FILE = "run_state.json"


def ensure_output_dir(config: AcceptanceConfig) -> Path:
    """Create and return the acceptance output directory."""
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON file using the repository's acceptance-tool format."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_run_state(config: AcceptanceConfig, state: dict[str, Any]) -> None:
    """Write run_state.json under the configured output directory."""
    write_json(ensure_output_dir(config) / RUN_STATE_FILE, state)


def update_run_state(config: AcceptanceConfig, **updates: Any) -> None:
    """Merge updates into run_state.json, creating it when needed."""
    output_dir = ensure_output_dir(config)
    state_path = output_dir / RUN_STATE_FILE
    state = read_json(state_path) if state_path.exists() else {}
    state.update(updates)
    write_json(state_path, state)


__all__ = [
    "ensure_output_dir",
    "read_json",
    "update_run_state",
    "write_json",
    "write_run_state",
]
