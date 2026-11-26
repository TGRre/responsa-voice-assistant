# -*- coding: utf-8 -*-
"""
intent_router.py — התאמת פקודות קוליות לפרויקט השו"ת.

7 פקודות בלבד, כולן מותאמות ב-regex על פלט ה-STT.
ללא LLM — תגובה מיידית, ללא מפתח API.

תומך ב-Python 3.11+
"""

from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from typing import Optional

from config import (
    FIRST_WORD_OVERRIDES,
    FIRST_WORD_SNAP_MIN_LEN,
    FIRST_WORD_SNAP_THRESHOLD,
    VOICE_COMMANDS,
)

_TRAILING_PUNCT = ".!?,;:…"
_STRIP_CHARS = "\"'״׳'’"
_FINAL_FORMS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})


def _normalize(text: str) -> str:
    """נרמול קלט: רווחים כפולים, רווחים בסוף/התחלה, סימני פיסוק וגרשיים."""
    s = " ".join(text.split())
    s = s.strip().strip(_STRIP_CHARS).strip()
    return s.rstrip(_TRAILING_PUNCT).strip()


def _fold_word(word: str) -> str:
    """נרמול מילה להשוואה: צורות סופיות, גרשיים, רישיות."""
    return word.strip().strip(_STRIP_CHARS).translate(_FINAL_FORMS).lower()


def _try_exact(norm: str) -> Optional[tuple[str, str]]:
    """מתאים טקסט מלא מול דפוסי ה-regex של 6 הפקודות."""
    for name, cmd in VOICE_COMMANDS.items():
        for pattern in cmd["patterns"]:
            try:
                m = re.fullmatch(pattern, norm, re.IGNORECASE)
            except re.error:
                continue
            if m:
                payload = ""
                if cmd.get("has_payload") and m.lastindex:
                    payload = (m.group(1) or "").strip()
                return name, payload
    return None


def _snap_candidates(first: str) -> list[str]:
    """
    מילות הפעלה פוטנציאליות למילה הראשונה המשובשת.

    FIRST_WORD_OVERRIDES קודם (מיפוי ידני), אחרת מילות הפעלה (triggers)
    שהדמיון שלהן למילה ≥ FIRST_WORD_SNAP_THRESHOLD, ממוינות מהגבוה לנמוך.
    """
    folded = _fold_word(first)
    if not folded:
        return []

    for key, override in FIRST_WORD_OVERRIDES.items():
        if _fold_word(key) == folded:
            return [override]

    if len(folded) < FIRST_WORD_SNAP_MIN_LEN:
        return []

    seen: set[str] = set()
    scored: list[tuple[float, str]] = []
    for cmd in VOICE_COMMANDS.values():
        for trigger in cmd["triggers"]:
            score = SequenceMatcher(None, folded, _fold_word(trigger)).ratio()
            if score >= FIRST_WORD_SNAP_THRESHOLD and trigger not in seen:
                seen.add(trigger)
                scored.append((score, trigger))
    scored.sort(key=lambda item: (-item[0], -len(item[1])))
    return [trigger for _, trigger in scored]


def match_command(text: str) -> Optional[tuple[str, str]]:
    """
    מתאים טקסט מנורמל מול 6 הפקודות הקוליות.

    ניסיון ראשון: התאמה מדויקת (regex). אם נכשל — מצמידים את המילה
    הראשונה למילת הפעלה קרובה (override או דמיון מעל הסף) ומנסים שוב.

    Returns:
        (command_name, payload) — payload ריק לפקודות ללא payload,
        או None אם אין התאמה.
    """
    norm = _normalize(text)
    if not norm:
        return None

    result = _try_exact(norm)
    if result is not None:
        return result

    words = norm.split()
    for trigger in _snap_candidates(words[0]):
        corrected = " ".join([trigger] + words[1:])
        result = _try_exact(corrected)
        if result is not None:
            print(f"[INTENT] תיקון: '{norm}' → '{corrected}'")
            return result

    return None


# ============================================================
# בדיקות עצמיות
# ============================================================

