# -*- coding: utf-8 -*-
"""
engine_bridge.py — גשר בין מנוע הזיהוי החדש (responsa_voice_engine)
לבין צינור הקול הקיים (responsa_voice).

המנוע החדש מזהה פקודות קוליות עבריות ומייצר טקסט קנוני
בלי גרש/גרשיים — מה שתוכנת פרויקט השו\"ת יודעת לקרוא.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# הוסף את תיקיית המנוע כדי לייבא את responsa_voice_engine (עובד גם ב-PyInstaller)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _ENGINE_ROOT = Path(sys._MEIPASS) / "responsa_voice_engine"  # type: ignore[attr-defined]
else:
    _ENGINE_ROOT = Path(__file__).resolve().parent.parent / "responsa_voice_engine"
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from src.hebrew_utils import clean_spoken_command
from src.matcher import CommandMatcher
from src.models import Action, MatchStatus
from src.schema_loader import load_schema_index
from src.whisper_config import generate_whisper_prompt

_matcher: CommandMatcher | None = None
_whisper_prompt: str | None = None

_SIMPLE_PUNCT_RE = re.compile(r"[,\.\!\?;:]+")
_QUOTE_STRIP_RE = re.compile(r"[\"״'׳\u2019]")
_SPACES_RE = re.compile(r"\s+")


def _get_matcher() -> CommandMatcher:
    global _matcher
    if _matcher is None:
        _matcher = CommandMatcher(load_schema_index())
    return _matcher


def warm_up() -> None:
    """Pre-warm the matcher index at startup so first utterance has <10ms latency."""
    _get_matcher()


def get_whisper_prompt() -> str:
    """Return the compact (<=220 char / <=224 token) Whisper initial_prompt."""
    global _whisper_prompt
    if _whisper_prompt is None:
        _whisper_prompt = generate_whisper_prompt(_get_matcher().index)
    return _whisper_prompt


@dataclass
class EngineResult:
    """תוצאה ממנוע הזיהוי החדש — תואמת ל-Resolution מבחינת שדות שימושיים."""

    command: str | None
    payload: str
    source: str
    corrected: str
    confidence: float
    book_ok: bool = True
    status: str = ""
    tts_feedback: str = ""


# מיפוי פעולות המנוע החדש → שמות פקודות של ResponsaController
_ACTION_TO_COMMAND = {
    Action.OPEN: "open",
    Action.SEARCH: "find",
    Action.NAVIGATE: "open",
}

# פקודות פעולה פשוטות (בלי reference) — עוברות ל-controller ישירות
_SIMPLE_COMMANDS = {
    "סגור": "close", "סגורי": "close", "סגירה": "close", "סוגר": "close",
    "צא": "close", "תצא": "close", "יציאה": "close",
    "הבא": "next", "הבאה": "next", "הבא בבקשה": "next",
    "אישור": "enter", "אשר": "enter", "אוקיי": "enter", "אוקי": "enter",
    "למעלה": "up", "מעלה": "up",
    "למטה": "down", "מטה": "down",
}

_SORTED_SIMPLE_COMMANDS = sorted(_SIMPLE_COMMANDS.items(), key=lambda x: -len(x[0]))


def _normalize_simple_text(text: str) -> str:
    cleaned = _SIMPLE_PUNCT_RE.sub(" ", text)
    return _SPACES_RE.sub(" ", cleaned).strip()


def resolve_with_engine(text: str) -> EngineResult:
    """מנתח טקסט קולי עם המנוע החדש ומחזיר תוצאה נקייה (בלי גרש/גרשיים)."""
    # 1. פקודות פעולה פשוטות (סגור, הבא, למעלה...) — כולל סימני פיסוק כמו "הבא, בבקשה."
    cleaned_simple = _normalize_simple_text(text)
    for phrase, cmd in _SORTED_SIMPLE_COMMANDS:
        if cleaned_simple == phrase or cleaned_simple.startswith(phrase + " "):
            return EngineResult(
                command=cmd,
                payload="",
                source="engine",
                corrected=phrase,
                confidence=1.0,
                book_ok=True,
                status="OK",
                tts_feedback="",
            )

    # 2. זיהוי reference (מסכת דף, פרשה, רמב"ם, שולחן ערוך...)
    m = _get_matcher()
    result = m.match(text)

    # 3. חיפוש חופשי בתוך הטקסט (Ctrl+F): "חפש/מצא/נמצא <query>" עובר ישירות לחיפוש
    #    בלי להיחסם ע"י בדיקות טווח/חלק של מקורות ובלי למחוק מילים כמו "על" מהביטוי
    if result.action == Action.SEARCH:
        raw_query = clean_spoken_command(text).text
        query = _QUOTE_STRIP_RE.sub("", raw_query)
        query = _SIMPLE_PUNCT_RE.sub(" ", query)
        query = _SPACES_RE.sub(" ", query).strip()
        if query:
            return EngineResult(
                command="find",
                payload=query,
                source="engine",
                corrected=query,
                confidence=1.0,
                book_ok=True,
                status="OK",
                tts_feedback=f"מחפש {query}",
            )
        return EngineResult(
            command=None,
            payload="",
            source="engine",
            corrected="",
            confidence=0.0,
            book_ok=False,
            status="NEEDS_FALLBACK",
            tts_feedback="מה לחפש?",
        )

    command = _ACTION_TO_COMMAND.get(result.action, "open")
    payload = result.canonical_command or ""

    if result.status == MatchStatus.OK and payload:
        return EngineResult(
            command=command,
            payload=payload,
            source="engine",
            corrected=payload,
            confidence=result.confidence / 100.0,
            book_ok=True,
            status=result.status.value,
            tts_feedback=result.tts_feedback,
        )

    # 4. שגיאה דטרמיניסטית (INVALID_RANGE / INVALID_COMBINATION / AMBIGUOUS_SECTION) או NEEDS_FALLBACK
    # לעולם לא מבצעים פקודה לא תקינה!
    return EngineResult(
        command=None,
        payload="",
        source="engine",
        corrected="",
        confidence=0.0,
        book_ok=False,
        status=result.status.value,
        tts_feedback=result.tts_feedback,
    )
