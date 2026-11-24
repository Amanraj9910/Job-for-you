"""Rotating file log in logs/jobhunt.log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ..config import settings

_configured = False


def setup_logging(verbose: bool = False) -> None:
    global _configured
    if _configured:
        return
    s = settings()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("jobhunt")
    root.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(s.logs_dir / "jobhunt.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(sh)
    _configured = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"jobhunt.{name}")
