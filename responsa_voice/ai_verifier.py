# -*- coding: utf-8 -*-
"""
ai_verifier.py — סוכן AI מהיר לאימות/תיקון זיהוי פקודות של Whisper.

מסלול היברידי (מהירות קודם):
1. regex מדויק ללא payload        → ללא רשת (<10ms).
2. regex + payload                → הצמדה מקומית לקטלוג הספרים;
                                    אם בטוח — ללא רשת, אחרת קריאת LLM.
3. regex נכשל                     → הצמדת מילה ראשונה (עם בדיקת שוליים);
                                    אם חד-משמעי — ללא רשת, אחרת קריאת LLM.

הסוכן מקבל הקשר (אוצר הפקודות + מועמדי ספרים) ומחזיר JSON מובנה:
command, payload, corrected_utterance, book_match, confidence.

נדרש OPENAI_API_KEY (או client מוזרק לבדיקות) — ניתן לשים אותו בקובץ
.env לצד המודול, או כמשתנה סביבה. OPENAI_BASE_URL לשירות תואם.

תומך ב-Python 3.11+
"""

from __future__ import annotations

import os
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel

from book_catalog import (
    build_catalog_block,
    find_book,
    has_spoken_numbers,
    normalize_reference,
    snap_payload,
    split_payload,
)
from config import (
    FIRST_WORD_OVERRIDES,
    FIRST_WORD_SNAP_THRESHOLD,
    LLMConfig,
    LLM_CONFIG,
    VOICE_COMMANDS,
)
from intent_router import (
    _fold_word,
    _normalize,
    _snap_candidates,
    _try_exact,
)

CommandName = Literal["open", "find", "next", "enter", "up", "down", "close"]

_TRIGGER_MARGIN_MIN: float = 0.10   # שוליים ל-mילת הפעלה חד-משמעית
_PREFILTER_TOP_N: int = 8           # כמה רשומות ספר לשלוח לפרומפט
_PREFILTER_MIN_SCORE: float = 0.30


# ============================================================
# 0. טעינת .env (ללא תלויות חיצוניות)
# ============================================================

def _load_env_file(path: Path) -> None:
    """טוען KEY=VALUE מתוך .env. לא דורס משתני סביבה שהוגדרו מראש."""
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_env_file(Path(__file__).resolve().parent / ".env")
if os.environ.get("APPDATA"):
    _load_env_file(Path(os.environ["APPDATA"]) / "ResponsaVoice" / ".env")
if getattr(sys, "frozen", False):
    _load_env_file(Path(sys.executable).resolve().parent / ".env")


def _mask_key(text: str) -> str:
    """מסתיר מפתחות API מתוך הודעות שגיאה לפני הדפסה."""
    return re.sub(r"sk-[A-Za-z0-9_\-]{6}[A-Za-z0-9_\-]*", "sk-***", text)


# ============================================================
# 1. מבני פלט
# ============================================================

class VerificationResult(BaseModel):
    """התשובה המובנית מהסוכן."""

    command: Optional[CommandName] = None
    payload: str = ""
    corrected_utterance: str = ""
    book_match: Optional[str] = None
    confidence: float = 0.0


@dataclass
class Resolution:
    """תוצאת ההחלטה הסופית של המסלול ההיברידי."""

    command: Optional[str]
    payload: str
    source: str        # 'regex' | 'snap' | 'ai' | 'cache' | 'none'
    corrected: str
    confidence: float
    book_ok: bool = True   # שם הספר תואם ספר אמיתי בקטלוג


# ============================================================
# 2. AIVerifier
# ============================================================

