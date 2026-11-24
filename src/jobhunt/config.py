"""Paths, settings and environment loading."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _find_root() -> Path:
    env = os.environ.get("JOBHUNT_HOME")
    if env:
        return Path(env).resolve()
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "pyproject.toml").exists() and (p / "src" / "jobhunt").exists():
            return p
    # fall back to the source checkout this package lives in
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path = field(init=False)
    profile_dir: Path = field(init=False)
    example_profile_dir: Path = field(init=False)
    db_path: Path = field(init=False)
    output_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        r = self.root
        s = object.__setattr__
        s(self, "data_dir", r / "data")
        s(self, "profile_dir", Path(os.environ.get("JOBHUNT_PROFILE", r / "profile")))
        s(self, "example_profile_dir", r / "profile.example")
        s(self, "db_path", Path(os.environ.get("JOBHUNT_DB", r / "data" / "jobhunt.db")))
        s(self, "output_dir", Path(os.environ.get("JOBHUNT_OUTPUT", r / "output")))
        s(self, "logs_dir", r / "logs")

    @property
    def report_path(self) -> Path:
        return self.output_dir / "jobs.html"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.profile_dir, self.output_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def settings() -> Settings:
    root = _find_root()
    load_dotenv(root / ".env", override=False)
    return Settings(root=root)


def env(name: str, default: str | None = None) -> str | None:
    settings()  # make sure .env is loaded
    return os.environ.get(name, default)
