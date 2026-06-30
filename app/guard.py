"""Content safety guard for generated captions and prompts.

Ported and adapted from the X-growth `publish_guard.py`: regex checks that block
leaked secrets, inflammatory, and spammy content before a caption is shown or a
prompt is sent to a provider. Returns a frozen result for clear pass/fail
semantics.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|auth)[=:]\s*\S{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]

_INFLAMMATORY_PATTERNS = [
    re.compile(r"(?i)\b(hate|kill|die|racist|suicide)\b"),
    re.compile(r"(炎上|死ね|殺|差別|自殺)"),
]

_SALESY_PATTERNS = [
    re.compile(r"(?i)\b(buy now|act fast|limited offer|dm me|follow for follow|f4f|l4l)\b"),
    re.compile(r"(今すぐ購入|今だけ|相互フォロー|フォロバ)"),
]

# Per-platform caption length ceilings. Default applies when platform is unknown.
_PLATFORM_MAX_LENGTH = {
    "x": 280,
    "instagram": 2200,
    "threads": 500,
    "linkedin": 3000,
    "default": 2200,
}
_MIN_LENGTH = 10


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str


def _scan(text: str) -> GuardResult | None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return GuardResult(allowed=False, reason="secret_pattern_detected")
    for pattern in _INFLAMMATORY_PATTERNS:
        if pattern.search(text):
            return GuardResult(allowed=False, reason="inflammatory_content")
    for pattern in _SALESY_PATTERNS:
        if pattern.search(text):
            return GuardResult(allowed=False, reason="salesy_spam")
    return None


def check_caption(text: str, platform: str = "default") -> GuardResult:
    """Validate a caption for length and unsafe content."""
    max_length = _PLATFORM_MAX_LENGTH.get(platform.lower(), _PLATFORM_MAX_LENGTH["default"])
    if len(text) < _MIN_LENGTH:
        return GuardResult(allowed=False, reason=f"too_short ({len(text)} chars)")
    if len(text) > max_length:
        return GuardResult(allowed=False, reason=f"too_long ({len(text)}/{max_length} chars)")
    violation = _scan(text)
    return violation or GuardResult(allowed=True, reason="ok")


def check_prompt(text: str) -> GuardResult:
    """Validate a generation prompt — block accidental secret leakage into providers."""
    if not text.strip():
        return GuardResult(allowed=False, reason="empty_prompt")
    violation = _scan(text)
    return violation or GuardResult(allowed=True, reason="ok")
