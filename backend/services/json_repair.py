"""Best-effort recovery of JSON from a model reply.

Order of operations for every structured call (requirement #12):

  1. try to parse / repair whatever came back;
  2. if that fails, re-ask the model with an explicit "return valid JSON only"
     instruction (handled in `agents/base.py`);
  3. if that also fails, the agent is marked INVALID_OUTPUT and the debate
     continues without it for this round.

This module implements step 1 only.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_JS_COMMENT_RE = re.compile(r"^\s*//.*$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text


_CLOSERS = {"{": "}", "[": "]"}


def _balanced_slice(text: str) -> str | None:
    """Return the first syntactically balanced {...} block, string-aware.

    If the model's reply was truncated mid-object, close the open brackets and
    strings so the fragment can still be parsed.
    """
    start = text.find("{")
    if start == -1:
        return None
    stack: list[str] = []
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in _CLOSERS:
            stack.append(ch)
        elif ch in ("}", "]"):
            if stack and _CLOSERS[stack[-1]] == ch:
                stack.pop()
                if not stack:
                    return text[start : i + 1]
            else:
                break  # unbalanced closer — fall through to repair below

    candidate = text[start:]
    if in_str:
        candidate += '"'
    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate.rstrip().rstrip(","))
    for opener in reversed(stack):
        candidate += _CLOSERS[opener]
    return candidate


def _light_repairs(chunk: str) -> str:
    chunk = _JS_COMMENT_RE.sub("", chunk)
    chunk = _TRAILING_COMMA_RE.sub(r"\1", chunk)
    # Smart quotes → straight quotes (only outside of obvious content is hard;
    # this is a last-ditch pass and only runs when strict parsing failed).
    chunk = chunk.replace("“", '"').replace("”", '"')
    return chunk


def extract_json(text: str) -> dict[str, Any] | None:
    """Return the first JSON object found in `text`, or None."""
    if not text or not text.strip():
        return None

    candidates: list[str] = []
    raw = text.strip()
    candidates.append(raw)

    fenced = _strip_fences(raw).strip()
    if fenced and fenced != raw:
        candidates.append(fenced)

    for base in list(candidates):
        sliced = _balanced_slice(base)
        if sliced:
            candidates.append(sliced)

    for cand in list(candidates):
        candidates.append(_light_repairs(cand))

    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
    return None
