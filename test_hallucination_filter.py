#!/usr/bin/env python3
"""Unit tests for the hallucination/energy guards (no model load)."""
import re
import numpy as np

SPEECH_PEAK_FLOOR = 0.01

_HALLUCINATION_PHRASES = [
    "продолжение следует",
    "продолжение в следующей серии",
    "субтитры сделал",
    "субтитры создавал",
    "субтитры добавил",
    "субтитры подготовил",
    "редактор субтитров",
    "корректор",
    "dimatorzok",
    "amara.org",
    "спасибо за просмотр",
    "спасибо за внимание",
    "подписывайтесь на канал",
    "ставьте лайки",
]
_NORM_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    return _NORM_RE.sub(" ", text.lower()).strip()


def is_hallucination_phrase(text: str) -> bool:
    """True for known Whisper silence artifacts that dominate a short output."""
    norm = _normalize(text)
    if not norm:
        return False
    if len(norm.split()) > 6:
        return False
    return any(p in norm for p in _HALLUCINATION_PHRASES)


def has_real_speech(audio: np.ndarray) -> bool:
    if len(audio) == 0:
        return False
    return float(np.max(np.abs(audio))) >= SPEECH_PEAK_FLOOR


# ── tests ────────────────────────────────────────────────────────────
def _check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {name}")
    assert cond, name


# phrase filter — known artifacts caught
_check("продолжение следует", is_hallucination_phrase("Продолжение следует..."))
_check("субтитры DimaTorzok", is_hallucination_phrase("Субтитры сделал DimaTorzok"))
_check("спасибо за просмотр", is_hallucination_phrase("Спасибо за просмотр!"))

# phrase filter — real dictation NOT caught
_check("real sentence 1", not is_hallucination_phrase(
    "надо этот айфрейм сделать выше пусть займет всю высоту блока"))
_check("real sentence 2", not is_hallucination_phrase(
    "электронной коммерции и ее автоматизации"))
_check("real long with subtitle word", not is_hallucination_phrase(
    "субтитры это отдельная подсистема которую надо вынести в свой модуль"))

# energy gate — bug-like near silence rejected, real speech accepted
rng = np.random.default_rng(0)
bug = (rng.standard_normal(16000 * 25) * 0.0005).astype(np.float32)  # peak ~0.0015
_check(f"bug near-silence rejected (peak={np.max(np.abs(bug)):.4f})", not has_real_speech(bug))
_check("pure silence rejected", not has_real_speech(np.zeros(16000, dtype=np.float32)))
speech = (rng.standard_normal(16000) * 0.05).astype(np.float32)      # peak ~0.2
_check(f"loud signal accepted (peak={np.max(np.abs(speech)):.4f})", has_real_speech(speech))

print("\nALL TESTS PASSED")
