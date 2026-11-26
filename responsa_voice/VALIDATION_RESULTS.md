# Step 1 Validation Results — Bar-Ilan Responsa Voice Assistant

**Date:** 2026-10-28
**App:** Responsa Project v33 (CD33) — `RESPONSA.exe`, class `ResponsaProject`, pid 20580
**Window title:** `פרוייקט השו"ת  :  גירסה 33  - שולחן עבודה בסיסי`
**Python:** 3.12.10 64-bit · venv at `responsa_voice\venv`

---

## Gate checklist

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | `--windows` marks Responsa `[✓]` | ✅ PASS | hwnd=67584, class=ResponsaProject |
| 2 | At least one backend dump usable | ✅ PASS | win32: 24 controls · uia: 41 controls |
| 3 | Search input field identified & reachable | ✅ PASS | Dialog `חיפוש בניסוח חופשי` → Edit id=1233 |
| 4 | `--search "שלום"` injects correct Hebrew and runs | ✅ PASS | Readback `שלום` verified; MDI results `1-3` |
| 5 | At least one navigation command works | ⚠️ **NOT VERIFIED** | F3 (`NEXT_RESULT`) did nothing |
| 6 | `--reset` recovers cleanly | ✅ PASS | `[EMERGENCY] איפוס הושלם — פוקוס הוחזר` |
| 7 | `RESPONSA_SHORTCUTS` corrected against reality | ⚠️ **PARTIAL** | Menu-accelerators verified; nav keys still guesses |

**Gate verdict: CONDITIONAL GO.** Core search pipeline (the thing Step 2 depends on) is proven. Navigation shortcuts are still unverified and must be settled before Fast Reflex commands can be trusted — but they do not block `intent_router.py` / `voice_pipeline.py` work.

---

## Critical discoveries (changed the architecture)

### 1. There is NO search field on the main window
The toolbar band `חיפוש` (auto_id 59405) has **no Edit control**. The two `Edit auto_id=1001` controls on the main window are:
- **wide (418px)** — edit part of `ComboBox 1296`, which is the **"חלונות..." (Windows) selector**, NOT search
- **narrow (43px)** — font-size box in the `גופן` band

Any code that typed into "the search field" was typing into the windows-selector.

### 2. Search is a dialog opened by menu command
`חיפוש` is a **direct menu command, id=32857** (not a popup, not Ctrl+F).
Sending `WM_COMMAND 32857` opens dialog `#32770` titled **`חיפוש בניסוח חופשי`**.

Dialog controls (verified):

| Control | ID | Role |
|---|---|---|
| Edit | **1233** | Query input |
| Button | **1** | `&בצע חיפוש` (Execute search) |
| Button | 2 | `ביטול` (Cancel) |
| Button | 1024 | `חיפוש בכל ה&מאגרים` (Search all databases) |
| Button | 1062 | `&ניקוי` (Clear) |
| Button | 1034 | `שאילתות &קודמות` (Previous queries) |
| ListBox | 1052 | Query history |

Four search dialog titles exist (right-side mode buttons in the dialog):
`חיפוש בניסוח חופשי` · `חיפוש קל` · `חיפוש טבלאי` · `חיפוש מתקדם`

### 3. `Ctrl+F` does not exist
Sending Ctrl+F does nothing. The real accelerators, dumped from the Win32 menu:

| Key | Command | Menu id |
|---|---|---|
| `Ctrl+M` | גימטריה | 32861 |
| `Ctrl+T` | מאגרים פעילים | 33013 |
| `Ctrl+H` | רשימת ספרים ומהדורות | 32797 |
| `Alt+Y` | דף יומי | 33142 |
| `F1` | נושאי עזרה | 57667 |
| `Alt+W` | שולחנות עבודה | 33070 |

Search / browse / connections are plain menu commands (no accelerator shown):
`חיפוש`=32857, `עיון`=32781, `קשרים`=33536.

### 4. Hebrew injection: use win32 `set_edit_text`, not UIA
- UIA `window_text()` returns `''` **even when text is present** — made early tests look like failures
- `WM_SETTEXT` returns success (1) but text does not stick on many controls
- **win32 backend `set_edit_text(text)` works** and readback matches
- Clicking by coordinates is unsafe (opened a stray `קיצור דרך` dialog) — use messages/buttons

