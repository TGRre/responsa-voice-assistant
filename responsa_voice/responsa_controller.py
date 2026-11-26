# -*- coding: utf-8 -*-
"""
responsa_controller.py — מנוע שליטה בתוכנת פרויקט השו"ת.

6 פקודות קוליות → צירופי מקשים פשוטים.
מקל על ניהול פוקוס, הזרקת טקסט עברי דרך clipboard, ואיפוס חירום.

תומך ב-Python 3.11+
דרישות: pip install pywin32 pyperclip keyboard psutil
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import win32gui
    import win32con
    import win32process
    import win32api
except ImportError:
    print("[ERROR] נדרש pywin32: pip install pywin32")
    sys.exit(1)

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import keyboard
except ImportError:
    keyboard = None

import config as _config
from config import (
    RESPONSA_PROCESS_NAME,
    RESPONSA_WINDOW_TITLE_PATTERNS,
    RESPONSA_WINDOW_CLASS,
    ERROR_WINDOW_PATTERNS,
    VOICE_COMMANDS,
    AUTOMATION_CONFIG,
)


# ============================================================
# פונקציות עזר גלובליות
# ============================================================

def _window_title_matches(title: str, patterns: list[str]) -> bool:
    if not title:
        return False
    if "שורת המשימות" in title or "taskbar" in title.lower() or "program manager" in title.lower():
        return False
    for pattern in patterns:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False


def _get_all_visible_windows() -> list[dict]:
    windows = []

    def _callback(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "class_name": win32gui.GetClassName(hwnd),
                        "pid": pid,
                    })
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_callback, None)
    return windows


# ============================================================
# ResponsaController
# ============================================================

class ResponsaController:
    """מנוע שליטה פשוט: פוקוס + שליחת מקשים + clipboard."""

    def __init__(self):
        self._main_hwnd: Optional[int] = None
        self._pid: Optional[int] = None
        self._connected = False

    # --- חיבור ---

    def connect(self) -> bool:
        """מתחבר לחלון הראשי של פרויקט השו\"ת."""
        # שיטה 1: לפי PID
        try:
            pid = None
            if psutil:
                for proc in psutil.process_iter(["pid", "name"]):
                    if proc.info["name"] and proc.info["name"].lower() == (RESPONSA_PROCESS_NAME + ".exe").lower():
                        pid = proc.info["pid"]
                        break
            if pid is None:
                raise RuntimeError("תהליך לא נמצא")
            self._pid = pid
            # חיפוש החלון לפי class
            for win_info in _get_all_visible_windows():
                if win_info["class_name"] == RESPONSA_WINDOW_CLASS:
                    self._main_hwnd = win_info["hwnd"]
                    self._connected = True
                    print(f"[CONNECTED] pid={pid}, hwnd={self._main_hwnd}")
                    return True
        except Exception as e:
            print(f"[DEBUG] חיבור PID: {e}")

        # שיטה 2: לפי כותרת
        for win_info in _get_all_visible_windows():
            if _window_title_matches(win_info["title"], RESPONSA_WINDOW_TITLE_PATTERNS):
                self._main_hwnd = win_info["hwnd"]
                self._pid = win_info["pid"]
                self._connected = True
                print(f"[CONNECTED] '{win_info['title']}'")
                return True

        print("[FAILED] לא ניתן להתחבר לפרויקט השו\"ת")
        return False

    # --- פוקוס ---

    def ensure_active_and_clean(self, cleanup: bool = True) -> bool:
        """מוודא שהתוכנה פעילה ובקדמת המסך. cleanup=True גם סוגר חלונות שגיאה."""
        if not self._is_process_alive():
            print("[WARNING] תהליך לא חי — מנסה להפעיל...")
            if not self._launch_process():
                return False
            time.sleep(3)

        if not self._connected or not self._main_hwnd:
            if not self.connect():
                return False

        if cleanup:
            self._dismiss_error_dialogs()

        if not self._bring_to_front():
            return False

        return self._verify_focus()

    def _is_process_alive(self) -> bool:
        if psutil:
            try:
                for proc in psutil.process_iter(["name"]):
                    if proc.info["name"] and RESPONSA_PROCESS_NAME.lower() in proc.info["name"].lower():
                        return True
            except Exception:
                pass
        for win_info in _get_all_visible_windows():
            if _window_title_matches(win_info["title"], RESPONSA_WINDOW_TITLE_PATTERNS):
                return True
        return False

    def _launch_process(self) -> bool:
        try:
            exe_path = _config.RESPONSA_EXE_PATH
            if not Path(exe_path).exists():
                try:
                    from app_settings import detect_responsa_exe
                    detected = detect_responsa_exe()
                    if Path(detected).exists():
                        exe_path = detected
                except Exception:
                    pass
            if not Path(exe_path).exists():
                print(f"[ERROR] קובץ לא נמצא: {exe_path}")
                return False
            win32api.ShellExecute(0, "open", exe_path, "", "", win32con.SW_SHOWNORMAL)
            for _ in range(30):
                time.sleep(1)
                if self._is_process_alive():
                    return True
            return False
        except Exception as e:
            print(f"[ERROR] שגיאת הפעלה: {e}")
            return False

    def _bring_to_front(self) -> bool:
        try:
            if not self._main_hwnd:
                return False
            if win32gui.IsIconic(self._main_hwnd):
                win32gui.ShowWindow(self._main_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self._main_hwnd)
            time.sleep(0.1)
            return win32gui.GetForegroundWindow() == self._main_hwnd
        except Exception:
            try:
                win32gui.ShowWindow(self._main_hwnd, win32con.SW_SHOW)
                win32gui.SetForegroundWindow(self._main_hwnd)
                return True
            except Exception:
                return False

    def _verify_focus(self) -> bool:
        try:
            return win32gui.GetForegroundWindow() == self._main_hwnd
        except Exception:
            return False

    # --- חלונות שגיאה ---

    def _dismiss_error_dialogs(self) -> int:
        closed = 0
        for win_info in _get_all_visible_windows():
            title = win_info["title"]
            is_dialog = win_info["class_name"] == "#32770"
            is_error = _window_title_matches(title, ERROR_WINDOW_PATTERNS)
            if is_dialog or is_error:
                print(f"[CLEANUP] סוגר: '{title}'")
                self._close_dialog(win_info["hwnd"])
                closed += 1
        return closed

    def _close_dialog(self, hwnd: int):
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.2)
            if win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
        except Exception:
            pass

    # --- שליחת מקשים ---

    def _send_key(self, key: str) -> bool:
        """שולח מקש או צירוף מקשים (keyboard.press_and_release)."""
        if not keyboard:
            print("[ERROR] keyboard לא מותקן")
            return False
        try:
            keyboard.press_and_release(key)
            time.sleep(AUTOMATION_CONFIG.action_delay)
            return True
        except Exception as e:
            print(f"[ERROR] שגיאת מקש '{key}': {e}")
            return False

    def _paste_text(self, text: str) -> bool:
        """העתקה ל-clipboard + הדבקה (בטוח לטקסט עברי)."""
        if not pyperclip:
            print("[ERROR] pyperclip לא מותקן")
            return False
        try:
            pyperclip.copy(text)
            time.sleep(AUTOMATION_CONFIG.clipboard_settle_time)
            return self._send_key("ctrl+v")
        except Exception as e:
            print(f"[ERROR] שגיאת הדבקה: {e}")
            return False

    # --- פקודות קוליות ---

    def execute_command(self, command: str, payload: str = "") -> bool:
        """
        מבצע פקודה קולית: מוודא פוקוס, שולח רצף מקשים.

        Args:
            command: שם הפקודה (open, find, next, enter, up, down)
            payload: טקסט להדבקה (ל-open ו-find בלבד)

        Returns:
            True אם הפקודה בוצעה
        """
        cmd = VOICE_COMMANDS.get(command)
        if not cmd:
            print(f"[ERROR] פקודה לא מוכרת: {command}")
            return False

        if not self.ensure_active_and_clean(cleanup=(command == "open")):
            return False

        try:
            for step in cmd["key_sequence"]:
                if step == "PASTE":
                    if not self._paste_text(payload):
                        return False
                elif step.startswith("wait:"):
                    time.sleep(float(step.split(":", 1)[1]))
                else:
                    if not self._send_key(step):
                        return False

            print(f"[CMD] {command}" + (f" '{payload}'" if payload else ""))
            return True

        except Exception as e:
            print(f"[ERROR] שגיאת ביצוע '{command}': {e}")
            self.emergency_reset()
            return False

    # --- איפוס חירום ---

    def emergency_reset(self) -> bool:
        """איפוס חירום: Esc + סגירת חלונות שגיאה + החזרת פוקוס."""
        print("[EMERGENCY] איפוס חירום...")
        try:
            for _ in range(5):
                if keyboard:
                    keyboard.press_and_release("escape")
                time.sleep(0.15)

            self._dismiss_error_dialogs()

            if not self._connected:
                self.connect()

            self._bring_to_front()
            time.sleep(0.2)

            if self._verify_focus():
                print("[EMERGENCY] איפוס הושלם")
                return True
            else:
                print("[WARNING] איפוס חלקי — פוקוס לא אומת")
                return False
        except Exception as e:
            print(f"[ERROR] שגיאת איפוס: {e}")
            return False

    # --- סטטוס ---

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "process_alive": self._is_process_alive(),
            "has_focus": self._verify_focus() if self._connected else False,
            "main_hwnd": self._main_hwnd,
        }


# ============================================================
# פונקציות נוחות
# ============================================================

def create_controller() -> ResponsaController:
    controller = ResponsaController()
    if controller.connect():
        return controller
    print("[WARNING] הבקר נוצר ללא חיבור")
    return controller


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="שליטה בפרויקט השו\"ת")
    parser.add_argument("--connect", action="store_true", help="התחברות")
    parser.add_argument("--status", action="store_true", help="סטטוס")
    parser.add_argument("--reset", action="store_true", help="איפוס חירום")
    parser.add_argument("--cmd", type=str, help="פקודה (open, find, next, enter, up, down)")
    parser.add_argument("--payload", type=str, default="", help=" payload לפקודה")
    args = parser.parse_args()

    controller = ResponsaController()

    if args.connect:
        controller.connect()

    if args.status:
        for k, v in controller.get_status().items():
            print(f"  {k}: {v}")

    if args.cmd:
        ok = controller.execute_command(args.cmd, args.payload)
        print(f"[{'OK' if ok else 'FAIL'}] {args.cmd}")

    if args.reset:
        ok = controller.emergency_reset()
        print(f"[{'OK' if ok else 'FAIL'}] איפוס")
