"""Uniform CLI output: JSON for coding agents, readable text for humans."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import typer


def _default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=_default)


def emit(obj, as_json: bool, text: str | None = None) -> None:
    if as_json:
        sys.stdout.write(dumps(obj) + "\n")
    else:
        typer.echo(text if text is not None else dumps(obj))


def fail(msg: str, code: int = 1, as_json: bool = False) -> None:
    if as_json:
        sys.stdout.write(dumps({"ok": False, "error": msg}) + "\n")
    else:
        typer.echo(f"ERROR: {msg}", err=True)
    raise typer.Exit(code)
