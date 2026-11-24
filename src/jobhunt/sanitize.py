"""Turn untrusted job-description HTML into plain text and neutralize instruction-like lines."""
from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup

# Lines in a JD that try to talk to an AI agent. We keep job text, drop these.
_INJECTION = re.compile(
    r"(ignore (all|any|the|previous|prior) (instructions|prompts)|disregard (the|all|previous)|"
    r"you are (now )?(an?|the) (ai|assistant|language model|llm)|system prompt|as an ai\b|"
    r"\bchatgpt\b|\bclaude\b|\bllm\b.*\b(must|should)\b|if you are an? (ai|bot|language model)|"
    r"<\s*/?\s*(system|assistant|user)\s*>|include the word .* in your (application|answer))",
    re.I)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw
    if "&lt;" in s and "<" not in s:  # double-encoded HTML (Greenhouse content)
        s = html.unescape(s)
    soup = BeautifulSoup(s, "html.parser")
    for t in soup(["script", "style", "noscript", "iframe", "svg"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for blk in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"]):
        blk.insert_after("\n")
    text = html.unescape(soup.get_text())
    return clean_text(text)


def clean_text(text: str) -> str:
    text = text.replace("\r", "\n").replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


def strip_injection(text: str) -> tuple[str, list[str]]:
    """Remove lines that look like prompt injection. Returns (clean_text, removed_lines)."""
    kept, removed = [], []
    for ln in text.split("\n"):
        if _INJECTION.search(ln):
            removed.append(ln)
        else:
            kept.append(ln)
    return "\n".join(kept), removed


def sanitize_jd(raw: str | None, is_html: bool = True) -> str:
    text = html_to_text(raw) if is_html else clean_text(raw or "")
    text, _ = strip_injection(text)
    return text[:60000]
