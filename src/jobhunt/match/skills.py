"""Skill extraction from job text using your skill inventory (resume.yaml names + aliases), and title similarity."""
from __future__ import annotations

import re
from functools import lru_cache

from rapidfuzz import fuzz

from ..resume import Resume, load_resume

# Common tech skills reported as gaps when a JD asks for them and your resume lacks them (not exhaustive;
# the agent judges the rest). Contributions for other fields welcome.
GAP_VOCAB = [
    "Java", "Go", "Golang", "Rust", "Scala", "Kotlin", "Swift", "C#", ".NET", "Ruby", "PHP", "Spark", "Hadoop",
    "Kafka", "Airflow", "dbt", "Snowflake", "Databricks", "BigQuery", "Kubernetes", "Terraform", "AWS",
    "SageMaker", "Bedrock", "Vertex AI", "PyTorch", "TensorFlow", "JAX", "Hugging Face", "LangChain",
    "LlamaIndex", "LangGraph", "CrewAI", "AutoGen", "Semantic Kernel", "MLflow", "Kubeflow", "CUDA", "Triton",
    "vLLM", "TensorRT", "ONNX", "Elasticsearch", "MongoDB", "MySQL", "DynamoDB", "Cassandra", "GraphQL",
    "Angular", "Vue", "Flutter", "React Native", "Android", "iOS", "Salesforce", "SAP", "ServiceNow",
    "Reinforcement learning", "Recommender systems", "Time series", "Statistics", "PhD", "NLP", "spaCy",
    "Pandas", "NumPy", "scikit-learn", "Django", "Flask", "Spring Boot", "Microservices", "Linux",
]

_SHORT = 3  # tokens this short only match exactly as whole words (Go, C, R, ML, CV, TS)


def _rx(term: str) -> re.Pattern:
    t = re.escape(term)
    return re.compile(rf"(?<![A-Za-z0-9+#.]){t}(?![A-Za-z0-9+#])", 0 if len(term) <= _SHORT else re.I)


@lru_cache(maxsize=4)
def _matchers(master_id: int) -> list[tuple[str, re.Pattern]]:
    m = _MASTER_CACHE[master_id]
    out = []
    for s in m.skills:
        for term in [s.name, *s.aliases]:
            if len(term) < 2:
                continue
            out.append((s.name, _rx(term)))
    return out


_MASTER_CACHE: dict[int, Resume] = {}


def _master(m: Resume | None) -> Resume:
    m = m or load_resume()
    _MASTER_CACHE[id(m)] = m
    return m


def extract_skills(text: str, master: Resume | None = None) -> dict[str, list[str]]:
    """-> {'have': [canonical skills found in text], 'gaps': [gap-vocab terms found that you lack]}"""
    m = _master(master)
    have: dict[str, None] = {}
    for name, rx in _matchers(id(m)):
        if name not in have and rx.search(text):
            have[name] = None
    gaps = [g for g in GAP_VOCAB if _rx(g).search(text)]
    vocab = m.skill_vocab()
    gaps = [g for g in gaps if g.lower() not in vocab]
    return {"have": list(have), "gaps": gaps}


def _words_rx(words: list[str]) -> re.Pattern | None:
    words = [w.strip() for w in words or [] if w and w.strip()]
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")\b", re.I)


@lru_cache(maxsize=8)
def _keyword_rx(strong: tuple, related: tuple, roles: tuple) -> tuple:
    return _words_rx(list(strong)), _words_rx(list(related)), _words_rx(list(roles))


def _profile_keywords() -> tuple:
    try:
        from ..profile import title_keywords
        tk = title_keywords()
        return _keyword_rx(tuple(tk.get("strong") or ()), tuple(tk.get("related") or ()),
                           tuple(tk.get("role_words") or ()))
    except Exception:  # noqa: BLE001 — no profile: fuzzy title match only
        return None, None, None


def title_similarity(title: str, targets: list[str]) -> float:
    """0..100: how well a job title fits your target titles (profile keyword tiers + fuzzy exact match)."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", title or "")
    t = re.sub(r"\b(sr\.?|senior|jr\.?|junior|i{1,3}|iv|l\d)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return 0.0
    full = f"{title}"
    exact = max((fuzz.token_sort_ratio(t.lower(), x.lower()) for x in targets), default=0.0)
    if exact >= 90:
        return 100.0
    strong, medium, role = _profile_keywords()
    has_role = bool(role and role.search(full))
    if strong and strong.search(full):
        base = 85.0 if has_role else 75.0
    elif medium and medium.search(full) and has_role:
        base = 60.0
    elif has_role:
        base = 40.0
    else:
        base = 15.0
    return max(base, min(exact, 89.0) if exact >= 80 else base)
