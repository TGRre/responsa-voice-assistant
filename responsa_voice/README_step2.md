# עוזר קולי לפרויקט השו"ת — צעד 2

## מה כולל הצעד הזה

| קובץ | תפקיד |
|---|---|
| `intent_router.py` | ניתוב פקודות קוליות: Fast Reflex (regex) + LLM (Structured Outputs) |
| `voice_pipeline.py` | צינור עיבוד קולי: PTT, מיקרופון, VAD, STT, TTS |
| `config.py` | הגדרות מרכזיות (קיצורים, מונחים, LLM, STT, TTS, VAD) |

## התקנה

```bash
# 1. הפעל את הסביבה הווירטואלית
venv\Scripts\activate

# 2. התקן תלויות (אם לא מותקנות)
pip install -r requirements.txt

# 3. הגדר מפתח OpenAI (ל-Intent Router)
#    ב-PowerShell:
$env:OPENAI_API_KEY = "sk-..."

#    או ב-cmd:
set OPENAI_API_KEY=sk-...
```

**הערה:** מפתח OpenAI נדרש **רק** למסלול ה-LLM (פקודות מורכבות).
פקודות ניווט מהירות (Fast Reflex) עובדות **ללא מפתח**.

### endpoint חלופי

אם אתה משתמש בשירות תואם OpenAI (OpenRouter, שרת מקומי וכו'):

```powershell
$env:OPENAI_BASE_URL = "https://your-endpoint/v1"
```

## שימוש — PTT (Push-to-Talk)

### הפעלה מלאה

```bash
python voice_pipeline.py --execute
```

**מקשי PTT:** `Ctrl+Alt+V`
- **לחיצה ראשונה** — מתחיל הקלטה (דובר עכשיו)
- **לחיצה שנייה** — מבטל את ההקלטה
- **סיום אוטומטי** — אחרי 1.5 שניות של שקט (מקסימום 30 שניות)

### דוגמאות פקודות קוליות

| אומרים | מה קורה |
|---|---|
| "חפש ברכות שלום" | פותח חיפוש + מחפש "שלום" |
| "תוצאה הבאה" | עובר לתוצאה הבאה (ניווט מהיר — ללא LLM) |
| "פתח שולחן ערוך אורח חיים סימן שלוש מאות ואחת" | פותח שו"ע או"ח ש"א |
| "אפס את המסך" | איפוס חירום |
| "שלום" (שיחה) | "לא הובן" — אינו פקודה |

### מהירות תגובה

- **Fast Reflex** (ניווט): מיידי (< 100ms) — ללא LLM
- **LLM** (חיפוש/פתיחת מקור): ~1-3 שניות (תלוי ברשת)

## פקודות CLI

### intent_router.py — בדיקת ניתוב

```bash
# בדיקות offline — ללא מפתח API
python intent_router.py --selftest

# ניתוב פקודה אחת (ללא ביצוע)
python intent_router.py --text "תוצאה הבאה"

# ניתוב + ביצוע בפועל (דורש את השו"ת פתוח)
python intent_router.py --text "חפש ברכות שלום" --execute

# עיבוד קובץ: פקודה בכל שורה → JSON בכל שורה
python intent_router.py --batch commands.txt
```

### voice_pipeline.py — בדיקות שמע

```bash
# רשימת התקני שמע
python voice_pipeline.py --list-devices

# בדיקת קולות עבריים (TTS)
python voice_pipeline.py --check-voices

# בדיקת מיקרופון (מקליט 3 שניות)
python voice_pipeline.py --test-mic

# תמלול ביטוי אחד (ללא LLM)
python voice_pipeline.py --transcribe-once
```

## הורדת מודל STT

בפעם הראשונה שמריצים תמלול, המודל `ivrit-ai/whisper-large-v3-turbo-ct2`
יורד אוטומטית מ-Hugging Face (**~1.5 GB**). זה קורה פעם אחת בלבד.

זמן תמלול משוער על CPU (i5-13420H): **~1-3 שניות** לביטוי של 5 שניות.

## קול עברי ל-TTS

ההקראת המשוב הקולי משתמשת ב-**Windows SAPI** (ללא רשת).

### בדיקה

```bash
python voice_pipeline.py --check-voices
```

אם נמצא קול עברי — מעולה. אם לא:

### התקנת חבילת דיבור עברית

1. **הגדרות** → **זמן ושפה** → **שפה**
2. הוסף **עברית** (אם לא מותקנת)
3. לחץ על העברית → **אפשרויות שפה** → **התקן** תחת **דיבור**

### מתקדם: חשיפת קול OneCore ל-SAPI5

Windows 10/11 מתקינים קולות **OneCore** שאינם גלויים ל-SAPI5 ישירות.
הקוד מנסה לטעון קולות OneCore אוטומטית דרך `SpObjectToken`.
אם זה לא עובד, ניתן למזג את ה-token-ים ידנית:

1. פתח **regedit**
2. עבור אל `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens`
3. מצא את הקול העברי (למשל `MSTTS_V110_heIL_Asaf`)
4. **ייצא** (Export) את המפתח
5. ערוך את הקובץ: שנה את הנתיב ל-`HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens`
6. **ייבא** (Import) את הקובץ

בלי קול עברי, ההקראת תודפס במקום (fallback_to_print).

## פתרון בעיות

| בעיה | פתרון |
|---|---|
| "לא נמצא קול עברי" | התקן חבילת דיבור עברית (למעלה) |
| STT לא מזהה | בדוק מיקרופון: `--test-mic` |
| LLM נכשל | ודא ש-`OPENAI_API_KEY` מוגדר |
| הורדת מודל איטית | קורה פעם אחת בלבד (~1.5 GB) |
| ניווט לא עובד | קיצורי הניווט טרם אומתו (ראו הערה למטה) |

## הערות חשובות

- **קיצורי ניווט עדיין לא אומתו** (F3 לא פעל בבדיקה חיה). פקודות ניווט
  מסומנות `[PENDING]` בפלט. לאימות מאוחר יותר: **כלים → מנהל קיצורי דרך**,
  קטגוריה **מסכי עיון וחיפוש ועוד**.
- **אין להשתמש ב-pyttsx3** — הוא מתנגש עם `comtypes>=1.4` ש-pywinauto מתקין.
  ה-TTS משתמש ב-`win32com` ישירות.
- **הזרקת טקסט עברי** לתוכנה דרך `set_edit_text` + אימות readback בלבד
  (לעולם לא הקלדה וירטואלית — פריסת מקלדת עלולה להיות אנגלית).
