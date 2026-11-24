"""``profile/resume.yaml``: the facts the scorer judges jobs against.

Plain text, grouped the way a resume is: skills, experience, projects, education, certifications. Each
experience/project has bullets; every bullet and entry gets a stable id (generated when you leave it out), so the
coding agent can cite *evidence* ("EXP_ACME.B2", "SKILL:Python") for each job requirement it marks as met.

Only write things that are true and that you could defend in an interview. The scorer never invents facts: a
requirement without evidence in this file is "unmet".
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SkillLevel = Literal["hands_on", "working", "exposure"]
EvidenceLevel = Literal["implemented", "contributed", "explored"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Skill(_Model):
    name: str = Field(..., min_length=1)
    level: SkillLevel = "working"
    aliases: list[str] = Field(default_factory=list, description="Other spellings, e.g. 'k8s' for Kubernetes")
    category: str = ""


class Bullet(_Model):
    id: str | None = None
    text: str = Field(..., min_length=3)
    skills: list[str] = Field(default_factory=list)
    evidence: EvidenceLevel = "implemented"
    owner: str | None = Field(None, exclude=True)     # filled in: the entry id this bullet belongs to


class Entry(_Model):
    id: str | None = None
    kind: Literal["job", "project", "education", "cert"] = "job"
    title: str = Field(..., min_length=1, description="Role / project name / degree / certification")
    org: str = ""
    start: str | None = None
    end: str | None = None
    location: str = ""
    bullets: list[Bullet] = Field(default_factory=list)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _str(cls, v):
        return None if v is None else str(v)

    @model_validator(mode="before")
    @classmethod
    def _bullet_strings(cls, v):
        # allow "bullets: [plain string, ...]" as a shortcut
        if isinstance(v, dict) and isinstance(v.get("bullets"), list):
            v = dict(v)
            v["bullets"] = [{"text": b} if isinstance(b, str) else b for b in v["bullets"]]
        return v

    def heading(self) -> str:
        dates = " – ".join(x for x in (self.start, self.end) if x)
        at = f" @ {self.org}" if self.org else ""
        return f"{self.title}{at}" + (f" ({dates})" if dates else "")


class Resume(_Model):
    kind: Literal["resume"] = "resume"
    schema_version: int = 1
    name: str = ""
    headline: str = ""
    summary: str = ""
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Entry] = Field(default_factory=list)
    projects: list[Entry] = Field(default_factory=list)
    education: list[Entry] = Field(default_factory=list)
    certifications: list[Entry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids(self):
        prefix = {"experience": ("EXP", "job"), "projects": ("PRJ", "project"), "education": ("EDU", "education"),
                  "certifications": ("CERT", "cert")}
        for field, (pre, kind) in prefix.items():
            for e in getattr(self, field):
                e.kind = kind
                if not e.id:
                    e.id = f"{pre}_{_slug(e.org or e.title)}"
                for i, b in enumerate(e.bullets, 1):
                    b.id = b.id or f"{e.id}.B{i}"
                    b.owner = e.id
        return self

    # ---- lookups
    @property
    def entries(self) -> list[Entry]:
        return [*self.experience, *self.projects, *self.education, *self.certifications]

    @property
    def bullets(self) -> list[Bullet]:
        return [b for e in self.entries for b in e.bullets]

    def skill_vocab(self) -> dict[str, str]:
        """lower-cased name/alias -> canonical skill name."""
        vocab: dict[str, str] = {}
        for s in self.skills:
            for term in [s.name, *s.aliases]:
                vocab[term.lower()] = s.name
        return vocab


def _slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (s or "X").upper()).strip("_")[:24] or "X"


def resume_path() -> Path:
    from .config import settings
    return settings().profile_dir / "resume.yaml"


@lru_cache(maxsize=1)
def load_resume(path: Path | None = None) -> Resume:
    from .profile import ProfileError
    p = path or resume_path()
    if not p.exists():
        raise ProfileError(f"{p} not found. Run `jobhunt init`, `jobhunt profile form`, or ask your coding agent "
                           f"to follow playbooks/setup.md to draft it from your resume.")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ProfileError(f"{p}: not valid YAML: {e}") from e
    try:
        return Resume.model_validate(data)
    except ValidationError as e:
        lines = ["resume.yaml:"] + [f"  - {'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ProfileError("\n".join(lines)) from e


def validate_resume(r: Resume) -> list[str]:
    """Consistency checks beyond the schema: unique ids, bullet skills exist, something to judge with."""
    errs: list[str] = []
    ids = [e.id for e in r.entries] + [b.id for b in r.bullets]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errs.append(f"resume.yaml: duplicate ids {dup} — give entries an explicit `id:`")
    known = {k for k in r.skill_vocab()}
    for b in r.bullets:
        for s in b.skills:
            if s.lower() not in known:
                errs.append(f"resume.yaml: bullet {b.id} lists skill {s!r} that is not in `skills`")
    names = [s.name.lower() for s in r.skills]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        errs.append(f"resume.yaml: skills listed twice: {dups}")
    if not r.skills:
        errs.append("resume.yaml: `skills` is empty — scoring needs your skills")
    if not r.bullets:
        errs.append("resume.yaml: no experience/project bullets — scoring needs evidence to cite")
    return errs


def dump_resume(r: Resume) -> str:
    return yaml.safe_dump(r.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True, width=110)
