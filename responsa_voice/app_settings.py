# -*- coding: utf-8 -*-
"""
app_settings.py — ניהול הגדרות משתמש, נתיבי משאבים ולוגים לאפליקציית Windows.

שומר הגדרות ב-%APPDATA%\\ResponsaVoice\\settings.json כך שניתן לשמור הגדרות
ומפתח API אופציונלי גם כשהאפליקציה מותקנת ב-Program Files, ובלי לשמור
מידע פרטי בתוך קוד המקור.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

APP_NAME = "ResponsaVoice"
APP_DISPLAY_NAME = 'עוזר קולי לפרויקט השו"ת'
APP_VERSION = "1.0.0"

PROVIDER_PRESETS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "qwen/qwen3-32b"),
    "openai": ("", "gpt-4o-mini"),
    "custom": ("", ""),
    "disabled": ("", ""),
}

_APPLIED_ENV_KEYS: set[str] = set()


def get_appdata_dir(*, create: bool = True) -> Path:
    """תיקיית הנתונים של המשתמש (%APPDATA%\\ResponsaVoice)."""
    base = os.environ.get("APPDATA")
    root = Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME.lower()}"
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return root


def get_settings_path(*, create: bool = False) -> Path:
    return get_appdata_dir(create=create) / "settings.json"


def get_log_path() -> Path:
    return get_appdata_dir(create=True) / "app.log"


def get_resource_path(*parts: str) -> Path:
    """נתיב למשאב ארוז — עובד הן בפיתוח והן ב-PyInstaller (sys._MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def detect_responsa_exe() -> str:
    """מזהה אוטומטית את התקנת RESPONSA.exe (הגרסה החדשה ביותר שנמצאה)."""
    candidates: list[Path] = []
    search_roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        try:
            for folder in root.glob("Responsa*"):
                exe = folder / "RESPONSA.exe"
                if exe.is_file():
                    candidates.append(exe)
        except OSError:
            continue

    if candidates:
        # מיין יורד כדי להעדיף את הגרסה הגבוהה ביותר (למשל ResponsaCD34 לפני ResponsaCD33)
        candidates.sort(key=lambda p: str(p).lower(), reverse=True)
        return str(candidates[0])

    return r"C:\Program Files (x86)\ResponsaCD33\RESPONSA.exe"


@dataclass
class UserSettings:
    responsa_exe_path: str = ""
    ptt_hotkey: str = "space"
    hud_corner: str = "top-right"
    tts_enabled: bool = True
    ai_provider: str = "groq"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.groq.com/openai/v1"
    openai_model: str = "qwen/qwen3-32b"


def load_settings(path: Optional[Path] = None) -> UserSettings:
    """טוען הגדרות משתמש מ-JSON; ממלא ברירות מחדל חסרות."""
    settings_path = path or get_settings_path()
    data: dict = {}
    if settings_path.is_file():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}

    defaults = UserSettings()
    exe_path = str(data.get("responsa_exe_path") or "").strip()
    if not exe_path:
        exe_path = detect_responsa_exe()

    hotkey = str(data.get("ptt_hotkey") or defaults.ptt_hotkey).strip() or defaults.ptt_hotkey
    corner = str(data.get("hud_corner") or defaults.hud_corner).strip()
    if corner not in ("top-right", "top-left", "bottom-right", "bottom-left"):
        corner = defaults.hud_corner

    provider = str(data.get("ai_provider") or defaults.ai_provider).strip().lower()
    if provider not in PROVIDER_PRESETS:
        provider = defaults.ai_provider

    tts_val = data.get("tts_enabled", defaults.tts_enabled)
    tts_enabled = bool(tts_val) if isinstance(tts_val, bool) else defaults.tts_enabled

    return UserSettings(
        responsa_exe_path=exe_path,
        ptt_hotkey=hotkey,
        hud_corner=corner,
        tts_enabled=tts_enabled,
        ai_provider=provider,
        openai_api_key=str(data.get("openai_api_key") or "").strip(),
        openai_base_url=str(
            data.get("openai_base_url")
            if "openai_base_url" in data
            else PROVIDER_PRESETS[provider][0]
        ).strip(),
        openai_model=str(
            data.get("openai_model")
            if "openai_model" in data
            else PROVIDER_PRESETS[provider][1]
        ).strip(),
    )


def save_settings(settings: UserSettings, path: Optional[Path] = None) -> Path:
    """שומר הגדרות משתמש לקובץ JSON בתיקיית %APPDATA%."""
    settings_path = path or get_settings_path(create=True)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings_path


def apply_settings(settings: UserSettings, *, explicit_save: bool = False) -> None:
    """מחיל את ההגדרות על config ומשתני הסביבה בזמן ריצה."""
    import config

    config.RESPONSA_EXE_PATH = settings.responsa_exe_path or detect_responsa_exe()
    config.VAD_CONFIG.ptt_hotkey = settings.ptt_hotkey or "space"

    # ניהול משתני ה-API: אם הוגדר מפתח בהגדרות — מחיל אותו;
    # אם המשתמש שמר מפורשות בלי מפתח או כיבה AI — מנקה מפתחות שהוחלו בעבר.
    if settings.ai_provider == "disabled" or not settings.openai_api_key:
        if explicit_save or "OPENAI_API_KEY" in _APPLIED_ENV_KEYS:
            os.environ.pop("OPENAI_API_KEY", None)
            _APPLIED_ENV_KEYS.discard("OPENAI_API_KEY")
        if explicit_save or "OPENAI_BASE_URL" in _APPLIED_ENV_KEYS:
            os.environ.pop("OPENAI_BASE_URL", None)
            _APPLIED_ENV_KEYS.discard("OPENAI_BASE_URL")
        if explicit_save or "OPENAI_MODEL" in _APPLIED_ENV_KEYS:
            os.environ.pop("OPENAI_MODEL", None)
            _APPLIED_ENV_KEYS.discard("OPENAI_MODEL")
    else:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
        _APPLIED_ENV_KEYS.add("OPENAI_API_KEY")

        if settings.openai_base_url:
            os.environ["OPENAI_BASE_URL"] = settings.openai_base_url
            _APPLIED_ENV_KEYS.add("OPENAI_BASE_URL")
        else:
            os.environ.pop("OPENAI_BASE_URL", None)
            _APPLIED_ENV_KEYS.discard("OPENAI_BASE_URL")

        if settings.openai_model:
            os.environ["OPENAI_MODEL"] = settings.openai_model
            _APPLIED_ENV_KEYS.add("OPENAI_MODEL")
        else:
            os.environ.pop("OPENAI_MODEL", None)
            _APPLIED_ENV_KEYS.discard("OPENAI_MODEL")

    # איפוס ה-singleton של ai_verifier אם כבר נטען
    ai_mod = sys.modules.get("ai_verifier")
    if ai_mod is not None and hasattr(ai_mod, "reset_verifier"):
        ai_mod.reset_verifier()


def setup_app_logging() -> None:
    """
    מנתב stdout/stderr לקובץ לוג במצב GUI (כשאין קונסולה ב-PyInstaller --windowed),
    ומוודא קידוד UTF-8 בטוח גם בהרצה רגילה.
    """
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        log_path = get_log_path()
        try:
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                log_path.write_text("", encoding="utf-8")
            log_file = open(log_path, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None or getattr(sys, "frozen", False):
                sys.stdout = log_file
            if sys.stderr is None or getattr(sys, "frozen", False):
                sys.stderr = log_file
        except OSError:
            pass
    else:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        if hasattr(sys.stderr, "reconfigure"):
            try:
                sys.stderr.reconfigure(encoding="utf-8")
            except Exception:
                pass