def _selftest() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"[PASS] {name}")
        else:
            failures += 1
            print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))

    # --- נרמול ---
    check("נרמול רווחים", _normalize("  שלום   עולם  ") == "שלום עולם")
    check("נרמול פיסוק סופי", _normalize("הבא.") == "הבא")

    # --- חיובי: פקודות עם payload ---
    payload_cases = [
        ("פתח ברכות ב", "open", "ברכות ב"),
        ("תפתח לי ברכות ב עמוד ע\"א", "open", "ברכות ב עמוד ע\"א"),
        ("לפתח שו\"ע או\"ח של\"א", "open", 'שו"ע או"ח של"א'),
        ("פתח", "open", ""),
        ("תמצא שלום", "find", "שלום"),
        ("תמצא לי את האמת", "find", "האמת"),
        ("מצא אמת", "find", "אמת"),
        ("תמצאי חסד", "find", "חסד"),
    ]
    for utterance, expected_cmd, expected_payload in payload_cases:
        result = match_command(utterance)
        check(
            f"'{utterance}' → {expected_cmd}('{expected_payload}')",
            result is not None and result[0] == expected_cmd and result[1] == expected_payload,
            f"התקבל {result}",
        )

    # --- חיובי: הצמדת מילה ראשונה (override / דמיון / גרשיים) ---
    snap_cases = [
        ("כך ברכות ב", "open", "ברכות ב"),      # override
        ("פסח ברכות ב", "open", "ברכות ב"),    # דמיון 0.667
        ("תפסח ברכות ב", "open", "ברכות ב"),   # דמיון 0.571
        ("ממצ שלום", "find", "שלום"),           # דמיון 0.667
        ("תוצח הבאה", "next", ""),              # דמיון 0.667 + ריבוי מילים
        ("\"פתח ברכות ב\"", "open", "ברכות ב"),  # גרשיים
        ("שגור", "close", ""),                   # דמיון → סגור
        ("תיסגר", "close", ""),                  # דמיון → סגור
    ]
    for utterance, expected_cmd, expected_payload in snap_cases:
        result = match_command(utterance)
        check(
            f"snap '{utterance}' → {expected_cmd}('{expected_payload}')",
            result is not None and result[0] == expected_cmd and result[1] == expected_payload,
            f"התקבל {result}",
        )

    # --- חיובי: פקודות ללא payload ---
    simple_cases = [
        ("הבא", "next"),
        ("הבאה", "next"),
        ("עוד", "next"),
        ("תוצאה הבאה", "next"),
        ("enter", "enter"),
        ("אנטר", "enter"),
        ("אישור", "enter"),
        ("אוקיי", "enter"),
        ("בסדר", "enter"),
        ("למעלה", "up"),
        ("מעלה", "up"),
        ("עוד למעלה", "up"),
        ("למטה", "down"),
        ("מטה", "down"),
        ("עוד למטה", "down"),
        ("סגור", "close"),
        ("תסגור את החלון", "close"),
        ("סגור לי את החלון שלי", "close"),
        ("סגירת חלון", "close"),
        ("סוגר את החלונות", "close"),
        ("צא", "close"),
        ("תצא מהחלון", "close"),
        ("יציאה", "close"),
    ]
    for utterance, expected_cmd in simple_cases:
        result = match_command(utterance)
        check(
            f"'{utterance}' → {expected_cmd}",
            result is not None and result[0] == expected_cmd,
            f"התקבל {result}",
        )

    # --- שלילי: לא לחטוף ---
    negative_cases = [
        "שלום עולם",
        "אני רוצה ללמוד תורה",
        "מה קורה",
        "",
        "תורה",             # נמשך ל"תוצאה" אבל לא עובר regex מלא
        "חפס ברכות ב",      # דמיון 0.333 — מתחת לסף
        "פסחים",            # דמיון 0.5 — מתחת לסף
        "שלום",
    ]
    for utterance in negative_cases:
        result = match_command(utterance)
        check(f"ללא התאמה: '{utterance}'", result is None, f"נקלט {result}")

    print("\n" + "=" * 50)
    if failures:
        print(f"  סיכום: {failures} בדיקות נכשלו")
    else:
        print("  סיכום: כל הבדיקות עברו")
    print("=" * 50)
    return 1 if failures else 0


# ============================================================
# CLI
# ============================================================

def main() -> int:
    import argparse
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="התאמת פקודות קוליות לפרויקט השו\"ת"
    )
    parser.add_argument("--text", type=str, help="טקסט פקודה לבדיקה")
    parser.add_argument("--selftest", action="store_true", help="בדיקות offline")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    if args.text:
        result = match_command(args.text)
        if result:
            name, payload = result
            print(json.dumps({"command": name, "payload": payload},
                             ensure_ascii=False))
        else:
            print(json.dumps({"command": None, "payload": None},
                             ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