class AIVerifier:
    """
    סוכן תיקון פקודות. client אופציונלי (מעבד system/user → JSON)
    מוזרק לבדיקות — אחרת משתמש ב-OpenAI-compatible API.
    """

    def __init__(
        self,
        llm_config: LLMConfig = LLM_CONFIG,
        *,
        client: Optional[Callable[[str, str], str]] = None,
    ):
        self._cfg = llm_config
        self._client = client
        self._api = None  # נטען עצלן
        self._cache: OrderedDict[str, VerificationResult] = OrderedDict()

    # --- זמינות ---

    def is_available(self) -> bool:
        return self._client is not None or bool(os.environ.get("OPENAI_API_KEY"))

    # --- cache ---

    def _cache_get(self, key: str) -> Optional[VerificationResult]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_put(self, key: str, value: VerificationResult) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cfg.cache_size:
            self._cache.popitem(last=False)

    # --- קריאת LLM ---

    def _call_llm(self, system: str, user: str) -> str:
        if self._client is not None:
            return self._client(system, user)

        if self._api is None:
            from openai import OpenAI

            base_url = self._cfg.base_url or os.environ.get("OPENAI_BASE_URL") or None
            self._api = OpenAI(
                base_url=base_url,
                timeout=self._cfg.timeout_s,
            )
        resp = self._api.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL") or self._cfg.model,
            temperature=0,
            max_tokens=self._cfg.max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    def _prefilter_entries(self, text: str) -> list:
        results = snap_payload(
            text, top_n=_PREFILTER_TOP_N, min_score=_PREFILTER_MIN_SCORE
        )
        return [entry for _, entry, _ in results]

    @staticmethod
    def _build_system_prompt() -> str:
        return """אתה מתקן זיהוי פקודות קול עבור פרויקט השו"ת (ספרים תורניים בעברית).
ההקלטה עברה דרך Whisper ועשויה להכיל שיבושים רבים: אותיות שבורות, מילים מקוצרות, פסיקים מיותרים, ושמות אותיות במקום מספרים.

אוצר הפקודות (7 פקודות):
- open (פתח/לפתח/תפתח) + payload = ייחוס ספר תורני
- find (מצא/תמצא/מצאי) + payload = טקסט חופשי לחיפוש — החזר את הטקסט כמו שהדובר התכוון, בלי לתקן לפורמט ייחוס
- next (הבאה/הבא/עוד/תוצאה הבאה)
- enter (אנטר/אישור/אוקיי/בסדר/enter)
- up (למעלה/מעלה/עוד למעלה)
- down (למטה/מטה/עוד למטה)
- close (סגור/תסגור/סגירה/סוגר/צא/יציאה) — סגירת חלון, ללא payload

פורמט ה-payload של open — ייחוס תורני תקני בלבד. סגנון המיקום תלוי בסוג הספר:
- חומש (בראשית/שמות/ויקרא/במדבר/דברים): {ספר} [פרק X] [פסוק Y] — 'בראשית כ"ז פסוק ג''
- תלמוד/משנה/תוספתא: {ספר} [כרך א/ב] [דף X] [עמוד א|ב] — 'יבמות דף י"ד עמוד ב'
- שולחן ערוך (או"ח/יו"ד/אב"ע/חו"מ) וספרי שו"ת: {ספר} [סימן X] [סעיף Y] — 'שו"ע יו"ד סימן פ"ז סעיף ג'
- משנה תורה (רמב"ם): {הלכות} [פרק X] [הלכה Y] — 'רמב"ם הלכות שבת פרק א הלכה א'
כללים:
1. מספור עברי: אותיות עם גרשיים לפני האות האחרונה — י"ד (14), כ"ז (27), קנ"ב (152), ש"א (301). אות אחת — בלי גרשיים.
2. שמות אותיות → אותיות: אלף→א, בית→ב, גימל→ג, דלת→ד, הי→ה, וו→ו, זין→ז, חית→ח, טית→ט, יוד→י, כף→כ, למד→ל, מם→מ, נון→נ, סמך→ס, עין→ע, פי→פ, צדי→צ, קוף→ק, ריש→ר, שין→ש, תיו→ת.
   דוגמה: 'יוד דלת' (או 'יו דלת') → י"ד ; 'עמוד בית' → עמוד ב.
3. מילות מספרים מדוברות → מספור עברי: 'ארבע עשרה'→י"ד, 'שלוש מאות ואחת'→ש"א, 'שניים'→ב.
4. עמוד — תמיד א או ב. 'עמוד הבית'/'עמוד בית'/'עמוד ב' → עמוד ב.
5. אין פסיקים ב-payload, אין ספרות ערביות (14, 4), אין מילים באנגלית.
6. תקן מילים משובשות לפי ההקשר: 'תחי'/'תפחי'→פתח, 'במות'→יבמות, 'תף'→דף, 'פסח'→פתח.
7. אל תנחש פרטי ייחוס שלא נאמרו — רק תקן צורה.
8. קיצורים נפוצים בדיבור: סי'=סימן, סעי'/סע'=סעיף, עמ'=עמוד, ע"א/ע"ב=עמוד א/ב, הל'=הלכה, פס'=פסוק, פ"ק=פרק.
9. מפרשי התלמוד (ראשונים ואחרונים) הם ספרים בפני עצמם: רי"ף, רא"ש, ר"ן, ריטב"א, רשב"א, רשב"ם, תוספות, מהרש"א, מהרש"ל, מאירי — הייחוס שלהם הוא שם המחלקה + דף.

דוגמאות:
  'תחי, במות, תף, יו, דלת, עמוד בית' → command: open, payload: 'יבמות דף י"ד עמוד ב'
  'תפחי בראשית כ"ז ג'' → command: open, payload: 'בראשית כ"ז פסוק ג''
  'פתח שולחן ערוך יורה דעה סימן פ"ז סעיף ג' → command: open, payload: 'שו"ע יו"ד סימן פ"ז סעיף ג'
  'תפחי רמב"ם הלכות שבת פרק א הלכה א' → command: open, payload: 'רמב"ם הלכות שבת פרק א הלכה א'
  'פתח שולחן ערוך אורח חיים סימן שלוש מאות ואחת' → command: open, payload: 'שו"ע או"ח סימן ש"א'
  'פסח ברכות אלף עמוד בית' → command: open, payload: 'ברכות א עמוד ב'
  'תמצא שלום' → command: find, payload: 'שלום'

book_match: שם הספר הקנוני אם זוהה, אחרת null.
confidence: 0 עד 1. אם אין פקודה — command null.
החזר JSON בלבד עם המפתחות: command, payload, corrected_utterance, book_match, confidence."""

    def verify(
        self,
        raw_text: str,
        hint_command: Optional[str] = None,
        hint_payload: str = "",
    ) -> Optional[VerificationResult]:
        """קריאת LLM אחת: מחזיר VerificationResult, או None בנפילה."""
        if not self.is_available():
            return None

        hint = "אין"
        if hint_command:
            hint = f"command={hint_command}" + (
                f", payload={hint_payload}" if hint_payload else ""
            )
        entries = self._prefilter_entries(hint_payload or raw_text)
        catalog = build_catalog_block(entries) if entries else "(ללא מועמדים)"
        user = (
            f"טקסט גולמי: {raw_text}\n"
            f"רמז מה-regex: {hint}\n"
            f"מועמדי ספרים (רשימה):\n{catalog}"
        )

        try:
            raw = self._call_llm(self._build_system_prompt(), user)
            result = VerificationResult.model_validate_json(raw)
        except Exception as e:
            print(f"[AI] קריאת LLM נכשלה — נופל ל-regex: {_mask_key(str(e))}")
            return None

        result.confidence = min(max(result.confidence, 0.0), 1.0)
        return result

    # --- מסלול היברידי ---

    def resolve(self, text: str) -> Resolution:
        norm = _normalize(text)
        if not norm:
            return Resolution(None, "", "none", "", 0.0)

        cached = self._cache_get(norm)
        if cached is not None:
            payload, book_ok = cached.payload, True
            if cached.command == "open" and payload:
                payload, book_ok = self._finalize_open_payload(payload)
            return Resolution(
                cached.command, payload, "cache",
                cached.corrected_utterance or norm, cached.confidence, book_ok,
            )

        hint_command: Optional[str] = None
        hint_payload = ""
        local = self._resolve_local(norm)
        if local is not None:
            hint_command, hint_payload, source, corrected, confidence = local
            if source != "ambiguous":
                # תוצאה מקומית בטוחה (ספר אמיתי בקטלוג) — ללא רשת
                return Resolution(
                    hint_command, hint_payload, source, corrected, confidence, True
                )
            # 'ambiguous' (כולל ספר שאינו בקטלוג) → אישור AI

        vr = self.verify(norm, hint_command, hint_payload)
        if vr is not None:
            corrected = vr.corrected_utterance or norm
            payload = vr.payload or hint_payload
            book_ok = True
            if vr.command == "open" and payload:
                payload, book_ok = self._finalize_open_payload(payload)
                vr.payload = payload
            self._cache_put(norm, vr)
            return Resolution(
                vr.command, payload, "ai", corrected, vr.confidence, book_ok
            )

        # נפילה: מחזירים את תוצאת ה-regex אם יש, אחרת none
        if hint_command:
            payload = hint_payload
            book_ok = True
            if hint_command == "open" and payload:
                payload, book_ok = self._finalize_open_payload(payload)
            return Resolution(hint_command, payload, "regex", norm, 0.5, book_ok)
        return Resolution(None, "", "none", norm, 0.0, False)

    def _command_for_trigger(self, trigger: str) -> Optional[str]:
        """מיפוי מילת הפעלה לשם הפקודה שאליה היא שייכת."""
        folded = _fold_word(trigger)
        for name, cmd in VOICE_COMMANDS.items():
            for t in cmd["triggers"]:
                if _fold_word(t) == folded:
                    return name
        return None

    def _resolve_local(
        self, norm: str
    ) -> Optional[tuple[Optional[str], str, str, str, float]]:
        """
        ניסיון ללא רשת.

        Returns:
            (command, payload, source, corrected, confidence) כשר source אחד מ
            'regex' | 'snap' | 'ambiguous'; None אם אין בכלל התאמה.
            'ambiguous' = יש התאמה אך לא בטוחה → לקרוא ל-LLM עם הרמז.
        """
        # 1) regex מדויק
        exact = _try_exact(norm)
        if exact is not None:
            command, payload = exact
            if not payload:
                return command, "", "regex", norm, 1.0
            if command != "open":
                # find = טקסט חופשי לחיפוש — לא נוגעים ב-payload
                return command, payload, "regex", norm, 1.0
            return self._snap_payload_safe(command, payload, norm)

        # 2) הצמדת מילה ראשונה — רק אם חד-משמעית
        words = norm.split()
        candidates = _snap_candidates(words[0])
        if not candidates:
            return None

        scored = sorted(
            (
                (
                    SequenceMatcher(
                        None, _fold_word(words[0]), _fold_word(trigger)
                    ).ratio(),
                    trigger,
                )
                for trigger in candidates
            ),
            key=lambda item: -item[0],
        )
        top_score, top_trigger = scored[0]
        margin = top_score - scored[1][0] if len(scored) > 1 else 1.0

        # override ידני = תמיד חד-משמעי
        is_override = _fold_word(words[0]) in {
            _fold_word(k) for k in FIRST_WORD_OVERRIDES
        }
        if not is_override and (top_score < FIRST_WORD_SNAP_THRESHOLD or margin < _TRIGGER_MARGIN_MIN):
            # לא חד-משמעי — רמז ל-LLM לפי מילת הפעלה המובילה
            return self._command_for_trigger(top_trigger), "", "ambiguous", norm, top_score

        corrected_norm = " ".join([top_trigger] + words[1:])
        exact = _try_exact(corrected_norm)
        if exact is None:
            return (top_trigger, "", "ambiguous", norm, top_score)

        command, payload = exact
        if not payload:
            return command, "", "snap", corrected_norm, max(top_score, 0.9)
        if command != "open":
            return command, payload, "snap", corrected_norm, max(top_score, 0.9)
        return self._snap_payload_safe(command, payload, corrected_norm)

    def _snap_payload_safe(
        self, command: str, payload: str, corrected_norm: str
    ) -> tuple[str, str, str, str, float]:
        """
        'snap' רק אם שם הספר הוא ספר אמיתי בקטלוג (התאמה מדויקת)
        ואין מספרים במילים. אחרת 'ambiguous' — לאישור AI.
        """
        book_part, _locator = split_payload(payload)
        fits = bool(book_part) and find_book(book_part) is not None
        candidates = snap_payload(payload)
        top_score = candidates[0][0] if candidates else 0.0

        if fits and not has_spoken_numbers(payload):
            if candidates:
                return command, candidates[0][2], "snap", corrected_norm, max(top_score, 0.95)
            return command, payload, "snap", corrected_norm, 1.0
        return command, payload, "ambiguous", corrected_norm, top_score

    def _finalize_open_payload(self, payload: str) -> tuple[str, bool]:
        """
        אימות סופי: שם הספר חייב להיות ספר אמיתי בקטלוג.
        אם כן — קנוניזציה; אם לא — הצמדה מקומית; אחרת החזרה עם book_ok=False.
        """
        payload = normalize_reference(payload)
        book_part, _locator = split_payload(payload)
        if book_part and find_book(book_part) is not None:
            return payload, True
        candidates = snap_payload(payload)
        if candidates and candidates[0][0] >= self._cfg.snap_score_min:
            return candidates[0][2], True
        return payload, False


