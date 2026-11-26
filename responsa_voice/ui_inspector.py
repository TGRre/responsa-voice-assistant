# -*- coding: utf-8 -*-
"""
ui_inspector.py — כלי דיאגנוסטיקה עצמאי לסריקת עץ הפקדים של פרויקט השו"ת.

מיועד לשלב הראשון: להבין את מבנה הממשק לפני כתיבת מנוע השליטה.

שימוש:
    python ui_inspector.py --dump              סריקה מלאה + כתיבה ל-JSON
    python ui_inspector.py --capture           הקלטת פקד חי (שים פוקוס ולחץ Enter)
    python ui_inspector.py --find "טקסט"       חיפוש פקד לפי טקסט/שם
    python ui_inspector.py --windows           רשימת כל החלונות הפתוחים
    python ui_inspector.py --dump --backend win32   סריקה עם בקאנד ספציפי

תומך ב-Python 3.11+, דורש: pip install pywinauto pywin32
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import win32gui
    import win32process
    import win32con
except ImportError:
    print("[ERROR] נדרש pywin32: pip install pywin32")
    sys.exit(1)

try:
    from pywinauto import Application, Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.controls.hwndwrapper import HwndWrapper
except ImportError:
    print("[ERROR] נדרש pywinauto: pip install pywinauto")
    sys.exit(1)

# ייבוא הגדרות מ-config
try:
    from config import (
        RESPONSA_PROCESS_NAME,
        RESPONSA_WINDOW_TITLE_PATTERNS,
        RESPONSA_EXE_PATH,
        RESPONSA_WINDOW_CLASS,
    )
except ImportError:
    # Fallback אם config לא נמצא
    RESPONSA_PROCESS_NAME = "RESPONSA"
    RESPONSA_WINDOW_TITLE_PATTERNS = [r"השו.?ת", r"responsa"]
    RESPONSA_EXE_PATH = r"C:\Program Files (x86)\ResponsaCD33\RESPONSA.exe"
    RESPONSA_WINDOW_CLASS = "ResponsaProject"


# ============================================================
# פונקציות עזר לזיהוי חלונות
# ============================================================

def _window_matches_title(title: str, patterns: list[str]) -> bool:
    """בודק אם כותרת החלון מתאימה לאחת התבניות (מדיר taskbar)."""
    if not title:
        return False
    # חלונות שאינם המטרה — "שורת המשימות" (taskbar) התאמה ל-שו.?ת
    if "שורת המשימות" in title or "taskbar" in title.lower() or "program manager" in title.lower():
        return False
    for pattern in patterns:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False
    for pattern in patterns:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False


def _get_window_pid(hwnd: int) -> Optional[int]:
    """מחזיר את ה-PID של החלון."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def _get_window_class(hwnd: int) -> str:
    """מחזיר את שם המחלקה של החלון."""
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _get_window_title(hwnd: int) -> str:
    """מחזיר את כותרת החלון."""
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _is_window_visible(hwnd: int) -> bool:
    """בודק אם החלון גלוי."""
    try:
        return win32gui.IsWindowVisible(hwnd)
    except Exception:
        return False


def _enum_all_windows() -> list[dict]:
    """מרחיב את כל החלונות הגלויים במערכת."""
    windows = []

    def _callback(hwnd, _):
        if _is_window_visible(hwnd):
            title = _get_window_title(hwnd)
            if title:  # רק חלונות עם כותרת
                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "class_name": _get_window_class(hwnd),
                    "pid": _get_window_pid(hwnd),
                    "rect": win32gui.GetWindowRect(hwnd),
                })
        return True

    win32gui.EnumWindows(_callback, None)
    return windows


# ============================================================
# מחלקה ראשית: ResponsaUIInspector
# ============================================================

