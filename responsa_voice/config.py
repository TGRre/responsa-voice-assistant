# -*- coding: utf-8 -*-
"""
config.py — הגדרות מרכזיות לעוזר הקולי של פרויקט השו"ת.

6 פקודות קוליות בלבד, כל אחת מפעילה צירוף מקשים.
כל ההגדרות ניתנות לשינוי בקובץ זה בלבד.

תומך ב-Python 3.11+
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ============================================================
# 1. נתיבים ותהליך
# ============================================================

RESPONSA_EXE_PATH: str = r"C:\Program Files (x86)\ResponsaCD33\RESPONSA.exe"
RESPONSA_PROCESS_NAME: str = "RESPONSA"

RESPONSA_WINDOW_TITLE_PATTERNS: list[str] = [
    r"השו.?ת",
    r"שו[\"'׳״'ˮ]ת",
    r"responsa",
    r"bar.?ilan",
    r"פרויקט",
]

RESPONSA_WINDOW_CLASS: str = "ResponsaProject"

ERROR_WINDOW_PATTERNS: list[str] = [
    r"שגיאה", r"error", r"אזהרה", r"warning",
    r"אישור", r"שים לב", r"האם אתה בטוח",
]

ERROR_DISMISS_BUTTONS: list[str] = [
    "אישור", "OK", "סגור", "Close", "בטל", "Cancel", "Yes", "לא", "No",
]

# ============================================================
# 2. פקודות קוליות — 7 פקודות בלבד
# ============================================================
# כל פקודה: דפוסי regex + מילות הפעלה + רצף מקשים + משוב קולי.
# "PASTE" ברצף = הדבקת ה-payload מה-clipboard.
# "wait:N" ברצף = השהיה של N שניות לפני הצעד הבא.
# "triggers" = מילים שאליהן מצמידים את המילה הראשונה המשובשת (מילה-גזע בלבד,
# חייבת להתאים לדפוסי ה-regex).

VOICE_COMMANDS: dict[str, dict] = {
    "open": {
        "patterns": [
            r"(?:ל)?(?:ת)?פתח(?:\s+לי)?(?:\s+(.+))?",
        ],
        "triggers": ["פתח"],
        "key_sequence": ["ctrl+o", "PASTE", "enter", "wait:2", "enter"],
        "has_payload": True,
        "feedback": "פותח",
    },
    "find": {
        "patterns": [
            r"(?:ת)?מצ(?:א|אי)(?:\s+לי)?(?:\s+את)?(?:\s+(.+))?",
        ],
        "triggers": ["מצא"],
        "key_sequence": ["ctrl+f", "PASTE", "enter"],
        "has_payload": True,
        "feedback": "מחפש",
    },
    "next": {
        "patterns": [
            r"הבא(?:ה)?",
            r"עוד",
            r"תוצאה\s+הבאה",
        ],
        "triggers": ["הבא", "הבאה", "עוד", "תוצאה"],
        "key_sequence": ["f3"],
        "has_payload": False,
        "feedback": "הבא",
    },
    "enter": {
        "patterns": [
            r"enter",
            r"א?י?נ(?:ת|ט)ר",
            r"אישור",
            r"אוקיי?",
            r"בסדר",
        ],
        "triggers": ["enter", "אנטר", "אישור", "אוקי", "אוקיי", "בסדר"],
        "key_sequence": ["enter"],
        "has_payload": False,
        "feedback": "מאשר",
    },
    "up": {
        "patterns": [
            r"למעלה",
            r"מעלה",
            r"עוד\s+למעלה",
        ],
        "triggers": ["למעלה", "מעלה", "עוד"],
        "key_sequence": ["up"],
        "has_payload": False,
        "feedback": "למעלה",
    },
    "down": {
        "patterns": [
            r"למטה",
            r"מטה",
            r"עוד\s+למטה",
        ],
        "triggers": ["למטה", "מטה", "עוד"],
        "key_sequence": ["down"],
        "has_payload": False,
        "feedback": "למטה",
    },
    "close": {
        "patterns": [
            r"(?:א|ל|ת)?סגור(?:י)?(?:\s+(?:לי|נא))?(?:\s+(?:את\s+)?ה?(?:חלון|חלונות)(?:\s+שלי)?)?(?:\s+עכשיו)?",
            r"סגו?ר(?:ה|ת|ים|ות)?(?:\s+(?:את\s+)?ה?(?:חלון|חלונות))?",
            r"סגירה(?:ת)?(?:\s+(?:את\s+)?ה?(?:חלון|חלונות))?",
            r"(?:ל|ת)?צ[א](?:ת)?(?:י)?(?:\s+(?:מן|מה)ה?(?:חלון|חלונות))?",
            r"יציאה(?:\s+(?:מן|מה)ה?(?:חלון|חלונות))?",
        ],
        "triggers": ["סגור", "סגורי", "סגירה", "סוגר", "צא", "תצא", "יציאה"],
        "key_sequence": ["ctrl+w"],
        "has_payload": False,
        "feedback": "סוגר",
    },
}

# ============================================================
# 3. מונחים תורניים להזרקה ל-Whisper initial_prompt
# ============================================================

COMMAND_TERMS_PROMPT: str = (
    "פקודות קוליות נפוצות: פתח, מצא, תמצא, הבא, הבאה, עוד, תוצאה, "
    "enter, אישור, אוקיי, בסדר, למעלה, למטה. "
)

TORAH_TERMS_PROMPT: str = (
    "מילים ומונחים נפוצים בתחום התורני: "
    "תורה, נביאים, כתובים, תלמוד בבלי, תלמוד ירושלמי, משנה, גמרא, "
    "רש\"י, תוספות, רמב\"ם, רמב\"ן, שולחן ערוך, אורח חיים, יורה דעה, "
    "אבן העזר, חושן משפט, שו\"ת, סימן, סעיף, דף, עמוד, "
    "ע\"א, ע\"ב, פרק, הלכה, ברייתא, מדרש, זוהר, "
    "משנה ברורה, באר היטב, ט\"ז, ש\"ך, "
    "חתם סופר, אגרות משה, יביע אומר, "
    "ברכות, שבת, עירובין, פסחים, ראש השנה, יומא, סוכה, "
    "ביצה, תענית, מגילה, יבמות, כתובות, "
    "בבא קמא, בבא מציעא, בבא בתרא, סנהדרין"
)

# ============================================================
# 3b. הצמדת המילה הראשונה למילת הפעלה קרובה
# ============================================================
# כשה-STT משבש את המילה הראשונה (למשל "פתח" → "כך"), מחליפים אותה
# במילת הפעלה (trigger) הקרובה ביותר — רק אם הדמיון מעל הסף.
# FIRST_WORD_OVERRIDES: מיפוי ידני של בלבולים עקשנים (נבדק לפני חישוב הדמיון).

FIRST_WORD_SNAP_THRESHOLD: float = 0.55
FIRST_WORD_SNAP_MIN_LEN: int = 3

FIRST_WORD_OVERRIDES: dict[str, str] = {
    "כך": "פתח",
    "סבבה": "בסדר",
    "כע": "אוקיי",
}

# ============================================================
# 4. הגדרות VAD (זיהוי דיבור)
# ============================================================

@dataclass
class VADConfig:
    sample_rate: int = 16000
    frame_duration_ms: int = 30
    padding_duration_ms: int = 300
    threshold: float = 0.5
    aggressiveness: int = 2
    ptt_enabled: bool = True
    ptt_hotkey: str = "space"
    wake_word: str = "שו\"ת"
    wake_word_threshold: float = 0.7
    max_speech_duration: float = 30.0
    silence_timeout: float = 1.5

VAD_CONFIG = VADConfig()

# ============================================================
# 5. הגדרות STT (Speech-to-Text)
# ============================================================

@dataclass
class STTConfig:
    model_name: str = "ivrit-ai/whisper-large-v3-turbo-ct2"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "he"
    beam_size: int = 2
    temperature: float = 0.0
    initial_prompt: str = ""
    vad_filter: bool = True
    vad_threshold: float = 0.5
    word_timestamps: bool = False
    condition_on_previous_text: bool = False

STT_CONFIG = STTConfig()

# ============================================================
# 6. הגדרות TTS — הקראת משוב קולי
# ============================================================

@dataclass
class TTSConfig:
    rate_wpm: int = 170
    volume: float = 1.0
    voice_name_override: str = ""
    fallback_to_print: bool = True

TTS_CONFIG = TTSConfig()

# ============================================================
# 6b. הגדרות LLM — סוכן תיקון פקודות (OpenAI-compatible)
# ============================================================
# נקרא רק כשה-regex לא בטוח (מסלול היברידי — ראה ai_verifier.py).
# base_url ריק → משתנה הסביבה OPENAI_BASE_URL, אחרת OpenAI רשמי.

@dataclass
class LLMConfig:
    model: str = "gpt-4o-mini"
    base_url: str = ""
    timeout_s: float = 2.5
    max_tokens: int = 150
    cache_size: int = 256
    snap_score_min: float = 0.85
    snap_margin_min: float = 0.08

LLM_CONFIG = LLMConfig()

# ============================================================
# 7. הגדרות אוטומציה
# ============================================================

@dataclass
class AutomationConfig:
    action_delay: float = 0.1
    clipboard_settle_time: float = 0.05

AUTOMATION_CONFIG = AutomationConfig()

# ============================================================
# 8. פונקציות עזר
# ============================================================

_COMPACT_WHISPER_PROMPT_FALLBACK: str = (
    'פתח מסכת חולין דף קט עמוד א, בבא קמא, שבת, ברכות, יבמות, '
    'רש"י, תוספות, רמב"ם הלכות, שולחן ערוך אורח חיים יורה דעה חושן משפט '
    'סימן סעיף, משנה ברורה, פרשת השבוע, פרק פסוק.'
)


def get_whisper_initial_prompt() -> str:
    """Return a compact Whisper initial_prompt within Whisper's 224-token window."""
    try:
        from engine_bridge import get_whisper_prompt
        prompt = get_whisper_prompt()
        if prompt:
            return prompt
    except Exception:
        pass
    return _COMPACT_WHISPER_PROMPT_FALLBACK


