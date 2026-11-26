# -*- coding: utf-8 -*-
"""
app_main.py — נקודת הכניסה הראשית של אפליקציית Windows (ResponsaVoice.exe).

מפעילה את העוזר הקולי במצב HUD צף, טוענת הגדרות מ-%APPDATA%\\ResponsaVoice,
ומונעת הפעלה כפולה במקביל.
"""

from __future__ import annotations

import ctypes
import sys

from app_settings import (
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_VERSION,
    apply_settings,
    load_settings,
    setup_app_logging,
)


def _acquire_single_instance_mutex():
    """מחזיר handle של Mutex אם זו ההרצה היחידה, אחרת None."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, f"Global\\{APP_NAME}_SingleInstance_v1")
        if not handle:
            return None
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return True  # fallback אם אינו על Windows


def _set_app_user_model_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{APP_NAME}.Desktop.{APP_VERSION}"
        )
    except Exception:
        pass


def _run_self_check() -> int:
    """בדיקה מהירה ללא GUI — מאמתת שהאריזה (PyInstaller) מכילה את כל הנתונים והמודולים."""
    from book_catalog import BOOKS
    from engine_bridge import get_whisper_prompt, resolve_with_engine

    settings = load_settings()
    apply_settings(settings)

    res = resolve_with_engine("פתח מסכת ברכות דף ב עמוד א")
    assert res.status == "OK" and res.command == "open" and res.payload, f"Engine failed: {res}"
    prompt = get_whisper_prompt()
    assert prompt, "Empty whisper prompt"
    assert len(BOOKS) > 100, f"Book catalog too small: {len(BOOKS)}"

    # אימות ייבוא תלויות קריטיות של קול ושמע
    import faster_whisper  # noqa: F401
    import keyboard  # noqa: F401
    import pyperclip  # noqa: F401
    import rapidfuzz  # noqa: F401
    import sounddevice  # noqa: F401
    import webrtcvad  # noqa: F401

    print(
        f"[SELF-CHECK OK] {APP_NAME} v{APP_VERSION} | "
        f"books={len(BOOKS)} | sample={res.payload}"
    )
    return 0


def main() -> int:
    if "--self-check" in sys.argv:
        return _run_self_check()

    setup_app_logging()
    _set_app_user_model_id()

    mutex = _acquire_single_instance_mutex()
    if mutex is None:
        try:
            import tkinter as tk
            from tkinter import messagebox

            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo(
                APP_DISPLAY_NAME,
                f"{APP_DISPLAY_NAME} כבר פועל ברקע.",
            )
            r.destroy()
        except Exception:
            pass
        return 0

    # העברת ארגומנט --execute ל-voice_pipeline.main()
    sys.argv = [sys.argv[0], "--execute"]
    from voice_pipeline import main as pipeline_main

    return pipeline_main()


if __name__ == "__main__":
    sys.exit(main())
