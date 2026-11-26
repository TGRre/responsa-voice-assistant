# עוזר קולי לפרויקט השו"ת — צעד 1

## מה כולל הצעד הזה

| קובץ | תפקיד |
|---|---|
| `config.py` | הגדרות מרכזיות: קיצורי מקלדת, מונחים תורניים, קיצורי ספרים |
| `ui_inspector.py` | כלי דיאגנוסטיקה לסריקת עץ הפקדים של התוכנה |
| `responsa_controller.py` | מנוע השליטה: חיפוש, פתיחת מקורות, ניווט, איפוס חירום |

## התקנה

```bash
# 1. יצירת סביבה וירטואלית (מומלץ)
python -m venv venv
venv\Scripts\activate

# 2. התקנת תלויות
pip install -r requirements.txt
```

## שימוש — שלב 1

### 1. סריקת ממשק התוכנה

```bash
# ודא שפרויקט השו"ת פתוח לפני ההרצה

# סריקה מלאה → יוצרת responsa_ui_tree.json
python ui_inspector.py --dump

# סריקה עם Win32 backend
python ui_inspector.py --dump --backend win32

# רשימת כל החלונות הפתוחים
python ui_inspector.py --windows

# חיפוש פקד לפי טקסט
python ui_inspector.py --find "חיפוש"

# רשימת שדות עריכה
python ui_inspector.py --find-edit

# רשימת כפתורים
python ui_inspector.py --find-button
```

### 2. הקלטת פקד חי

```bash
# הסקריפט ימתין 30 שניות — שים פוקוס על הפקד בתוכנה
python ui_inspector.py --capture

# עם הארכת זמן
python ui_inspector.py --capture --timeout 60
```

### 3. בדיקת מנוע השליטה

```bash
# התחברות
python responsa_controller.py --connect

# בדיקת סטטוס
python responsa_controller.py --status

# בדיקת "ודא פעיל ונקי"
python responsa_controller.py --clean

# בדיקת חיפוש
python responsa_controller.py --search "שלום"

# בדיקת פתיחת מקור
python responsa_controller.py --source 'ברכות ב ע"א'

# בדיקת ניווט
python responsa_controller.py --nav NEXT_RESULT

# איפוס חירום
python responsa_controller.py --reset
```

## מה הלאה — צעד 2

לאחר שתאשר שהשליטה עובדת (או שתשלח את `responsa_ui_tree.json`):

1. **`intent_router.py`** — מודל שפה שמתרגם דיבור תורני ל-JSON מובנה
2. **`voice_pipeline.py`** — קליטת שמע, VAD, STT עם Whisper

## הערות חשובות

- **הזרקת טקסט עברי**: המערכת משתמשת ב-Clipboard (`Ctrl+V`) ולא בהקלדה ישירה
- **אימות פוקוס**: כל פעולה בודקת שהחלון הנכון בפוקוס לפני ביצוע
- **רשת ביטחון**: `emergency_reset()` נקרא אוטומטית בכל שגיאה
- **קיצורי מקלדת**: ניתן לשנות ב-`config.py` → `RESPONSA_SHORTCUTS`