# ============================================================
# 3. ממשק מודולרי (singleton)
# ============================================================

_VERIFIER: Optional[AIVerifier] = None


def reset_verifier() -> None:
    """מאפס את ה-singleton כך שהגדרות API חדשות ייטענו מיד."""
    global _VERIFIER
    _VERIFIER = None


def resolve(text: str) -> Resolution:
    """נקודת כניסה נוחה: מסלול היברידי עם singleton עצלן."""
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = AIVerifier()
    return _VERIFIER.resolve(text)


# ============================================================
# 4. בדיקות עצמיות (ללא מפתח — client מזויף)
# ============================================================

def _selftest() -> int:
    failures = 0
    calls: list[str] = []

    def fake_client(system: str, user: str) -> str:
        calls.append(user)
        return (
            '{"command": "open", "payload": "ברכות", '
            '"corrected_utterance": "פתח ברכות", '
            '"book_match": "ברכות", "confidence": 0.9}'
        )

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"[PASS] {name}")
        else:
            failures += 1
            print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))

    v = AIVerifier(client=fake_client)

    # --- ללא רשת: regex נקי ---
    r = v.resolve("הבא")
    check(
        "regex 'הבא' → next",
        r.command == "next" and r.source == "regex",
        f"התקבל {r}",
    )
    r = v.resolve("תסגור את החלון")
    check(
        "regex 'תסגור את החלון' → close",
        r.command == "close" and r.source == "regex",
        f"התקבל {r}",
    )
    check("regex לא קורא ל-LLM", calls == [], f"קריאות: {len(calls)}")

    # --- ללא רשת: regex + payload snap ---
    r = v.resolve('פתח ברכות ב')
    check(
        "snap 'פתח ברכות ב' → open('ברכות ב')",
        r.command == "open" and r.payload == "ברכות ב" and r.source == "snap",
        f"התקבל {r}",
    )
    check("snap לא קורא ל-LLM", calls == [], f"קריאות: {len(calls)}")

    # --- ללא רשת: מילת כרך כתובה → אות כרך ---
    r = v.resolve('פתח ברכות אלף')
    check(
        "snap 'פתח ברכות אלף' → open('ברכות א')",
        r.command == "open" and r.payload == "ברכות א" and r.source == "snap",
        f"התקבל {r}",
    )

    # --- ללא רשת: find = טקסט חופשי — ללא AI וללא snap ---
    r = v.resolve("תמצא שלום")
    check(
        "find 'תמצא שלום' → regex passthrough",
        r.command == "find" and r.payload == "שלום" and r.source == "regex",
        f"התקבל {r}",
    )
    check("find לא קורא ל-LLM", calls == [], f"קריאות: {len(calls)}")

    # --- ללא רשת: override מילה ראשונה ---
    r = v.resolve('כך ברכות ב')
    check(
        "override 'כך ברכות ב' → open",
        r.command == "open" and r.source == "snap",
        f"התקבל {r}",
    )

    # --- ללא רשת: אין פקודה, אין LLM זמין ---
    saved_key = os.environ.pop("OPENAI_API_KEY", None)
    saved_base = os.environ.pop("OPENAI_BASE_URL", None)
    try:
        v_noapi = AIVerifier(client=None)
        r = v_noapi.resolve("שלום עולם")
        check(
            "בלי מפתח: 'שלום עולם' → none",
            r.command is None and r.source == "none",
            f"התקבל {r}",
        )
    finally:
        if saved_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_key
        if saved_base is not None:
            os.environ["OPENAI_BASE_URL"] = saved_base

    # --- LLM (client מזויף): payload לא-בר-הצמדה → ambiguous → AI ---
    r = v.resolve("פתח סדר זרעים")
    check(
        "AI 'פתח סדר זרעים' → open('ברכות')",
        r.command == "open" and r.source == "ai" and r.payload == "ברכות",
        f"התקבל {r}",
    )
    check("AI בוצע פעם אחת", len(calls) == 1, f"קריאות: {len(calls)}")

    # --- cache ---
    r = v.resolve("פתח סדר זרעים")
    check(
        "cache 'פתח סדר זרעים'",
        r.source == "cache" and r.command == "open",
        f"התקבל {r}",
    )
    check("cache ללא קריאת LLM נוספת", len(calls) == 1, f"קריאות: {len(calls)}")

    # --- מספרים במילים → נשלח ל-AI גם כשהספר בטוח ---
    r = v.resolve('פתח שולחן ערוך אורח חיים סימן שלוש מאות ואחת')
    check(
        "spoken-number → AI",
        r.command == "open" and r.source == "ai",
        f"התקבל {r}",
    )

    # --- ספר שאינו בקטלוג → אישור AI ---
    def rittba_client(system: str, user: str) -> str:
        calls.append(user)
        return (
            '{"command": "open", "payload": "ריטב\\"א בבא מציעא נח", '
            '"corrected_utterance": "פתח ריטב\\"א בבא מציעא נח", '
            '"book_match": "ריטב\\"א", "confidence": 0.95}'
        )

    v_r = AIVerifier(client=rittba_client)
    r = v_r.resolve("תפחי ריטבה בבא מציעא נח")
    check(
        "AI מאשר ספר אמיתי: 'ריטבה' → 'ריטב\"א'",
        r.command == "open"
        and r.payload == 'ריטב"א בבא מציעא נח'
        and r.source == "ai"
        and r.book_ok,
        f"התקבל {r}",
    )

    def garbage_client(system: str, user: str) -> str:
        return (
            '{"command": "open", "payload": "גשם של ברכה", '
            '"corrected_utterance": "פתח גשם של ברכה", '
            '"book_match": null, "confidence": 0.4}'
        )

    v_g = AIVerifier(client=garbage_client)
    r = v_g.resolve("פתח גשם של ברכה")
    check(
        "ספר לא מוכר → book_ok=False",
        r.command == "open" and not r.book_ok,
        f"התקבל {r}",
    )

    # --- נפילת client → fallback ---
    def failing_client(system: str, user: str) -> str:
        raise RuntimeError("network down")

    v_fail = AIVerifier(client=failing_client)
    r = v_fail.resolve("פתח סדר זרעים")
    check(
        "נפילת LLM → fallback regex",
        r.command == "open" and r.source == "regex" and r.payload == "סדר זרעים",
        f"התקבל {r}",
    )

    # --- JSON שגוי → fallback ---
    v_bad = AIVerifier(client=lambda s, u: "this is not json")
    r = v_bad.resolve("פתח סדר זרעים")
    check(
        "JSON שגוי → fallback",
        r.command == "open" and r.source == "regex",
        f"התקבל {r}",
    )

    # --- ריק ---
    r = v.resolve("")
    check("טקסט ריק → none", r.command is None and r.source == "none", f"התקבל {r}")

    print("\n" + "=" * 50)
    if failures:
        print(f"  סיכום: {failures} בדיקות נכשלו")
    else:
        print("  סיכום: כל הבדיקות עברו")
    print("=" * 50)
    return 1 if failures else 0


# ============================================================
# 5. CLI
# ============================================================

def main() -> int:
    import argparse
    import json

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="סוכן AI לתיקון זיהוי פקודות קול"
    )
    parser.add_argument("--text", type=str, help="טקסט פקודה לבדיקה")
    parser.add_argument("--selftest", action="store_true", help="בדיקות offline")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    if args.text:
        r = resolve(args.text)
        print(json.dumps({
            "command": r.command,
            "payload": r.payload,
            "source": r.source,
            "corrected": r.corrected,
            "confidence": r.confidence,
            "book_ok": r.book_ok,
        }, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
