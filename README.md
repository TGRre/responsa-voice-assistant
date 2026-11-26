# עוזר קולי לפרויקט השו"ת | Bar-Ilan Responsa Voice Assistant

עוזר קולי בעברית לשליטה מהירה בתוכנת **פרויקט השו"ת של אוניברסיטת בר-אילן** (Windows).  
מאפשר לפתוח מקורות תורניים (תלמוד בבלי ומפרשיו, רמב"ם, שולחן ערוך ונושאי כליו, תנ"ך ופרשות שבוע), לחפש טקסט ולנווט בתוכנה בעזרת פקודות קוליות — **100% אופליין** ללא צורך במפתח API.

---

## ✨ תכונות עיקריות

- **זיהוי דיבור עברי איכותי ואופליין** — מבוסס על `faster-whisper` עם מודל `ivrit-ai/whisper-large-v3-turbo-ct2`.
- **מנוע אימות ותיקון תורני מהיר (< 10ms)** — מזהה ומאמת מסכתות, דפים ועמודים, פרקי רמב"ם, סימני שולחן ערוך ונושאי כליו, פרשות ותנ"ך, ומזריק טקסט נקי לתוכנת השו"ת.
- **חלון צף (HUD)** — מציג מצב הקלטה, תמלול גולמי ופקודה מתוקנת, כולל חלון **הגדרות (⚙)** מובנה.
- **משוב קולי בעברית (TTS)** — באמצעות Windows SAPI / OneCore (אופליין).
- **סוכן AI חלופי (אופציונלי)** — ניתן להזין מפתח API אישי (Groq בחינם או OpenAI) בחלון ההגדרות לטיפול בפקודות חופשיות או משובשות במיוחד.

---

## 📥 התקנה למשתמשי Windows (ללא צורך ב-Python)

1. עבור ללשונית **[Releases](../../releases)** והורד את קובץ ההתקנה האחרון:
   - **`ResponsaVoice-Setup-x.x.x.exe`** — מתקין רגיל ל-Windows (יוצר קיצורי דרך בתפריט התחלה ובשולחן העבודה, ללא צורך בהרשאות מנהל).
   - או **`ResponsaVoice-Portable-x.x.x.zip`** — גרסה ניידת ללא התקנה.
2. הפעל את ההתקנה והרץ את **Responsa Voice Assistant**.
3. **הפעלה ראשונה**: האפליקציה תוריד אוטומטית את מודל התמלול העברי מ-Hugging Face (~1.5 GB, פעם אחת בלבד).

> **הערה לגבי Windows SmartScreen:** מכיוון שהאפליקציה אינה חתומה בתעודת קוד מסחרית, ייתכן ש-Windows יציג אזהרה בהפעלה הראשונה. לחץ **"מידע נוסף" (More info)** ← **"הפעל בכל זאת" (Run anyway)**.

---

## 🎙️ שימוש

1. ודא שתוכנת **פרויקט השו"ת** פתוחה (או מוגדרת בהגדרות).
2. לחץ על מקש הדיבור (**ברירת מחדל: `Space`**, ניתן לשינוי ב-⚙) ודבר:
   - `"פתח מסכת ברכות דף ב עמוד א"`
   - `"פתח רש"י שבת דף כ עמוד ב"`
   - `"פתח רמב"ם הלכות שבת פרק א הלכה א"`
   - `"פתח שולחן ערוך אורח חיים סימן של"א סעיף א"`
   - `"פתח משנה ברורה סימן שכ"ח"`
   - `"פתח בראשית פרק א פסוק א"` / `"פתח פרשת בא"`
   - `"חפש שלום"` / `"תמצא חסד"`
   - `"הבא"` / `"למעלה"` / `"למטה"` / `"אישור"` / `"סגור"`
3. ההקלטה נעצרת אוטומטית לאחר 1.5 שניות של שקט (או בלחיצה נוספת לביטול).

### ⚙️ הגדרות (אופציונלי)
לחץ על סמל **⚙** בחלון הצף כדי לשנות:
- מקש הדיבור (`Space`, `Ctrl+Alt+V`, `F9` וכו')
- מיקום החלון הצף על המסך
- הפעלה/כיבוי של משוב קולי (TTS)
- נתיב קובץ `RESPONSA.exe` (מזוהה אוטומטית)
- מפתח API אישי (Groq / OpenAI) לתיקון חלופי — נשמר **מקומית בלבד** ב-`%APPDATA%\ResponsaVoice\settings.json`.

---

## 🛠️ פיתוח ובנייה מקוד המקור

### דרישות
- Windows 10 / 11 (64-bit)
- Python 3.11+

### הרצה מפיתוח
```powershell
python -m venv responsa_voice\venv
responsa_voice\venv\Scripts\activate
pip install -r responsa_voice\requirements.txt

# הפעלת האפליקציה עם HUD והגדרות
python responsa_voice\app_main.py
```

### הרצת בדיקות
```powershell
pip install pytest
pytest responsa_voice_engine/tests -q
python responsa_voice/ai_verifier.py --selftest
python responsa_voice/intent_router.py --selftest
python responsa_voice/book_catalog.py --selftest
python responsa_voice/app_main.py --self-check
```

### בניית קובץ התקנה ל-Windows
```powershell
pip install pyinstaller
winget install JRSoftware.InnoSetup

powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Version "1.0.0"
```
הקבצים ייווצרו בתיקייה `installer_output\`:
- `ResponsaVoice-Setup-1.0.0.exe`
- `ResponsaVoice-Portable-1.0.0.zip`

### פרסום אוטומטי ב-GitHub Releases
בעת יצירת תגית גרסה (למשל `v1.0.0`) ודחיפתה ל-GitHub, ה-Workflow ב-[`.github/workflows/release.yml`](.github/workflows/release.yml) מריץ את כל הבדיקות, בונה את קובץ ההתקנה ומפרסם אותו אוטומטית תחת **Releases**:
```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 📄 רישיון
מופץ תחת רישיון [MIT](LICENSE).
