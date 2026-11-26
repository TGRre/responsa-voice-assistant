"""Whisper initial_prompt generation for faster-whisper Hebrew recognition."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from src.schema_loader import SchemaIndex

# Whisper's hard initial_prompt cap is 224 tokens (truncates from the START!).
# Hebrew averages ~1–1.5 tokens per character, so keep the prompt <= 220 chars
# (and <= 210 tokens when an exact tokenizer callback is provided).
_CHAR_BUDGET = 220
_TOKEN_BUDGET = 210

# Clean, standard Hebrew command vocabulary (NEVER include misspelling aliases,
# which bias Whisper toward producing errors).
_PROMPT_SEGMENTS: tuple[str, ...] = (
    'פתח מסכת חולין דף קט עמוד א',
    'בבא קמא',
    'שבת',
    'ברכות',
    'יבמות',
    'רש"י',
    'תוספות',
    'רמב"ם הלכות',
    'שולחן ערוך אורח חיים יורה דעה חושן משפט סימן סעיף',
    'משנה ברורה',
    'פרשת השבוע',
    'פרק פסוק',
)


def generate_whisper_prompt(
    index: SchemaIndex,
    tokenizer: Callable[[str], Sequence[int]] | None = None,
) -> str:
    """Generate a compact, standard-Hebrew initial_prompt for faster-whisper.

    Deterministic and strictly bounded to fit within Whisper's 224-token window
    without truncating leading action verbs or structural terms. Never includes
    misspelling aliases.
    """
    _ = index  # retained in signature for API compatibility
    chosen: list[str] = []

    for seg in _PROMPT_SEGMENTS:
        candidate = ", ".join([*chosen, seg]) + "."
        if len(candidate) > _CHAR_BUDGET:
            break
        if tokenizer is not None and len(tokenizer(candidate)) > _TOKEN_BUDGET:
            break
        chosen.append(seg)

    if not chosen:
        return ""
    return ", ".join(chosen) + "."