class ResponsaUIInspector:
    """
    סורק עצמאי של עץ הפקדים בפרויקט השו"ת.

    מתחבר לתהליך הפעיל, סורק בשני בקאנדים (UIA + Win32),
    ומייצא JSON מפורט עם כל הפקדים.
    """

    def __init__(
        self,
        process_name: str = RESPONSA_PROCESS_NAME,
        title_patterns: Optional[list[str]] = None,
    ):
        self.process_name = process_name
        self.title_patterns = title_patterns or RESPONSA_WINDOW_TITLE_PATTERNS
        self._app: Optional[Application] = None
        self._main_window = None
        self._connected = False

    # --- חיבור ---

    def connect(self) -> bool:
        """
        מאתר ומתחבר לחלון הראשי של פרויקט השו"ת.

        Returns:
            True אם החיבור הצליח
        """
        # שיטה 1: חיבור לפי PID (process= דורשת PID, לא שם)
        try:
            import psutil
            pid = None
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and proc.info["name"].lower() == (self.process_name + ".exe").lower():
                    pid = proc.info["pid"]
                    break
            if pid is None:
                raise RuntimeError(f"תהליך {self.process_name} לא נמצא")
            self._app = Application(backend="uia").connect(process=pid, timeout=5)
            # class_name מהיר ואמין (title_re עם alternation נתקע ב-UIA)
            self._main_window = self._app.window(class_name=RESPONSA_WINDOW_CLASS)
            self._main_window.wait("exists", timeout=5)
            self._connected = True
            print(f"[OK] מחובר ל-{self.process_name} (pid={pid})")
            return True
        except Exception as e:
            print(f"[DEBUG] חיבור לפי PID נכשל: {e}")

        # שיטה 3: חיבור לפי חיפוש חלונות — סינון לפי class + כותרת (מונע תפיסת taskbar)
        try:
            desktop = Desktop(backend="uia")
            for win in desktop.windows():
                try:
                    class_name = win.class_name() or ""
                except Exception:
                    class_name = ""
                title = win.window_text()
                # סינון מחמיר: מחלקת חלון תואמת, או כותרת תואמת שאינה taskbar
                if class_name == RESPONSA_WINDOW_CLASS or (
                    _window_matches_title(title, self.title_patterns)
                    and "שורת המשימות" not in title
                    and "taskbar" not in title.lower()
                ):
                    self._main_window = win
                    self._connected = True
                    print(f"[OK] מחובר לחלון: '{title}' (class={class_name})")
                    return True
        except Exception as e:
            print(f"[DEBUG] חיבור לפי כותרת נכשל: {e}")

        print("[FAIL] לא ניתן להתחבר לחלון פרויקט השו\"ת")
        print("       ודא שהתוכנה פתוחה ונסה שוב")
        return False

    def _pattern_to_regex(self) -> str:
        """ממיר את רשימת התבניות ל-regex יחיד."""
        return "|".join(self.title_patterns)

    # --- סריקה מלאה ---

    def dump_full_tree(self, backend: str = "uia") -> dict:
        """
        סורק את עץ הפקדים המלא של החלון הראשי.

        Args:
            backend: "uia" או "win32"

        Returns:
            dict עם כל הפקדים
        """
        if not self._connected:
            if not self.connect():
                return {"error": "לא התחבר לתוכנה"}

        result = {
            "timestamp": datetime.now().isoformat(),
            "process": self.process_name,
            "backend": backend,
            "window_title": self._main_window.window_text(),
            "window_rect": self._get_rect_dict(self._main_window),
            "controls": [],
            "summary": {},
        }

        try:
            if backend == "uia":
                controls = self._scan_uia()
            else:
                controls = self._scan_win32()
            result["controls"] = controls
            result["summary"] = self._build_summary(controls)
        except Exception as e:
            result["error"] = str(e)

        return result

    def _get_rect_dict(self, control) -> dict:
        """מחזיר את מלב הפקד כ-dict."""
        try:
            rect = control.rectangle()
            return {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.width(),
                "height": rect.height(),
            }
        except Exception:
            return {}

    def _scan_uia(self) -> list[dict]:
        """סורק את עץ הפקדים דרך UIA (מומלץ)."""
        controls = []
        try:
            # קבלת כל הפקדים מהחלון הראשי
            all_controls = self._main_window.descendants()
            for i, ctrl in enumerate(all_controls):
                try:
                    info = self._extract_control_info_uia(ctrl, depth=0, index=i)
                    if info:
                        controls.append(info)
                except Exception:
                    continue
        except Exception as e:
            print(f"[ERROR] שגיאת סריקת UIA: {e}")

        return controls

    def _scan_win32(self) -> list[dict]:
        """סורק את עץ הפקדים דרך Win32 (מהיר יותר, פחות מידע)."""
        controls = []
        try:
            hwnd = self._main_window.handle
            self._enum_child_windows(hwnd, controls, depth=0)
        except Exception as e:
            print(f"[ERROR] שגיאת סריקת Win32: {e}")

        return controls

    def _enum_child_windows(self, hwnd: int, controls: list, depth: int):
        """מרחיב את כל ילדי החלון (Win32)."""
        children: list[int] = []

        def _callback(child_hwnd, _):
            children.append(child_hwnd)
            return True

        try:
            win32gui.EnumChildWindows(hwnd, _callback, None)
        except Exception:
            pass

        for child_hwnd in children:
            try:
                info = {
                    "hwnd": child_hwnd,
                    "class_name": _get_window_class(child_hwnd),
                    "text": win32gui.GetWindowText(child_hwnd),
                    "visible": _is_window_visible(child_hwnd),
                    "enabled": win32gui.IsWindowEnabled(child_hwnd),
                    "rect": win32gui.GetWindowRect(child_hwnd),
                    "depth": depth,
                    "control_id": win32gui.GetDlgCtrlID(child_hwnd),
                    "backend": "win32",
                }
                controls.append(info)
                # EnumChildWindows כבר ממפה את כל הצאצאים — אין רקורסיה (מונע כפילויות)
            except Exception:
                continue

    def _extract_control_info_uia(self, ctrl, depth: int, index: int) -> Optional[dict]:
        """מחלץ מידע מפקד בודד (UIA)."""
        try:
            elem = ctrl.element_info
            info = {
                "index": index,
                "control_type": elem.control_type or "Unknown",
                "class_name": elem.class_name or "",
                "automation_id": elem.automation_id or "",
                "name": elem.name or "",
                "rect": self._get_rect_dict(ctrl),
                "is_focusable": elem.is_focusable if hasattr(elem, "is_focusable") else False,
                "is_enabled": elem.enabled if hasattr(elem, "enabled") else True,
                "is_visible": elem.visible if hasattr(elem, "visible") else True,
                "depth": depth,
                "backend": "uia",
                "pywinauto_selector": self._build_selector_uia(ctrl),
            }

            # מידע נוסף לפקדי עריכה
            if info["control_type"] == "Edit":
                try:
                    info["current_value"] = ctrl.window_text()
                except Exception:
                    info["current_value"] = ""

            # מידע נוסף לפקדי לחצן
            if info["control_type"] == "Button":
                try:
                    info["is_toggle"] = ctrl.get_toggle_state() is not None
                except Exception:
                    info["is_toggle"] = False

            return info

        except Exception:
            return None

    def _build_selector_uia(self, ctrl) -> str:
        """בונה pywinauto selector עבור פקד."""
        try:
            elem = ctrl.element_info
            parts = []

            if elem.automation_id:
                parts.append(f"auto_id='{elem.automation_id}'")
            elif elem.name:
                parts.append(f"title='{elem.name}'")
            elif elem.class_name:
                parts.append(f"class_name='{elem.class_name}'")

            if elem.control_type:
                parts.append(f"control_type='{elem.control_type}'")

            if parts:
                return f"child_window({', '.join(parts)})"
            return "child_window()"
        except Exception:
            return ""

    def _build_summary(self, controls: list[dict]) -> dict:
        """בונה סיכום של הפקדים לפי סוג."""
        summary = {}
        for ctrl in controls:
            ctype = ctrl.get("control_type", ctrl.get("class_name", "Unknown"))
            summary[ctype] = summary.get(ctype, 0) + 1
        return summary

    # --- כתיבה ל-JSON ---

    def dump_to_json(self, filepath: str = "responsa_ui_tree.json", backend: str = "uia") -> str:
        """
        סורק וכותב את עץ הפקדים לקובץ JSON.

        Args:
            filepath: נתיב הקובץ
            backend: "uia" או "win32"

        Returns:
            נתיב הקובץ שנכתב
        """
        data = self.dump_full_tree(backend)
        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        n_controls = len(data.get("controls", []))
        print(f"[OK] נכתב קובץ: {output_path} ({n_controls} פקדים)")
        return str(output_path)

    # --- חיפוש פקדים ---

    def find_controls_by_type(self, control_type: str, backend: str = "uia") -> list[dict]:
        """
        מחפש פקדים לפי סוג (Edit, Button, ComboBox, TreeItem...).

        Args:
            control_type: סוג הפקד
            backend: בקאנד לסריקה

        Returns:
            רשימת פקדים תואמים
        """
        data = self.dump_full_tree(backend)
        return [
            c for c in data.get("controls", [])
            if control_type.lower() in (c.get("control_type", "") or "").lower()
            or control_type.lower() in (c.get("class_name", "") or "").lower()
        ]

    def find_controls_by_name(self, pattern: str, backend: str = "uia") -> list[dict]:
        """
        מחפש פקדים לפי Regex על שם/טקסט.

        Args:
            pattern: תבנית Regex
            backend: בקאנד לסריקה

        Returns:
            רשימת פקדים תואמים
        """
        data = self.dump_full_tree(backend)
        results = []
        regex = re.compile(pattern, re.IGNORECASE)
        for ctrl in data.get("controls", []):
            name = ctrl.get("name", "") or ctrl.get("text", "")
            if regex.search(name):
                results.append(ctrl)
        return results

    # --- הקלטת פקד חי ---

    def live_capture(self, timeout: float = 30.0) -> Optional[dict]:
        """
        "הקלטת פקד חי": המשתמש שם פוקוס על פקד, הסקריפט מזהה אותו.

        מדפיס את ה-selector המדויק ב-pywinauto.

        Args:
            timeout: זמן מקסימלי המתנה בשניות

        Returns:
            dict עם פרטי הפקד, או None
        """
        print("\n" + "=" * 60)
        print("  הקלטת פקד חי")
        print("=" * 60)
        print("שים את הפוקוס (עכבר/מקלדת) על הפקד הרצוי")
        print("בתוך תוכנת פרויקט השו\"ת...")
        print(f"זמן מקסימלי: {timeout} שניות")
        print("להפסקה: Ctrl+C")
        print("=" * 60 + "\n")

        start = time.time()
        last_hwnd = None

        while time.time() - start < timeout:
            try:
                # קבלת החלון הפעיל
                foreground = win32gui.GetForegroundWindow()
                if not foreground:
                    time.sleep(0.3)
                    continue

                fg_title = _get_window_title(foreground)
                fg_class = _get_window_class(foreground)

                # בדיקה אם זה חלון של השו"ת
                is_responsa = (
                    _window_matches_title(fg_title, self.title_patterns)
                    or self._is_responsa_hwnd(foreground)
                )

                if not is_responsa:
                    time.sleep(0.3)
                    continue

                # קבלת הפקד הממוקד
                focused = self._get_focused_control(foreground)
                if focused and focused != last_hwnd:
                    last_hwnd = focused
                    info = self._describe_control_live(focused)
                    if info:
                        print("\n" + "-" * 60)
                        print("  נקלט פקד:")
                        print("-" * 60)
                        for key, value in info.items():
                            print(f"  {key:25s}: {value}")
                        print("-" * 60)
                        print(f"\n  pywinauto selector: {info.get('pywinauto_selector', 'N/A')}")
                        print("-" * 60 + "\n")

                        # שמירה לקובץ
                        self._save_captured_control(info)
                        return info

                time.sleep(0.3)

            except KeyboardInterrupt:
                print("\n[INFO] הקלטה הופסקה על ידי המשתמש")
                return None
            except Exception as e:
                print(f"[DEBUG] שגיאה: {e}")
                time.sleep(0.5)

        print(f"\n[TIMEOUT] עבר {timeout} שניות ללא קליטה")
        return None

    def _is_responsa_hwnd(self, hwnd: int) -> bool:
        """בודק אם ה-handle שייך לתהליך השו"ת."""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32process.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
            _, exe_path = win32process.GetModuleFileNameEx(handle, 0)
            win32process.CloseHandle(handle)
            return self.process_name.lower() in exe_path.lower()
        except Exception:
            return False

    def _get_focused_control(self, hwnd: int) -> Optional[int]:
        """
        מאתר את ה-handle של הפקד הממוקד בחלון.

        משתמש ב-GetGUIThreadInfo.
        """
        try:
            # קבלת thread ID של החלון
            thread_id, _ = win32process.GetWindowThreadProcessId(hwnd)

            # GetGUIThreadInfo
            import ctypes
            from ctypes import wintypes

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)

            # קריאה ל-GetGUIThreadInfo
            user32 = ctypes.windll.user32
            result = user32.GetGUIThreadInfo(thread_id, ctypes.byref(gti))

            if result and gti.hwndFocus:
                return gti.hwndFocus

            # fallback: החלון הפעיל עצמו
            return hwnd

        except Exception:
            return hwnd

    def _describe_control_live(self, hwnd: int) -> Optional[dict]:
        """מתאר פקד לפי ה-handle שלו."""
        try:
            info = {
                "hwnd": hwnd,
                "class_name": _get_window_class(hwnd),
                "text": _get_window_title(hwnd),
                "rect": win32gui.GetWindowRect(hwnd),
                "control_id": win32gui.GetDlgCtrlID(hwnd),
                "visible": _is_window_visible(hwnd),
                "enabled": win32gui.IsWindowEnabled(hwnd),
            }

            # ניסיון לקבל מידע UIA
            try:
                app = Application(backend="uia").connect(handle=hwnd)
                ctrl = app.window(handle=hwnd)
                elem = ctrl.element_info
                info["control_type"] = elem.control_type or "Unknown"
                info["automation_id"] = elem.automation_id or ""
                info["name"] = elem.name or ""
                info["pywinauto_selector"] = self._build_selector_uia(ctrl)
            except Exception:
                info["control_type"] = info.get("class_name", "Unknown")
                info["pywinauto_selector"] = (
                    f"window(handle={hwnd})"
                )

            return info

        except Exception as e:
            print(f"[ERROR] לא ניתן לתאר פקד: {e}")
            return None

    def _save_captured_control(self, info: dict):
        """שומר פקד שנקלט לקובץ JSON."""
        filepath = Path("captured_controls.json")
        existing = []
        if filepath.exists():
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        existing.append({
            **info,
            "captured_at": datetime.now().isoformat(),
        })

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        print(f"[OK] נשמר ל-{filepath}")

    # --- כל החלונות ---

    def list_windows(self) -> list[dict]:
        """מחזיר את כל החלונות הגלויים במערכת."""
        return _enum_all_windows()

    # --- פלט למסך ---

    def print_control_info(self, control: dict):
        """מדפיס פרטי פקד בצורה מסודרת."""
        print("-" * 60)
        for key, value in control.items():
            if key == "rect" and isinstance(value, dict):
                print(f"  {key:25s}: {value.get('width', '?')}x{value.get('height', '?')} "
                      f"@ ({value.get('left', '?')}, {value.get('top', '?')})")
            else:
                print(f"  {key:25s}: {value}")
        print("-" * 60)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="כלי דיאגנוסטיקה לסריקת עץ הפקדים של פרויקט השו\"ת",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות שימוש:
  python ui_inspector.py --dump                     סריקה מלאה ל-JSON
  python ui_inspector.py --dump --backend win32     סריקה עם Win32
  python ui_inspector.py --capture                  הקלטת פקד חי
  python ui_inspector.py --find "חיפוש"             חיפוש פקד
  python ui_inspector.py --find-edit                רשימת שדות עריכה
  python ui_inspector.py --find-button              רשימת כפתורים
  python ui_inspector.py --windows                  כל החלונות הפתוחים
        """,
    )

    parser.add_argument(
        "--dump", action="store_true",
        help="סריקה מלאה של עץ הפקדים + כתיבה ל-JSON",
    )
    parser.add_argument(
        "--capture", action="store_true",
        help="הקלטת פקד חי (שים פוקוס על הפקד)",
    )
    parser.add_argument(
        "--find", type=str, metavar="PATTERN",
        help="חיפוש פקד לפי Regex על טקסט/שם",
    )
    parser.add_argument(
        "--find-edit", action="store_true",
        help="רשימת כל שדות העריכה (Edit)",
    )
    parser.add_argument(
        "--find-button", action="store_true",
        help="רשימת כל הכפתורים (Button)",
    )
    parser.add_argument(
        "--windows", action="store_true",
        help="רשימת כל החלונות הגלויים במערכת",
    )
    parser.add_argument(
        "--backend", choices=["uia", "win32"], default="uia",
        help="בקאנד לסריקה (ברירת מחדל: uia)",
    )
    parser.add_argument(
        "--output", type=str, default="responsa_ui_tree.json",
        help="נתיב קובץ ה-JSON (ברירת מחדל: responsa_ui_tree.json)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="זמן המתנה להקלטה חיה בשניות (ברירת מחדל: 30)",
    )

    args = parser.parse_args()

    inspector = ResponsaUIInspector()

    # --- רשימת חלונות ---
    if args.windows:
        windows = inspector.list_windows()
        print(f"\nנמצאו {len(windows)} חלונות גלויים:\n")
        for w in windows:
            match = "✓" if _window_matches_title(w["title"], inspector.title_patterns) else " "
            print(f"  [{match}] hwnd={w['hwnd']:10d}  pid={w['pid']:6d}  "
                  f"class={w['class_name']:30s}  title={w['title'][:50]}")
        return

    # --- חיבור ---
    if not inspector.connect():
        print("\n[ERROR] לא ניתן להתחבר לפרויקט השו\"ת")
        print("        ודא שהתוכנה פתוחה ונסה שוב")
        sys.exit(1)

    # --- סריקה מלאה ---
    if args.dump:
        filepath = inspector.dump_to_json(args.output, args.backend)
        print(f"\nהקובץ נוצר: {filepath}")
        print("ניתן להעביר אותו ל-responsa_controller.py לקביעת selectors")

    # --- הקלטה חיה ---
    if args.capture:
        result = inspector.live_capture(args.timeout)
        if result:
            print("\n[SUCCESS] פקד נקלט בהצלחה")
        else:
            print("\n[FAIL] לא נקלט פקד")

    # --- חיפוש ---
    if args.find:
        results = inspector.find_controls_by_name(args.find, args.backend)
        print(f"\nנמצאו {len(results)} פקדים תואמים ל-'{args.find}':\n")
        for ctrl in results:
            inspector.print_control_info(ctrl)

    # --- שדות עריכה ---
    if args.find_edit:
        results = inspector.find_controls_by_type("Edit", args.backend)
        print(f"\nנמצאו {len(results)} שדות עריכה:\n")
        for ctrl in results:
            inspector.print_control_info(ctrl)

    # --- כפתורים ---
    if args.find_button:
        results = inspector.find_controls_by_type("Button", args.backend)
        print(f"\nנמצאו {len(results)} כפתורים:\n")
        for ctrl in results:
            inspector.print_control_info(ctrl)


if __name__ == "__main__":
    main()