# ============================================================
# 9. בדיקת תקינות (רצה ב-import)
# ============================================================

def _validate_config() -> None:
    assert isinstance(VOICE_COMMANDS, dict) and len(VOICE_COMMANDS) == 7
    for name, cmd in VOICE_COMMANDS.items():
        assert "patterns" in cmd and cmd["patterns"], f"{name}: missing patterns"
        assert "triggers" in cmd and cmd["triggers"], f"{name}: missing triggers"
        assert all(isinstance(t, str) and t.strip() for t in cmd["triggers"]), \
            f"{name}: bad trigger word"
        assert "key_sequence" in cmd and cmd["key_sequence"], f"{name}: missing key_sequence"
        assert "feedback" in cmd and cmd["feedback"], f"{name}: missing feedback"
    assert 0.0 < FIRST_WORD_SNAP_THRESHOLD <= 1.0
    assert FIRST_WORD_SNAP_MIN_LEN >= 1
    for key, val in FIRST_WORD_OVERRIDES.items():
        assert isinstance(key, str) and key.strip(), "override: empty key"
        assert isinstance(val, str) and val.strip(), f"override {key}: empty value"
    assert VAD_CONFIG.sample_rate in (8000, 16000, 32000, 48000)
    assert VAD_CONFIG.frame_duration_ms in (10, 20, 30)
    assert 0.0 <= VAD_CONFIG.threshold <= 1.0
    assert 0 < TTS_CONFIG.rate_wpm <= 500
    assert 0.0 <= TTS_CONFIG.volume <= 1.0
    assert LLM_CONFIG.model.strip()
    assert LLM_CONFIG.timeout_s > 0
    assert LLM_CONFIG.max_tokens > 0
    assert LLM_CONFIG.cache_size > 0
    assert 0.0 < LLM_CONFIG.snap_score_min <= 1.0
    assert LLM_CONFIG.snap_margin_min >= 0.0

_validate_config()
