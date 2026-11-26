# -*- coding: utf-8 -*-
"""
build_books_data.py — בנייה של books_data.json מתוך Sefaria.

מושך את אינדקס הספרים של Sefaria (~6600 טקסטים), מכווץ פרשנים
("X on Y" → X), מדרג לפי שכיחות-משוערת, ומוסיף לכל ספר את
"הדרך שבה אנשים מתארים את המיקום" (heSectionNames: דף/עמוד,
סימן/סעיף, פרק/פסוק, פרק/הלכה...) מ- /api/v2/index/.

הרצה:  python build_books_data.py
פלט:   books_data.json (~1000 ספרים) — נטען ע"י book_catalog.py.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

INDEX_URL = "https://www.sefaria.org/api/index"
V2_URL = "https://www.sefaria.org/api/v2/index/"
USER_AGENT = "responsa-voice/1.0"
MAX_BOOKS = 1000
OUT = Path(__file__).resolve().parent / "books_data.json"

# סדר עדיפות: נמוך = חשוב יותר (נכנס קודם לרשימת ה-1000).
# יצירות עצמאיות לפני פרשנים — כדי שספרי יסוד לא יידחקו החוצה.
_PRIORITY = {
    "tanakh": 1,
    "talmud": 2,
    "mishnah": 3,
    "tosefta": 3,
    "rambam": 4,
    "shulchan_aruch": 4,
    "midrash": 5,
    "responsa": 6,
    "thought": 7,
    "musar": 7,
    "chasidut": 7,
    "kabbalah": 8,
    "liturgy": 9,
    "halakhah": 10,
    "talmud_commentary": 11,
    "commentary": 12,
    "second_temple": 13,
    "reference": 13,
    "other": 14,
}

# פרשנים מפורסמים — נכנסים לפני שאר הפרשנים
_FAMOUS_COMMENTATORS = {
    'רש"י', "תוספות", 'רמב"ן', 'רשב"א', 'ריטב"א', 'ר"ן', "ר\"ן",
    "רבנו יונה", "רבנו חננאל", 'מהר"ם מרוטנבורג', 'מהרש"א', 'מהרש"ל',
    'מהר"ם', "בית יוסף", "בעל הטורים", 'ט"ז', 'ש"ך', "משנה ברורה",
    "באר היטב", "פרי מגדים", "ערוך השולחן", "קצות החושן", "נתיבות המשפט",
    "חתם סופר", "חידושי הרי\"ם", "הגהות מיימוניות", "מרדכי", 'סמ"ג',
    "ספר החינוך", 'רס"ג', "ראב\"ד", "רשב\"ץ", "בעל המאור", "מאירי",
    'רי"ף', 'רא"ש', 'רשב"ם', 'ר"ח', 'תוספות רי"ד', 'נימוקי יוסף',
    'מהר"י בן לב', 'חידושי אנשי שם', 'חידושי הר"ן', 'חידושי הרשב"א',
    'חידושי הריטב"א', 'חידושי הרמב"ן', 'חידושי מהרש"א',
}

# מכסות לכל קטגוריה — ספרי יסוד לא נדחקים על-ידי מאות פרשנים נדירים
_QUOTA = {
    "tanakh": 35,
    "talmud": 100,
    "mishnah": 60,
    "tosefta": 60,
    "rambam": 85,
    "shulchan_aruch": 12,
    "midrash": 55,
    "responsa": 90,
    "thought": 95,
    "musar": 45,
    "chasidut": 70,
    "kabbalah": 50,
    "liturgy": 55,
    "halakhah": 50,
    "talmud_commentary": 45,
    "commentary": 30,
    "second_temple": 25,
    "reference": 15,
    "other": 15,
}

_PAREN_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def _fetch(url: str, retries: int = 2) -> dict:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(1.0 + attempt)


def _walk(nodes: list, out: list) -> None:
    for n in nodes:
        if n.get("contents"):
            _walk(n["contents"], out)
        else:
            out.append(n)


def _classify(title: str, he: str, cats: list[str]) -> str:
    top = cats[0] if cats else ""
    if top == "Tanakh":
        return "tanakh"
    if top == "Talmud":
        return "talmud"
    if top == "Mishnah":
        return "mishnah"
    if top == "Tosefta":
        return "tosefta"
    if top == "Midrash":
        return "midrash"
    if top == "Responsa":
        return "responsa"
    if top == "Jewish Thought":
        return "thought"
    if top == "Kabbalah":
        return "kabbalah"
    if top == "Chasidut":
        return "chasidut"
    if top == "Musar":
        return "musar"
    if top == "Liturgy":
        return "liturgy"
    if top == "Halakhah":
        if "משנה תורה" in he:
            return "rambam"
        if "שולחן ערוך" in he:
            return "shulchan_aruch"
        return "halakhah"
    if top == "Second Temple":
        return "second_temple"
    if top == "Reference":
        return "reference"
    return "other"


def _fallback_sections(category: str) -> list[str]:
    return {
        "tanakh": ["פרק", "פסוק"],
        "talmud": ["דף", "עמוד"],
        "mishnah": ["פרק", "משנה"],
        "tosefta": ["פרק"],
        "rambam": ["פרק", "הלכה"],
        "shulchan_aruch": ["סימן", "סעיף"],
        "responsa": ["סימן"],
    }.get(category, ["פרק"])


def _split_commentary(title: str, he: str) -> tuple[str, str, str, str]:
    """
    'Rashi on Genesis' / 'רש"י על בראשית' →
    (X_en, X_he, base_en, base_he). אם זה לא פרשן — מחזיר את הקלט ריק.
    """
    if " on " in title:
        x_en, _, base_en = title.partition(" on ")
    else:
        return "", "", "", ""
    if " על " in he:
        x_he, _, base_he = he.partition(" על ")
    elif ", " in he:
        x_he, _, base_he = he.partition(", ")
    else:
        x_he, base_he = he, ""
    return x_en.strip(), x_he.strip(), base_en.strip(), base_he.strip()


def _make_aliases(canonical: str, he_full: str, en: str, category: str) -> list[str]:
    aliases = {canonical, he_full, en}
    stripped = _PAREN_RE.sub("", canonical).strip()
    if stripped:
        aliases.add(stripped)
    if ", " in he_full:
        last = he_full.rsplit(", ", 1)[-1].strip()
        if last:
            aliases.add(last)
    if canonical.startswith("משנה תורה "):
        rest = canonical[len("משנה תורה "):]
        aliases.add(rest)
        aliases.add("רמב\"ם " + rest)
    if category == "tanakh":
        aliases.add("ספר " + canonical)
        if canonical in ("בראשית", "שמות", "ויקרא", "במדבר", "דברים"):
            aliases.add("חומש " + canonical)
    # צורה מקוצרת בלי תחילית: 'מדרש תנחומא' → 'תנחומא'
    for prefix in ("מדרש ", "ספר ", "פירוש "):
        if canonical.startswith(prefix) and len(canonical) > len(prefix) + 2:
            aliases.add(canonical[len(prefix):])
    aliases.discard("")
    return sorted(aliases)


def _sections_for(title: str, category: str) -> list[str]:
    """heSectionNames מ-Sefaria v2; fallback לפי קטגוריה."""
    try:
        data = _fetch(V2_URL + urllib.parse.quote(title), retries=1)
        schema = data.get("schema") or {}
        node = (schema.get("nodes") or [schema])[0]
        names = node.get("heSectionNames") or []
        if names:
            return [str(x) for x in names[:2]]
    except Exception:
        pass
    return _fallback_sections(category)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("[1] מושך אינדקס Sefaria...")
    index = _fetch(INDEX_URL)
    leaves: list[dict] = []
    _walk(index, leaves)
    print(f"    {len(leaves)} טקסטים באינדקס")

    # כיווץ פרשנים + מיון לפי עדיפות
    entries: dict[str, dict] = {}   # key = key-tilde dequoted canonical
    rows: list[dict] = []

    def _key(s: str) -> str:
        return re.sub(r"['\"״׳'’]", "", s).strip()

    for leaf in leaves:
        title = (leaf.get("title") or "").strip()
        he = (leaf.get("heTitle") or "").strip()
        if not title or not he:
            continue
        cats = leaf.get("categories") or []

        x_en, x_he, base_en, base_he = _split_commentary(title, he)
        if x_he:
            canonical, en = x_he, x_en
            top = cats[0] if cats else ""
            base_cat = _classify(base_en, base_he, cats)
            # מפרשי התלמוד — אזור משל עצמם (ראשונים ואחרונים על הש"ס)
            if base_cat in ("talmud", "mishnah") or top in ("Talmud", "Mishnah"):
                category = "talmud_commentary"
                sections = ["דף", "עמוד"]
            else:
                category = "commentary"
                sections = (
                    ["פרק", "פסוק"] if base_cat == "tanakh" else ["דף", "עמוד"]
                )
        else:
            canonical = he.replace(", ", " ").strip()
            en = title
            category = _classify(title, he, cats)
            sections = _fallback_sections(category)

        k = _key(canonical)
        if not k or k in entries:
            continue
        entries[k] = {
            "canonical": canonical,
            "he_full": he,
            "en": en,
            "category": category,
            "sections": sections,
            "priority": _PRIORITY.get(category, 13),
        }

    print(f"    {len(entries)} ספרים ייחודיים אחרי כיווץ פרשנים")

    ranked = sorted(
        entries.values(),
        key=lambda e: (
            e["priority"],
            0 if e["canonical"] in _FAMOUS_COMMENTATORS else 1,
            0 if " (" not in e["canonical"] and "(" not in e["canonical"] else 1,
            len(e["canonical"]),   # שם קצר = יצירה מוכרת יותר (יוריסטיקה)
            e["canonical"],
        ),
    )
    # בחירה במכסות לכל קטגוריה; גג MAX_BOOKS בסך הכל
    selected: list[dict] = []
    counts: dict[str, int] = {}
    for e in ranked:
        if len(selected) >= MAX_BOOKS:
            break
        cat = e["category"]
        if counts.get(cat, 0) >= _QUOTA.get(cat, 15):
            continue
        counts[cat] = counts.get(cat, 0) + 1
        selected.append(e)
    print(f"[2] נבחרו {len(selected)} ספרים (מתוך {len(ranked)}): {counts}")

    print("[3] מושך סגנונות מיקום (v2)...")
    real_titles = {e["canonical"]: None for e in selected}
    # v2 צריך את הכותרת המקורית (באנגלית עם פסיקים)
    by_canonical_en = {e["canonical"]: e["en"] for e in selected}

    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_sections_for, by_canonical_en[e["canonical"]], e["category"]): e
            for e in selected
        }
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                e["sections"] = fut.result()
            except Exception:
                pass
            done += 1
            if done % 100 == 0:
                print(f"    {done}/{len(selected)}")

    rows = [
        {
            "canonical": e["canonical"],
            "aliases": _make_aliases(e["canonical"], e["he_full"], e["en"], e["category"]),
            "category": e["category"],
            "volumes": [],
            "sections": e["sections"],
        }
        for e in selected
    ]

    OUT.write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[4] נכתב {OUT.name}: {len(rows)} ספרים")
    return 0


if __name__ == "__main__":
    sys.exit(main())