### 5. pywinauto connect gotchas
| Attempt | Result |
|---|---|
| `connect(path="RESPONSA.exe")` | ❌ times out |
| `connect(process="RESPONSA")` | ❌ `Process with PID=RESPONSA not found` (needs **int PID**) |
| `connect(process=pid)` + `window(title_re="a\|b\|c")` | ❌ times out (alternation hangs UIA) |
| `connect(process=pid)` + `window(class_name="ResponsaProject")` | ✅ 0.2s |

Also: `win32gui.EnumChildWindows(hwnd)` needs a **callback** — calling it bare returns 0 children (was silently swallowed).

### 6. Title pattern bug
`r"שו.?ת"` matched the **taskbar** title `שורת המשימות` (שו+ר+ת). Fixed to require definite article `השו.?ת` and/or an explicit quote char, plus a taskbar denylist in the title-match helpers.

---

## Bugs found & fixed in code

| File | Bug | Fix |
|---|---|---|
| `config.py` | `שו.?ת` matched taskbar | `השו.?ת` + quote-required alt + `RESPONSA_WINDOW_CLASS` |
| `config.py` | `open_search: "Ctrl+F"` is wrong | `MENU:search` + `MENU_COMMANDS` map (verified ids) |
| `ui_inspector.py` / `responsa_controller.py` | connect by `path="*.exe"` / `process="NAME"` | resolve PID via psutil, `connect(process=pid)` |
| both | `title_re` alternation hang | `class_name=ResponsaProject` lookup |
| both | taskbar accepted as match | denylist in `_window_*matches_title` |
| `ui_inspector.py` | `EnumChildWindows` bare call → 0 controls | callback-based enumeration, no double recursion |
| `ui_inspector.py` | `find_controls_by_type` only matched `control_type` | also match `class_name` (win32 tree) |
| `responsa_controller.py` | `_find_search_field` targeted ComboBox 1296 | targets dialog Edit id=1233 |
| `responsa_controller.py` | `execute_search` used Ctrl+F | `_open_search_dialog()` via WM_COMMAND 32857 |
| `responsa_controller.py` | injection via UIA | win32 `set_edit_text` + readback verify |
| `responsa_controller.py` | `_select_search_scope` looked for TreeItem | scope is in dialog; TreeItem fallback kept |
| `responsa_controller.py` | CLI swallowed action results | prints `[OK]/[FAIL]` per action |

---

## Navigation — still open

`--nav NEXT_RESULT` (F3) reported OK but the MDI result window stayed at `1-3`. The nav keys in `RESPONSA_SHORTCUTS` (`F3`, `Shift+F3`, `PageDown`, …) are still guesses.

**How to settle (next session, hands-on):**
1. Open `כלים → מנהל קיצורי דרך` (or `MENU_COMMANDS["shortcut_manager"]` = 33296)
2. Category **`מסכי עיון וחיפוש ועוד`** — read the bound key for next/prev result, page up/down
3. Update `RESPONSA_SHORTCUTS` and re-run `--nav`

Alternatively the user can read the bindings once and tell us.

---

## Artifacts

| File | Content |
|---|---|
| `responsa_ui_tree_win32.json` | 24 controls, Win32 backend |
| `responsa_ui_tree_uia.json` | 41 controls, UIA backend |
| `config.py` | patched patterns + `MENU_COMMANDS` + verified accelerators |
| `ui_inspector.py` | patched connect / enum / find |
| `responsa_controller.py` | patched connect / search path / CLI |

## Known warnings
- `32-bit application should be automated using 32-bit Python (you use 64-bit Python)` — pywinauto warns but win32 backend works. Consider 32-bit Python if UIA flakiness appears.
- `set_edit_text` readback must be verified — never assume injection succeeded.
- Do not click by screen coordinates on the main window; use messages and control ids.

---

## Verdict for Step 2

**Proceed** with `intent_router.py` + `voice_pipeline.py`. The action-execution layer for SEARCH is proven end-to-end. Carry these invariants into Step 2:

1. Search = `WM_COMMAND 32857` → dialog → Edit `1233` → Button `1`
2. Inject Hebrew with win32 `set_edit_text` + readback
3. Never use `open_search` as a keyboard shortcut
4. Nav actions must be labeled "pending shortcut verification" until the manager is read
