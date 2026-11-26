# -*- coding: utf-8 -*-
"""
overlay.py — HUD צף (always-on-top) לעוזר הקולי של פרויקט השו"ת.

פס קומפקטי מעל המסך שמראה:
1. מצב המערכת (idle / listening / transcribing / understanding / executing / error)
2. המילים שזוהו (טקסט גולמי מ-Whisper)
3. הפקודה המתוקנת + הספר (אחרי AI/snap)

ה-UI רץ על mainloop של tkinter ב-thread הראשי; ה-pipeline שולח עדכונים
דרך queue (thread-safe). גרירה עם העכבר, Esc או כפתור ✕ לסגירה.

תומך ב-Python 3.11+
"""

from __future__ import annotations

import queue
import sys
from typing import Callable, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    _TK_AVAILABLE = True
except Exception:  # pragma: no cover - סביבה בלי tkinter
    tk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    _TK_AVAILABLE = False

# מצב → (תווית בעברית, צבע)
STATES: dict[str, tuple[str, str]] = {
    "loading": ("טוען מודל…", "#89dceb"),
    "idle": ("ממתין", "#6c7086"),
    "listening": ("מקליט…", "#a6e3a1"),
    "transcribing": ("מתמלל…", "#f9e2af"),
    "understanding": ("מפרש פקודה…", "#cba6f7"),
    "executing": ("מבצע…", "#89b4fa"),
    "error": ("שגיאה", "#f38ba8"),
}

_COMMAND_HE: dict[str, str] = {
    "open": "פתח",
    "find": "מצא",
    "next": "הבא",
    "enter": "אישור",
    "up": "למעלה",
    "down": "למטה",
    "close": "סגור",
}

_BG = "#1e1e2e"
_SURFACE = "#313244"
_RAW_COLOR = "#a6adc8"
_CORRECTED_COLOR = "#cdd6f4"
_ACCENT = "#89b4fa"
_FONT = "Segoe UI"
_WIDTH = 360
_PAD = 12

_CORNER_LABELS: dict[str, str] = {
    "top-right": "ימין למעלה",
    "top-left": "שמאל למעלה",
    "bottom-right": "ימין למטה",
    "bottom-left": "שמאל למטה",
}
_CORNER_BY_LABEL: dict[str, str] = {v: k for k, v in _CORNER_LABELS.items()}

_PROVIDER_LABELS: dict[str, str] = {
    "groq": "Groq (חינמי / מהיר)",
    "openai": "OpenAI",
    "custom": "שרת תואם OpenAI (מותאם אישית)",
    "disabled": "מושבת (אופליין בלבד)",
}
_PROVIDER_BY_LABEL: dict[str, str] = {v: k for k, v in _PROVIDER_LABELS.items()}


class OverlayHUD:
    """
    חלון צף עליון. queue מקבל (state, data) — ראה STATES וערכי data:
    text (raw), command, payload, corrected (מחרוזת מוכנה לשורה 3).
    """

    def __init__(
        self,
        q: "queue.Queue[tuple[str, dict]]",
        *,
        corner: str = "top-right",
        on_close: Optional[Callable[[], None]] = None,
        on_settings_saved: Optional[Callable[[object], None]] = None,
        hotkey_hint: str = "",
    ):
        if not _TK_AVAILABLE:
            raise RuntimeError("tkinter לא זמין")
        self._q = q
        self._corner = corner
        self._on_close = on_close
        self._on_settings_saved = on_settings_saved
        self._hotkey_hint = hotkey_hint
        self._root: Optional["tk.Tk"] = None
        self._status_label: Optional["tk.Label"] = None
        self._hint_label: Optional["tk.Label"] = None
        self._raw_label: Optional["tk.Label"] = None
        self._fixed_label: Optional["tk.Label"] = None
        self._settings_win: Optional["tk.Toplevel"] = None

    # --- בנייה ---

    def _build(self) -> None:
        root = tk.Tk()
        self._root = root
        root.title('עוזר קולי לפרויקט השו"ת')
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", 0.93)
        except Exception:
            pass
        root.configure(bg=_BG)

        frame = tk.Frame(root, bg=_BG, padx=_PAD, pady=8)
        frame.pack(fill="both", expand=True)

        # שורת כותרת: סטטוס + כפתורי הגדרות וסגירה
        header = tk.Frame(frame, bg=_BG)
        header.pack(fill="x")
        self._status_label = tk.Label(
            header, text="ממתין", fg=STATES["idle"][1], bg=_BG,
            font=(_FONT, 10, "bold"), anchor="e",
        )
        self._status_label.pack(side="right")

        close_btn = tk.Label(
            header, text="✕", fg="#6c7086", bg=_BG,
            font=(_FONT, 9), cursor="hand2", padx=6,
        )
        close_btn.pack(side="left")
        close_btn.bind("<Button-1>", lambda e: self._close())

        settings_btn = tk.Label(
            header, text="⚙", fg="#a6adc8", bg=_BG,
            font=(_FONT, 10), cursor="hand2", padx=4,
        )
        settings_btn.pack(side="left")
        settings_btn.bind("<Button-1>", lambda e: self._open_settings())

        hint_text = f"PTT: {self._hotkey_hint.upper()}" if self._hotkey_hint else ""
        self._hint_label = tk.Label(
            header, text=hint_text, fg="#6c7086", bg=_BG,
            font=(_FONT, 8), padx=6,
        )
        self._hint_label.pack(side="left")

        self._raw_label = tk.Label(
            frame, text="", fg=_RAW_COLOR, bg=_BG, font=(_FONT, 10),
            anchor="e", justify="right", wraplength=_WIDTH - 2 * _PAD,
        )
        self._raw_label.pack(fill="x", pady=(6, 2))

        self._fixed_label = tk.Label(
            frame, text="", fg=_CORRECTED_COLOR, bg=_BG,
            font=(_FONT, 11, "bold"), anchor="e", justify="right",
            wraplength=_WIDTH - 2 * _PAD,
        )
        self._fixed_label.pack(fill="x", pady=(0, 4))

        # גרירה מכל מקום על החלון (חוץ מכפתורי הפעולה)
        for widget in (root, frame, header, self._status_label, self._hint_label,
                       self._raw_label, self._fixed_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
        root.bind("<Escape>", lambda e: self._close())

        self._place()
        root.after(100, self._tick)

    def _place(self) -> None:
        assert self._root is not None
        self._root.update_idletasks()
        h = self._root.winfo_reqheight()
        margin = 20
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = sw - _WIDTH - margin if "right" in self._corner else margin
        y = margin if "top" in self._corner else sh - h - margin
        # רוחב קבוע — החלון לא "צומח" עם הטקסט ונופל מהצד
        self._root.geometry(f"{_WIDTH}x{h}")
        self._root.update_idletasks()
        x, y = self._clamp(x, y)
        self._root.geometry(f"+{x}+{y}")

    # --- שמירה על המסך ---

    def _screen_bounds(self) -> tuple[int, int, int, int]:
        """(x, y, w, h) של שטח התצוגה הווירטואלי — כל המסכים במערכת."""
        assert self._root is not None
        vx = self._root.winfo_vrootx()
        vy = self._root.winfo_vrooty()
        vw = self._root.winfo_vrootwidth()
        vh = self._root.winfo_vrootheight()
        if vw <= 0 or vh <= 0:
            vx, vy = 0, 0
            vw = self._root.winfo_screenwidth()
            vh = self._root.winfo_screenheight()
        return vx, vy, vw, vh

    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        """מגביל את המיקום כך שהחלון כולו יישאר בתוך המסך (לא נופל מהצדדים)."""
        assert self._root is not None
        w = self._root.winfo_width()
        h = self._root.winfo_height()
        if w <= 1:
            w = _WIDTH
        if h <= 1:
            h = self._root.winfo_reqheight()
        vx, vy, vw, vh = self._screen_bounds()
        margin = 2
        lo_x, hi_x = vx + margin, vx + vw - w - margin
        lo_y, hi_y = vy + margin, vy + vh - h - margin
        x = lo_x if hi_x < lo_x else min(max(x, lo_x), hi_x)
        y = lo_y if hi_y < lo_y else min(max(y, lo_y), hi_y)
        return int(x), int(y)

    def _keep_visible(self) -> None:
        """מחזיר את החלון למסך אם יצא ממנו (שינוי רזולוציה/גרירה)."""
        assert self._root is not None
        x, y = self._clamp(self._root.winfo_x(), self._root.winfo_y())
        if (x, y) != (self._root.winfo_x(), self._root.winfo_y()):
            self._root.geometry(f"+{x}+{y}")

    # --- גרירה ---

    def _drag_start(self, event) -> None:
        assert self._root is not None
        self._root._drag_x = event.x  # type: ignore[attr-defined]
        self._root._drag_y = event.y  # type: ignore[attr-defined]

    def _drag_move(self, event) -> None:
        assert self._root is not None
        x = self._root.winfo_x() + event.x - self._root._drag_x  # type: ignore[attr-defined]
        y = self._root.winfo_y() + event.y - self._root._drag_y  # type: ignore[attr-defined]
        x, y = self._clamp(x, y)
        self._root.geometry(f"+{x}+{y}")

    # --- עדכונים ---

    def _tick(self) -> None:
        if self._root is None:
            return
        self._keep_visible()
        while True:
            try:
                state, data = self._q.get_nowait()
            except queue.Empty:
                break
            self._apply(state, data)
        self._root.after(100, self._tick)

    def _apply(self, state: str, data: dict) -> None:
        label, color = STATES.get(state, ("…", "#6c7086"))
        if self._status_label is not None:
            self._status_label.config(text=label, fg=color)

        if self._raw_label is not None and "text" in data:
            self._raw_label.config(text=data.get("text") or "")

        if self._fixed_label is not None:
            corrected = data.get("corrected")
            if not corrected and data.get("command"):
                cmd_he = _COMMAND_HE.get(data["command"], data["command"])
                payload = data.get("payload") or ""
                corrected = f"{cmd_he} | {payload}" if payload else cmd_he
            if corrected is not None:
                self._fixed_label.config(text=corrected)

        # התאמת גובה החלון לתוכן תוך שמירה על רוחב קבוע
        if self._root is not None:
            self._root.update_idletasks()
            req_h = max(84, self._root.winfo_reqheight())
            x = self._root.winfo_x()
            y = self._root.winfo_y()
            self._root.geometry(f"{_WIDTH}x{req_h}")
            x, y = self._clamp(x, y)
            self._root.geometry(f"+{x}+{y}")

    # --- חלון הגדרות ---

    def _open_settings(self) -> None:
        if self._root is None:
            return
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        from app_settings import (
            APP_DISPLAY_NAME,
            APP_VERSION,
            PROVIDER_PRESETS,
            UserSettings,
            apply_settings,
            detect_responsa_exe,
            load_settings,
            save_settings,
        )

        cur = load_settings()
        win = tk.Toplevel(self._root)
        self._settings_win = win
        win.title(f"הגדרות — {APP_DISPLAY_NAME} v{APP_VERSION}")
        win.configure(bg=_BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)

        container = tk.Frame(win, bg=_BG, padx=16, pady=14)
        container.pack(fill="both", expand=True)

        def _lbl(parent, text: str, *, bold: bool = False, fg: str = _CORRECTED_COLOR) -> "tk.Label":
            return tk.Label(
                parent, text=text, bg=_BG, fg=fg,
                font=(_FONT, 10, "bold" if bold else "normal"),
                anchor="e", justify="right",
            )

        _lbl(container, "הגדרות כלליות", bold=True, fg=_ACCENT).pack(fill="x", pady=(0, 6))

        # מקש PTT
        row_ptt = tk.Frame(container, bg=_BG)
        row_ptt.pack(fill="x", pady=4)
        _lbl(row_ptt, ":מקש דיבור (PTT)").pack(side="right", padx=(8, 0))
        ptt_var = tk.StringVar(value=cur.ptt_hotkey)
        ptt_combo = ttk.Combobox(
            row_ptt, textvariable=ptt_var, width=22,
            values=("space", "ctrl+alt+v", "f9", "alt+v"),
        )
        ptt_combo.pack(side="left")

        # מיקום HUD
        row_corner = tk.Frame(container, bg=_BG)
        row_corner.pack(fill="x", pady=4)
        _lbl(row_corner, ":מיקום חלון צף").pack(side="right", padx=(8, 0))
        corner_var = tk.StringVar(value=_CORNER_LABELS.get(cur.hud_corner, _CORNER_LABELS["top-right"]))
        corner_combo = ttk.Combobox(
            row_corner, textvariable=corner_var, width=22, state="readonly",
            values=tuple(_CORNER_LABELS.values()),
        )
        corner_combo.pack(side="left")

        # משוב קולי TTS
        tts_var = tk.BooleanVar(value=cur.tts_enabled)
        tts_chk = tk.Checkbutton(
            container, text="הפעל משוב קולי בעברית (TTS)", variable=tts_var,
            bg=_BG, fg=_CORRECTED_COLOR, selectcolor=_SURFACE,
            activebackground=_BG, activeforeground=_CORRECTED_COLOR,
            font=(_FONT, 10), anchor="e",
        )
        tts_chk.pack(fill="x", pady=(6, 4))

        # נתיב פרויקט השו"ת
        _lbl(container, ':נתיב לתוכנת פרויקט השו"ת (RESPONSA.exe)').pack(fill="x", pady=(6, 2))
        row_exe = tk.Frame(container, bg=_BG)
        row_exe.pack(fill="x", pady=2)
        exe_var = tk.StringVar(value=cur.responsa_exe_path)
        exe_entry = tk.Entry(
            row_exe, textvariable=exe_var, width=38, bg=_SURFACE, fg=_CORRECTED_COLOR,
            insertbackground=_CORRECTED_COLOR, relief="flat", font=(_FONT, 9),
        )
        exe_entry.pack(side="left", fill="x", expand=True, ipady=4)

        def _browse_exe() -> None:
            chosen = filedialog.askopenfilename(
                parent=win,
                title="בחר RESPONSA.exe",
                filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
            )
            if chosen:
                exe_var.set(chosen)

        def _auto_exe() -> None:
            exe_var.set(detect_responsa_exe())

        tk.Button(
            row_exe, text="עיון…", command=_browse_exe,
            bg=_SURFACE, fg=_CORRECTED_COLOR, relief="flat", padx=8, font=(_FONT, 9),
        ).pack(side="right", padx=(6, 0))
        tk.Button(
            row_exe, text="זיהוי", command=_auto_exe,
            bg=_SURFACE, fg=_CORRECTED_COLOR, relief="flat", padx=8, font=(_FONT, 9),
        ).pack(side="right", padx=(6, 0))

        # מפריד
        tk.Frame(container, bg=_SURFACE, height=1).pack(fill="x", pady=12)

        _lbl(container, "סוכן AI חלופי (אופציונלי)", bold=True, fg=_ACCENT).pack(fill="x")
        _lbl(
            container,
            "המנוע הראשי פועל 100% אופליין ללא מפתח API. מפתח נדרש רק לתיקון פקודות משובשות במיוחד.",
            fg=_RAW_COLOR,
        ).pack(fill="x", pady=(2, 8))

        # ספק AI
        row_prov = tk.Frame(container, bg=_BG)
        row_prov.pack(fill="x", pady=4)
        _lbl(row_prov, ":ספק AI").pack(side="right", padx=(8, 0))
        prov_var = tk.StringVar(value=_PROVIDER_LABELS.get(cur.ai_provider, _PROVIDER_LABELS["groq"]))
        prov_combo = ttk.Combobox(
            row_prov, textvariable=prov_var, width=28, state="readonly",
            values=tuple(_PROVIDER_LABELS.values()),
        )
        prov_combo.pack(side="left")

        # API Key
        _lbl(container, ":מפתח API (נשמר מקומית בלבד ב-%APPDATA%)").pack(fill="x", pady=(6, 2))
        row_key = tk.Frame(container, bg=_BG)
        row_key.pack(fill="x", pady=2)
        key_var = tk.StringVar(value=cur.openai_api_key)
        key_entry = tk.Entry(
            row_key, textvariable=key_var, show="•", width=40,
            bg=_SURFACE, fg=_CORRECTED_COLOR, insertbackground=_CORRECTED_COLOR,
            relief="flat", font=(_FONT, 9),
        )
        key_entry.pack(side="left", fill="x", expand=True, ipady=4)
        showing = {"v": False}

        def _toggle_key() -> None:
            showing["v"] = not showing["v"]
            key_entry.config(show="" if showing["v"] else "•")
            show_btn.config(text="הסתר" if showing["v"] else "הצג")

        show_btn = tk.Button(
            row_key, text="הצג", command=_toggle_key,
            bg=_SURFACE, fg=_CORRECTED_COLOR, relief="flat", padx=8, font=(_FONT, 9),
        )
        show_btn.pack(side="right", padx=(6, 0))

        # Base URL + Model
        row_url = tk.Frame(container, bg=_BG)
        row_url.pack(fill="x", pady=4)
        _lbl(row_url, ":Base URL").pack(side="right", padx=(8, 0))
        url_var = tk.StringVar(value=cur.openai_base_url)
        url_entry = tk.Entry(
            row_url, textvariable=url_var, width=36, bg=_SURFACE, fg=_CORRECTED_COLOR,
            insertbackground=_CORRECTED_COLOR, relief="flat", font=(_FONT, 9),
        )
        url_entry.pack(side="left", ipady=3)

        row_model = tk.Frame(container, bg=_BG)
        row_model.pack(fill="x", pady=4)
        _lbl(row_model, ":מודל").pack(side="right", padx=(8, 0))
        model_var = tk.StringVar(value=cur.openai_model)
        model_entry = tk.Entry(
            row_model, textvariable=model_var, width=36, bg=_SURFACE, fg=_CORRECTED_COLOR,
            insertbackground=_CORRECTED_COLOR, relief="flat", font=(_FONT, 9),
        )
        model_entry.pack(side="left", ipady=3)

        def _on_provider_change(_event=None) -> None:
            code = _PROVIDER_BY_LABEL.get(prov_var.get(), "groq")
            preset_url, preset_model = PROVIDER_PRESETS.get(code, ("", ""))
            if code in ("groq", "openai", "disabled"):
                url_var.set(preset_url)
                model_var.set(preset_model)

        prov_combo.bind("<<ComboboxSelected>>", _on_provider_change)

        # כפתורי שמירה / ביטול
        btns = tk.Frame(container, bg=_BG)
        btns.pack(fill="x", pady=(14, 0))

        def _save() -> None:
            new_hotkey = ptt_var.get().strip() or "space"
            new_corner = _CORNER_BY_LABEL.get(corner_var.get(), "top-right")
            new_prov = _PROVIDER_BY_LABEL.get(prov_var.get(), "groq")
            updated = UserSettings(
                responsa_exe_path=exe_var.get().strip() or detect_responsa_exe(),
                ptt_hotkey=new_hotkey,
                hud_corner=new_corner,
                tts_enabled=bool(tts_var.get()),
                ai_provider=new_prov,
                openai_api_key=key_var.get().strip(),
                openai_base_url=url_var.get().strip(),
                openai_model=model_var.get().strip(),
            )
            try:
                save_settings(updated)
                apply_settings(updated, explicit_save=True)
            except Exception as e:
                messagebox.showerror("שגיאה", f"לא ניתן לשמור הגדרות:\n{e}", parent=win)
                return

            self._corner = updated.hud_corner
            self._hotkey_hint = updated.ptt_hotkey
            if self._hint_label is not None:
                self._hint_label.config(text=f"PTT: {updated.ptt_hotkey.upper()}")
            self._place()

            if self._on_settings_saved is not None:
                try:
                    self._on_settings_saved(updated)
                except Exception as e:
                    messagebox.showwarning("אזהרה", f"ההגדרות נשמרו, אך עדכון חי נכשל:\n{e}", parent=win)

            win.destroy()

        tk.Button(
            btns, text="שמור", command=_save,
            bg=_ACCENT, fg="#11111b", relief="flat", padx=18, pady=5,
            font=(_FONT, 10, "bold"), cursor="hand2",
        ).pack(side="right")
        tk.Button(
            btns, text="ביטול", command=win.destroy,
            bg=_SURFACE, fg=_CORRECTED_COLOR, relief="flat", padx=14, pady=5,
            font=(_FONT, 10), cursor="hand2",
        ).pack(side="right", padx=(0, 8))

        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        win.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 2}")
        win.focus_force()

    # --- חיים ---

    def run(self) -> None:
        """בונה את החלון ומריץ mainloop (חוסם עד סגירה)."""
        self._build()
        assert self._root is not None
        try:
            self._root.mainloop()
        finally:
            self._root = None

    def stop(self) -> None:
        if self._root is not None:
            try:
                self._root.quit()
                self._root.destroy()
            except Exception:
                pass
            self._root = None

    def _close(self) -> None:
        self.stop()
        if self._on_close is not None:
            self._on_close()


# ============================================================
# CLI — הדגמה עצמאית
# ============================================================

def _demo() -> int:
    import threading
    import time

    q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    hud = OverlayHUD(q)

    samples = [
        ("listening", {}),
        ("transcribing", {"text": "תפחי ברכות אלף"}),
        ("understanding", {"text": "תפחי ברכות אלף"}),
        ("executing", {"text": "תפחי ברכות אלף",
                       "command": "open", "payload": "ברכות א"}),
        ("idle", {"text": "תפחי ברכות אלף",
                  "command": "open", "payload": "ברכות א"}),
        ("error", {"text": "…", "corrected": "הפעולה נכשלה"}),
    ]

    def feed() -> None:
        for state, data in samples:
            q.put((state, dict(data)))
            time.sleep(1.5)

    threading.Thread(target=feed, daemon=True).start()
    hud.run()
    return 0


def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="HUD צף של העוזר הקולי")
    parser.add_argument("--demo", action="store_true", help="הדגמת מצבים")
    args = parser.parse_args()

    if args.demo:
        if not _TK_AVAILABLE:
            print("[HUD] tkinter לא זמין")
            return 1
        return _demo()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
