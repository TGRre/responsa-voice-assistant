# -*- coding: utf-8 -*-
"""
book_catalog.py — קטלוג הספרים של פרויקט השו"ת + פיצול/הצמדת payload.

הקטלוג נותן לסוכן ה-LLM הקשר (אילו ספרים קיימים) ומאפשר גם
הצמדה מקומית מהירה (בלי רשת) של שם ספר משובש לצורה קנונית.

payload טיפוסי: 'ברכות ב עמוד ע"א' → book-part 'ברכות ב', locator 'עמוד ע"א'.

תומך ב-Python 3.11+
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

from intent_router import _fold_word, _normalize

# סף דמיון להתאמת book-part לכינוי (גם בלי וגם עם סימני גרשיים)
_ALIAS_MATCH_MIN: float = 0.80

# תחילת locator: מילות מיקום (עמוד/דף/סימן/...) וסימני עמוד (ע"א/ע"ב)
_LOCATOR_RE = re.compile(
    r"""^(?:
        עמוד(?:ים)? | עמ'? |
        דף | דפי'? |
        סימ[ןנ] | סי'? |
        סעיף | סעיפים | סעי'? |
        פרק(?:ים)? |
       הלכה | הל'? |
        ע["'״׳][אב]
    )$""",
    re.VERBOSE,
)

_QUOTES_RE = re.compile(r"['\"״׳'’]")

# מילים כתובות במקום אות כרך: 'ברכות אלף' → 'ברכות א'
# (מיפוי אחרי הסרת גרשיים — כך 'א'ל"ף' מכוסה גם הוא)
_VOLUME_WORDS: dict[str, str] = {
    "אלף": "א",
    "בית": "ב",
    "שתיים": "ב",
    "שניים": "ב",
    "ראשון": "א",
    "שני": "ב",
}

_CATEGORY_HE: dict[str, str] = {
    "masechet": "משנה",
    "shulchan_aruch": "שולחן ערוך",
    "responsa": "שו\"ת",
    "tanakh": "תורה",
    "talmud": "תלמוד",
    "mishnah": "משנה",
    "tosefta": "תוספתא",
    "rambam": "רמב\"ם",
    "midrash": "מדרש",
    "talmud_commentary": "מפרשי התלמוד",
    "thought": "מחשבת",
    "musar": "מוסר",
    "chasidut": "חסידות",
    "kabbalah": "קבלה",
    "liturgy": "תפילה",
    "halakhah": "הלכה",
    "commentary": "פרשנות",
    "second_temple": "בית שני",
    "reference": "עיון",
    "other": "אחר",
}

# סגנון המיקום ברירת-מחדל לפי קטגוריה (entries מ-books_data.json מגיעים עם sections)
_CATEGORY_SECTIONS: dict[str, tuple[str, ...]] = {
    "masechet": ("דף", "עמוד"),
    "shulchan_aruch": ("סימן", "סעיף"),
    "responsa": ("סימן",),
    "tanakh": ("פרק", "פסוק"),
    "talmud_commentary": ("דף", "עמוד"),
}


# ============================================================
# 1. מבנה רשומה
# ============================================================

@dataclass(frozen=True)
class BookEntry:
    canonical: str           # 'ברכות', 'שו"ע או"ח', 'אגרות משה'
    aliases: tuple[str, ...] # כינויים + צורות כרך ('ברכות א')
    category: str            # 'masechet' | 'shulchan_aruch' | 'responsa' | ...
    volumes: tuple[str, ...] # ('א','ב') למכתחי משנה; () אחרת
    sections: tuple[str, ...] = ()  # סגנון המיקום: ('דף','עמוד') / ('סימן','סעיף') / ('פרק','פסוק')


def _make(
    canonical: str,
    category: str,
    volumes: tuple[str, ...] = (),
    extra_aliases: tuple[str, ...] = (),
    sections: tuple[str, ...] = (),
) -> BookEntry:
    """בונה רשומה: canonical + צורות כרכים נכנסים אוטומטית ל-aliases."""
    if isinstance(extra_aliases, str):
        extra_aliases = (extra_aliases,)
    aliases = (
        (canonical,)
        + tuple(f"{canonical} {v}" for v in volumes)
        + tuple(extra_aliases)
    )
    return BookEntry(
        canonical=canonical,
        aliases=tuple(dict.fromkeys(aliases)),
        category=category,
        volumes=tuple(volumes),
        sections=tuple(sections) or _CATEGORY_SECTIONS.get(category, ()),
    )


def _fold_strip(text: str) -> str:
    """נרמול משווה: צורות סופיות + הסרת כל סימני הגרשיים."""
    return _QUOTES_RE.sub("", _fold_word(text))


def _ratio(a: str, b: str, floor: float = 0.0) -> float:
    """
    SequenceMatcher עם דילוג זול: יחס רצפים לא יכול לעבור 2·min/(a+b),
    ולכן זוגות שאורכיהם לא תואמים — נדחים בלי לחשב דמיון.
    """
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    if 2.0 * min(la, lb) / (la + lb) < floor:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# אינדקס כינויים — נבנה אחרי ש-BOOKS שלם (כולל books_data.json)
_ALIAS_SET: set[str] = set()                             # exact lookup
_ALIAS_ENTRY: dict[str, BookEntry] = {}                  # bare → entry ראשון
_ALIAS_INDEX: list[tuple[str, str, BookEntry]] = []      # (bare, folded, entry)


def _rebuild_alias_index() -> None:
    global _ALIAS_SET, _ALIAS_ENTRY, _ALIAS_INDEX
    _ALIAS_SET = set()
    _ALIAS_ENTRY = {}
    _ALIAS_INDEX = []
    for entry in BOOKS:
        for alias in entry.aliases:
            bare = _fold_strip(alias)
            if not bare or bare in _ALIAS_SET:
                continue
            _ALIAS_SET.add(bare)
            _ALIAS_ENTRY.setdefault(bare, entry)
            _ALIAS_INDEX.append((bare, _fold_word(alias), entry))


def _is_exact_alias(text: str, entries: Optional[Iterable[BookEntry]] = None) -> bool:
    """התאמה מדויקת (אחרי נרמול גרשיים/צורות סופיות) לאחד הכינויים."""
    bare = _fold_strip(text)
    if not bare:
        return False
    if entries is None:
        return bare in _ALIAS_SET
    return any(_fold_strip(alias) == bare for e in entries for alias in e.aliases)


def _best_alias_score(
    text: str,
    entries: Optional[Iterable[BookEntry]] = None,
    floor: float = 0.0,
) -> float:
    """הציון המקסימלי של text מול כל הכינויים (עם/בלי גרשיים)."""
    folded = _fold_word(text)
    bare = _fold_strip(text)
    if not bare:
        return 0.0
    if entries is None:
        pairs: Iterable[tuple[str, str, BookEntry]] = _ALIAS_INDEX
    else:
        pairs = (
            (_fold_strip(alias), _fold_word(alias), e)
            for e in entries
            for alias in e.aliases
        )
    best = 0.0
    for a_bare, a_folded, _entry in pairs:
        score = max(
            _ratio(folded, a_folded, floor),
            _ratio(bare, a_bare, floor),
        )
        if score > best:
            best = score
            if best >= 0.999:
                return best
    return best


# ============================================================
# 2. פיצול payload ל-book-part ו-locator
# ============================================================

def split_payload(payload: str) -> tuple[str, str]:
    """
    'ברכות ב עמוד ע"א' → ('ברכות ב', 'עמוד ע"א').

    סדר: (1) ההתאמה הארוכה ביותר של תחילת ה-payload לכינוי ידוע —
    קודם מדויק, אחר-כך דמיון (ה-score הגבוה מנצח, מקצר נשבר תיקו);
    (2) פיצול על מילת locator; (3) הכל book-part.
    """
    payload = _normalize(payload)
    if not payload:
        return "", ""
    tokens = payload.split()

    # (1a) התאמה מדויקת — התחילות הארוכה ביותר
    for k in range(len(tokens), 0, -1):
        cand = " ".join(tokens[:k])
        if _is_exact_alias(cand):
            return cand, " ".join(tokens[k:]).strip()

    # (1b) התאמה בדמיון — ה-score הגבוה מנצח
    best_k, best_score = 0, _ALIAS_MATCH_MIN
    for k in range(1, len(tokens) + 1):
        cand = " ".join(tokens[:k])
        score = _best_alias_score(cand, floor=_ALIAS_MATCH_MIN)
        if score > best_score:
            best_k, best_score = k, score
    if best_k:
        return " ".join(tokens[:best_k]), " ".join(tokens[best_k:]).strip()

    for i, tok in enumerate(tokens):
        if _LOCATOR_RE.match(_fold_word(tok)):
            return " ".join(tokens[:i]).strip(), " ".join(tokens[i:]).strip()

    return payload, ""


def _detect_volume(book_part: str) -> tuple[str, str]:
    """
    'ברכות ב' → ('ברכות', 'ב'); 'ברכות אלף' → ('ברכות', 'א').
    ללא כרך → (book_part, '').
    """
    tokens = book_part.split()
    if tokens and len(tokens) > 1:
        last = _fold_word(tokens[-1])
        if last in ("א", "ב"):
            return " ".join(tokens[:-1]), tokens[-1]
        spelled = _VOLUME_WORDS.get(_QUOTES_RE.sub("", tokens[-1]))
        if spelled:
            return " ".join(tokens[:-1]), spelled
    return book_part, ""


# ============================================================
# 3. הצמדת payload לקטלוג
# ============================================================

def snap_payload(
    payload: str,
    *,
    top_n: int = 8,
    min_score: float = _ALIAS_MATCH_MIN,
) -> list[tuple[float, BookEntry, str]]:
    """
    מדריך את book-part מול הקטלוג ומחזיר מועמדים.

    Returns:
        [(score, entry, payload_קנוני_מומלץ)] — הצעה בנויה מ
        '{canonical} {volume} {locator}' (ה-locator עובר כלשונו).
        min_score נמוך שימושי למועמדים לפרומפט (ללא התחייבות להצמדה).
    """
    book_part, locator = split_payload(payload)
    if not book_part:
        return []
    core, volume = _detect_volume(book_part)

    folded_core, bare_core = _fold_word(core), _fold_strip(core)
    folded_book, bare_book = _fold_word(book_part), _fold_strip(book_part)
    best_by_entry: dict[str, tuple[float, BookEntry]] = {}
    for a_bare, a_folded, entry in _ALIAS_INDEX:
        score = max(
            _ratio(folded_core, a_folded, min_score),
            _ratio(bare_core, a_bare, min_score),
            _ratio(folded_book, a_folded, min_score),
            _ratio(bare_book, a_bare, min_score),
        )
        prev = best_by_entry.get(entry.canonical)
        if score >= min_score and (prev is None or score > prev[0]):
            best_by_entry[entry.canonical] = (score, entry)

    scored = sorted(best_by_entry.values(), key=lambda item: -item[0])
    results: list[tuple[float, BookEntry, str]] = []
    for score, entry in scored[:top_n]:
        parts = [entry.canonical]
        loc = locator
        if volume and entry.volumes:
            parts.append(volume)
        elif loc and entry.volumes:
            # מילת כרך כתובה בראש ה-locator: 'ברכות' + 'אלף' → 'ברכות א'
            # רק אם היא לבדה או שאחריה מילת מיקום אמיתית (לא 'בית שמואל')
            loc_tokens = loc.split()
            rest_ok = (
                len(loc_tokens) == 1
                or _LOCATOR_RE.match(_fold_word(loc_tokens[1])) is not None
            )
            spelled = (
                _VOLUME_WORDS.get(_QUOTES_RE.sub("", loc_tokens[0]))
                if rest_ok
                else None
            )
            if spelled:
                parts.append(spelled)
                loc = " ".join(loc_tokens[1:])
        if loc:
            parts.append(loc)
        results.append((score, entry, normalize_reference(" ".join(parts))))
    return results


def build_catalog_block(entries: Iterable[BookEntry]) -> str:
    """בלוק קומפקטי לפרומפט: שורה אחת לכל ספר."""
    lines: list[str] = []
    for e in entries:
        parts = [e.canonical, _CATEGORY_HE.get(e.category, e.category)]
        if e.volumes:
            parts.append("כרכים: " + ",".join(e.volumes))
        if e.sections:
            parts.append("מיקום: " + "/".join(e.sections))
        extras = [a for a in e.aliases if a != e.canonical]
        if extras:
            parts.append("כינויים: " + ", ".join(extras))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def find_book(name: str) -> Optional[BookEntry]:
    """חיפוש מדויק לפי canonical או כינוי (O(1) דרך אינדקס)."""
    return _ALIAS_ENTRY.get(_fold_strip(name))


# ============================================================
# 3b. נרמול ייחוס לפורמט תורני תקני
# ============================================================
# 'יבמות יוד דלת עמוד בית' → 'יבמות דף י"ד עמוד ב'
# מטפל: שמות אותיות, ספרות ערביות, פסיקים, והוספת דף/סימן.

_LETTER_NAMES: dict[str, str] = {
    "אלף": "א", "בית": "ב", "גימל": "ג", "דלת": "ד", "הא": "ה", "הי": "ה",
    "וו": "ו", "ואו": "ו", "זין": "ז", "חית": "ח", "טית": "ט", "יוד": "י",
    "יו": "י", "כף": "כ", "למד": "ל", "מם": "מ", "מים": "מ", "נון": "נ",
    "סמך": "ס", "עין": "ע", "פי": "פ", "צדי": "צ", "קוף": "ק", "ריש": "ר",
    "שין": "ש", "תיו": "ת",
}

_DIGITS_RE = re.compile(r"^\d{1,3}$")
_GERSHAYIM_NUM_RE = re.compile(r"^[א-ת]+[\"'״][א-ת]$")
_BARE_NUM_RE = re.compile(r"^[א-ת]{2,3}$")

# מילים קצרות נפוצות — לא מספרים גם בלי גרשיים ('של א' ≠ 'ש"ל')
_NUM_STOPWORDS: frozenset[str] = frozenset({
    "של", "על", "את", "אל", "מן", "כל", "גם", "בין", "עוד", "עם",
    "שם", "עד", "דבר", "ראש", "אחר", "לפי", "אלה", "אלו", "אין",
    "כמו", "רק", "פה", "זה", "זו", "או", "אם", "כי", "לא", "כן",
})

_NUMERAL_VALUES: dict[str, int] = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ל": 30, "מ": 40, "נ": 50, "ס": 60, "ע": 70, "פ": 80,
    "צ": 90, "ק": 100, "ר": 200, "ש": 300, "ת": 400,
}

# מספרים במילים מדוברות — 'שלוש מאות ואחת' ועוד. מקרים כאלה נשלחים ל-AI
# להמרה למספור עברי (הנרמול המקומי מטפל רק בשמות אותיות וספרות).
# 'אלף' אינו כאן — הוא משמש גם כאות כרך (ברכות אלף → ברכות א).
_SPOKEN_NUMBER_WORDS: frozenset[str] = frozenset({
    "אחת", "שתיים", "שתים", "שנים",
    "שלוש", "שלושה", "שלשה", "ארבע", "ארבעה",
    "חמש", "חמשה", "שש", "שישה", "ששה",
    "שבע", "שבעה", "שמונה", "תשע", "תשעה",
    "עשר", "עשרה", "עשרים", "שלושים", "ארבעים", "חמישים",
    "שישים", "ששים", "שבעים", "שמונים", "תשעים",
    "מאה", "מאתיים", "מאתים", "מאות",
})

_NUMERAL_TABLE = (
    (400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"), (90, "צ"), (80, "פ"),
    (70, "ע"), (60, "ס"), (50, "נ"), (40, "מ"), (30, "ל"), (20, "כ"),
    (10, "י"), (9, "ט"), (8, "ח"), (7, "ז"), (6, "ו"), (5, "ה"),
    (4, "ד"), (3, "ג"), (2, "ב"), (1, "א"),
)


def to_hebrew_numeral(n: int) -> str:
    """14 → 'י"ד', 301 → 'ש"א', 15 → 'ט"ו' (לפי המנהג)."""
    if n <= 0 or n >= 1000:
        return str(n)
    if n == 15:
        return 'ט"ו'
    if n == 16:
        return 'ט"ז'
    parts: list[str] = []
    for value, letter in _NUMERAL_TABLE:
        while n >= value:
            parts.append(letter)
            n -= value
    if len(parts) == 1:
        return parts[0]
    return "".join(parts[:-1]) + '"' + parts[-1]


def has_spoken_numbers(text: str) -> bool:
    """האם הטקסט מכיל מספרים במילים ('שלוש מאות ואחת') שדורשים המרה."""
    for token in text.split():
        bare = _QUOTES_RE.sub("", token)
        if bare and bare[0] in "וה":
            bare = bare[1:]
        if bare in _SPOKEN_NUMBER_WORDS:
            return True
    return False


def _letter_of(token: str) -> Optional[str]:
    """שם של אות → האות עצמה ('יוד'→י, 'יו'→י). None אם זו לא שם-אות."""
    bare = _QUOTES_RE.sub("", token)
    if not bare or _LOCATOR_RE.match(_fold_word(token)):
        return None
    if bare in _LETTER_NAMES:
        return _LETTER_NAMES[bare]
    best, best_score = None, 0.8
    for name, letter in _LETTER_NAMES.items():
        score = SequenceMatcher(None, bare, name).ratio()
        if score >= 0.8 and score > best_score:
            best, best_score = letter, score
    return best


def _format_bare_numeral(token: str) -> Optional[str]:
    """'כז' → 'כ"ז'. רק אם זה באמת נראה כמספר (לא מילה)."""
    if not _BARE_NUM_RE.match(token) or token in _NUM_STOPWORDS:
        return None
    if _letter_of(token) is not None:
        return None
    try:
        vals = [_NUMERAL_VALUES[c] for c in token]
    except KeyError:
        return None
    # ספרות עברייות נכתבות מהגדול לקטן — סדר עולה מעיד על מילה
    if any(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
        return None
    return token[:-1] + '"' + token[-1]


def normalize_reference(payload: str) -> str:
    """
    מנרמל ייחוס ספר לפורמט תורני:
    'יבמות יוד דלת עמוד בית' → 'יבמות דף י"ד עמוד ב'
    'יבמות דף 14 עמוד 2'     → 'יבמות דף י"ד עמוד ב'
    מילים רגילות (כמו 'בית' ב'בית שמואל') עוברות כלשונן.
    """
    payload = _normalize(payload).replace(",", " ")
    payload = " ".join(payload.split())
    if not payload:
        return ""
    book_part, locator = split_payload(payload)
    if not locator:
        return payload

    tokens = locator.split()
    out: list[str] = []
    i = 0
    while i < len(tokens):
        letter = _letter_of(tokens[i])
        if letter:
            run = [letter]
            j = i + 1
            while j < len(tokens):
                nxt = _letter_of(tokens[j])
                if nxt is None:
                    break
                run.append(nxt)
                j += 1
            prev_is_keyword = (
                i > 0 and _LOCATOR_RE.match(_fold_word(tokens[i - 1])) is not None
            )
            next_is_edge = (
                j >= len(tokens)
                or _LOCATOR_RE.match(_fold_word(tokens[j])) is not None
            )
            # רצף של 2+ שמות-אותיות, או אות יחידה בסמיכות למילת מיקום/סוף
            if len(run) >= 2 or prev_is_keyword or next_is_edge:
                letters = "".join(run)
                out.append(
                    letters if len(letters) == 1 else letters[:-1] + '"' + letters[-1]
                )
                i = j
                continue
        if _DIGITS_RE.match(tokens[i]):
            out.append(to_hebrew_numeral(int(tokens[i])))
        else:
            # ספרות עבריות בלי גרשיים ('כז') → 'כ"ז' — רק במשבצת מספרית,
            # ורק אם זו לא שם-ספר מוכר ('בבא מציעא', 'תמיד'...)
            prev_is_kw = (
                i > 0 and _LOCATOR_RE.match(_fold_word(tokens[i - 1])) is not None
            )
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            is_bookish = _is_exact_alias(tokens[i]) or (
                bool(nxt) and _is_exact_alias(f"{tokens[i]} {nxt}")
            )
            formatted = (
                _format_bare_numeral(tokens[i])
                if (i == 0 or prev_is_kw) and not is_bookish
                else None
            )
            out.append(formatted or tokens[i])
        i += 1

    loc = " ".join(out)
    # דף/סימן/פרק: מספר עברי בראש ה-locator — לפי סגנון המיקום של הספר
    if out and _GERSHAYIM_NUM_RE.match(out[0]):
        entry = find_book(book_part)
        if entry is not None and entry.sections:
            loc = f"{entry.sections[0]} {loc}"
    return f"{book_part} {loc}".strip()


# ============================================================
# 4. הקטלוג עצמו (~120 רשומות)
# ============================================================
# נזרע מ-TORAH_TERMS_PROMPT + שמות בר-אילן סטנדרטיים.
# הוספת רשומה = שורה אחת ב-_make(...).

_MASECHTOT = [
    # זרעים
    "ברכות", "פאה", "דמאי", "כלאים", "שביעית", "תרומות", "מעשרות",
    "מעשר שני", "חלה", "ערלה", "ביכורים",
    # מועד
    "שבת", "עירובין", "פסחים", "שקלים", "יומא", "סוכה", "ביצה",
    "ראש השנה", "תענית", "מגילה", "מועד קטן", "חגיגה",
    # נשים
    "יבמות", "כתובות", "נדרים", "נזיר", "סוטה", "גיטין", "קידושין",
    # נזיקין
    "בבא קמא", "בבא מציעא", "בבא בתרא", "סנהדרין", "מכות", "שבועות",
    "הוריות", "עדויות", "אבות", "עבודה זרה",
    # קדשים
    "זבחים", "מנחות", "חולין", "בכורות", "ערכין", "תמורה", "כריתות",
    "מעילה", "תמיד", "מידות",
    # טהרות
    "כלים", "אהלות", "נגעים", "פרה", "טהרות", "מקואות", "נדה",
    "מכשירין", "זבים", "טבול יום", "ידיים", "עוקצין",
]

_MASECHET_EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "בבא קמא": ('ב"ק',),
    "בבא מציעא": ('ב"מ',),
    "בבא בתרא": ('ב"ב',),
    "ראש השנה": ('ר"ה',),
    "עבודה זרה": ('ע"ז',),
    "מועד קטן": ('מו"ק',),
    "קידושין": ("קדושין",),
}

BOOKS: list[BookEntry] = []

# --- חומשים (מיקום: פרק/פסוק) ---
BOOKS += [
    _make("בראשית", "tanakh", (), ('חומש בראשית', 'ספר בראשית')),
    _make("שמות", "tanakh", (), ('חומש שמות', 'ספר שמות')),
    _make("ויקרא", "tanakh", (), ('חומש ויקרא', 'ספר ויקרא')),
    _make("במדבר", "tanakh", (), ('חומש במדבר', 'ספר במדבר')),
    _make("דברים", "tanakh", (), ('חומש דברים', 'ספר דברים')),
]

# --- תלמוד בבלי (63 מכתחי משנה, כל מחלקה בשני כרכים) ---
for _name in _MASECHTOT:
    BOOKS.append(_make(_name, "masechet", ("א", "ב"), _MASECHET_EXTRA_ALIASES.get(_name, ())))

# --- ארבעת חלקי שולחן ערוך ---
BOOKS += [
    _make('שו"ע או"ח', "shulchan_aruch", (),
          ('אורח חיים', 'שולחן ערוך או"ח', 'שולחן ערוך אורח חיים', 'או"ח', 'שולחן ערוך')),
    _make('שו"ע יו"ד', "shulchan_aruch", (),
          ('יורה דעה', 'שולחן ערוך יו"ד', 'שולחן ערוך יורה דעה', 'יו"ד')),
    _make('שו"ע אבן העזר', "shulchan_aruch", (),
          ('אבן העזר', 'אה"ע', 'אב"ע', 'שולחן ערוך אבן העזר')),
    _make('שו"ע חושן משפט', "shulchan_aruch", (),
          ('חושן משפט', 'חשן משפט', 'חו"מ', 'שולחן ערוך חושן משפט')),
]

# --- פרשנים וספרי יסוד ---
BOOKS += [
    _make("רש\"י", "other", (), ('רש"י', 'פירוש רש"י')),
    _make("תוספות", "talmud_commentary", (), ('תוס', "תוס'", 'חידושי התוספות')),
    _make("רמב\"ם", "other", (), ('רמב"ם', 'משנה תורה', 'יד החזקה', 'היד החזקה')),
    _make("רמב\"ן", "other", (), ('רמב"ן', 'נימוקי יוסף')),
    _make("משנה", "other", (), ('משנה', 'ששה סדרי משנה')),
    _make("תלמוד בבלי", "other", (), ('בבלי', 'גמרא', 'תלמוד בבלי', 'תלמוד')),
    _make("תלמוד ירושלמי", "other", (), ('ירושלמי', 'תלמוד ירושלמי')),
    _make("זוהר", "other", (), ('זוהר', 'זהר', 'זוהר הקדוש')),
    _make("מדרש", "other", (), ('מדרש', 'מדרש רבה', 'בראשית רבה')),
    _make("תורה", "other", (), ('תורה', 'חומש', 'חמישה חומשי תורה')),
    _make("נביאים", "other", (), ('נביאים',)),
    _make("כתובים", "other", (), ('כתובים',)),
    _make("משנה ברורה", "other", (), ('משנ"ב', 'משנה ברורה')),
    _make("באר היטב", "other", (), ('באר"ה', 'באר היטב')),
    _make('ט"ז', "other", (), ("טורי זהב",)),
    _make('ש"ך', "other", (), ("שפתי כהן",)),
    _make("ערוך השולחן", "other", (), ('ערוה"ש', 'ערוך השולחן')),
    _make('מהרש"א', "talmud_commentary", (), ('חידושי מהרש"א',)),
    _make('מהרש"ל', "talmud_commentary", (), ('חידושי מהרש"ל',)),
    _make("חלקת מחוקק", "other", (), ('ח"מ', 'חלקת מחוקק')),
    _make("בית שמואל", "other", (), ('ב"ש', 'בית שמואל')),
]

# --- מפרשי התלמוד: ראשונים ואחרונים (רי"ף, רא"ש, ר"ן, ריטב"א...) ---
BOOKS += [
    _make('רי"ף', "talmud_commentary", (), ('הרי"ף', 'רבינו יצחק אלפסי', 'אלפסי')),
    _make('רא"ש', "talmud_commentary", (), ('הרא"ש', 'רבינו אשר')),
    _make('ר"ן', "talmud_commentary", (), ('הרן', 'חידושי הר"ן', 'רבנו נסים')),
    _make('ריטב"א', "talmud_commentary", (), ('חידושי הריטב"א',)),
    _make('רשב"א', "talmud_commentary", (), ('חידושי הרשב"א',)),
    _make('רשב"ם', "talmud_commentary", (), ('חידושי הרשב"ם',)),
    _make('תוספות רי"ד', "talmud_commentary", (), ('רי"ד', 'תוספות ריד')),
    _make('ר"ח', "talmud_commentary", (), ('רבנו חננאל',)),
    _make('מאירי', "talmud_commentary", (), ('בית הבחירה',)),
    _make('בעל המאור', "talmud_commentary", ()),
    _make('נימוקי יוסף', "talmud_commentary", ()),
    _make('רבינו יונה', "talmud_commentary", ()),
    _make('מהר"י בן לב', "talmud_commentary", (), ('בן לב',)),
    _make('חידושי אנשי שם', "talmud_commentary", (), ('אנשי שם',)),
]

# --- ספרי שו\"ת ---
BOOKS += [
    _make("אגרות משה", "responsa", (), ('אג"מ', 'אגרות משה')),
    _make("יביע אומר", "responsa", (), ('יבי"א', 'יביע אומר')),
    _make("חתם סופר", "responsa", (), ('חת"ם סופר', 'חתם סופר', 'שו"ת חתם סופר')),
    _make("שו\"ת הרשב\"א", "responsa", (), ('הרשב"א', 'תשובות הרשב"א', 'שו"ת הרשב"א')),
    _make("שו\"ת הרמב\"ן", "responsa", (), ('הרמב"ן', 'תשובות הרמב"ן', 'שו"ת הרמב"ן')),
    _make("משנה הלכות", "responsa", (), ('משנה הלכות',)),
    _make("מנחת יצחק", "responsa", (), ('מנח"י', 'מנחת יצחק')),
    _make("ציץ אליעזר", "responsa", (), ('ציץ אליעזר', 'ציץ אליעזר')),
    _make("שלמת חיים", "responsa", (), ('שלמת חיים',)),
    _make("משפטי עוזיאל", "responsa", (), ('משפטי עוזיאל',)),
    _make("אחיעזר", "responsa", (), ('אחיעזר',)),
    _make("שואל ומשיב", "responsa", (), ('שואל ומשיב', 'שו"ת שואל ומשיב')),
    _make("אבני נזר", "responsa", (), ('אבני נזר',)),
    _make("עין יצחק", "responsa", (), ('עין יצחק',)),
    _make("מלמד להועיל", "responsa", (), ('מלמד להועיל',)),
    _make("נודע ביהודה", "responsa", (), ('נו"ב', 'נודע ביהודה')),
    _make("דברי חיים", "responsa", (), ('דברי חיים',)),
    _make("חכמת שלמה", "responsa", (), ('חכמת שלמה',)),
    _make("רבי עקיבא איגר", "responsa", (), ('עקיבא איגר', 'רא"א', 'ר"ע איגר')),
    _make("אגרות קודש", "responsa", (), ('אגרות קודש',)),
    _make("קול סיני", "responsa", (), ('קול סיני',)),
    _make("אמרי יושר", "responsa", (), ('אמרי יושר',)),
    _make("ארץ צבי", "responsa", (), ('ארץ צבי',)),
    _make("ישכיל עבדי", "responsa", (), ('ישכיל עבדי',)),
    _make("אגלי טל", "responsa", (), ('אגלי טל',)),
]


# ============================================================
# 4b. טעינת books_data.json (~1000 ספרים מ-Sefaria, ראה build_books_data.py)
# ============================================================
# הקטלוג הידני למעלה תמיד מנצח (dedup לפי canonical); הנתונים מוסיפים
# את שאר הספרים יחד עם סגנון המיקום שלהם (דף/עמוד, סימן/סעיף, פרק/פסוק...).

def _load_data_entries() -> list[BookEntry]:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        path = Path(sys._MEIPASS) / "books_data.json"  # type: ignore[attr-defined]
    else:
        path = Path(__file__).resolve().parent / "books_data.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[BookEntry] = []
    for row in raw:
        try:
            canonical = row["canonical"]
            aliases = list(row.get("aliases") or (canonical,))
            category = row.get("category") or "other"
            # רמב"ם: הצורה המדוברת ('רמב"ם הלכות שבת') עדיפה על 'משנה תורה...'
            if category == "rambam" and canonical.startswith("משנה תורה "):
                aliases.append(canonical)
                canonical = 'רמב"ם ' + canonical[len("משנה תורה "):]
            out.append(BookEntry(
                canonical=canonical,
                aliases=tuple(dict.fromkeys(aliases)),
                category=category,
                volumes=tuple(row.get("volumes") or ()),
                sections=tuple(row.get("sections") or ()),
            ))
        except (KeyError, TypeError):
            continue
    return out


def _merge_data() -> int:
    known: set[str] = set()
    for e in BOOKS:
        for alias in e.aliases:
            known.add(_fold_strip(alias))
    added = 0
    for e in _load_data_entries():
        key = _fold_strip(e.canonical)
        if not key or key in known:
            continue
        BOOKS.append(e)
        known.add(key)
        for alias in e.aliases:
            known.add(_fold_strip(alias))
        added += 1
    return added


_MERGED_COUNT = _merge_data()
_rebuild_alias_index()


# ============================================================
# 5. בדיקות עצמיות
# ============================================================

def _selftest() -> int:
    failures = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"[PASS] {name}")
        else:
            failures += 1
            print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))

    # --- פיצול payload ---
    split_cases = [
        ('ברכות ב עמוד ע"א', ("ברכות ב", 'עמוד ע"א')),
        ('שו"ע או"ח של"א', ('שו"ע או"ח', 'של"א')),
        ("ברכות ב", ("ברכות ב", "")),
        ("שלום", ("שלום", "")),
        ('אגרות משה יו"ד סימן כ', ("אגרות משה", 'יו"ד סימן כ')),
    ]
    for payload, (exp_book, exp_loc) in split_cases:
        book, loc = split_payload(payload)
        check(
            f"split '{payload}'",
            book == exp_book and loc == exp_loc,
            f"התקבל ('{book}', '{loc}')",
        )

    # --- הצמדה מקומית ---
    snap = snap_payload("ברכות ב")
    check(
        "snap 'ברכות ב' → ברכות",
        bool(snap) and snap[0][1].canonical == "ברכות" and snap[0][2] == "ברכות ב",
        f"התקבל {snap[:2]}",
    )

    snap = snap_payload('שעה אוח של א')
    check(
        "snap 'שעה אוח של א' → שו\"ע או\"ח",
        bool(snap) and snap[0][1].canonical == 'שו"ע או"ח',
        f"התקבל {snap[:2]}",
    )

    snap = snap_payload('ברכות אלף')
    check(
        "snap 'ברכות ألف' → 'ברכות א'",
        bool(snap) and snap[0][2] == "ברכות א",
        f"התקבל {snap[:2]}",
    )

    snap = snap_payload('ברכות בית שמואל')
    check(
        "snap 'ברכות בית שמואל' — 'בית' לא מתעוות",
        bool(snap) and snap[0][2] == "ברכות בית שמואל",
        f"התקבל {snap[:2]}",
    )

    snap = snap_payload('עמוד ע"א')
    check("snap ללא ספר → ריק", snap == [], f"התקבל {snap}")

    # --- נרמול ייחוס תורני ---
    check(
        "normalize 'יבמות יוד דלת עמוד בית'",
        normalize_reference("יבמות יוד דלת עמוד בית") == 'יבמות דף י"ד עמוד ב',
        normalize_reference("יבמות יוד דלת עמוד בית"),
    )
    check(
        "normalize 'יבמות דף 14 עמוד 2'",
        normalize_reference("יבמות דף 14 עמוד 2") == 'יבמות דף י"ד עמוד ב',
        normalize_reference("יבמות דף 14 עמוד 2"),
    )
    check(
        "normalize 'שו\"ע או\"ח של\"א' → סימן",
        normalize_reference('שו"ע או"ח של"א') == 'שו"ע או"ח סימן של"א',
        normalize_reference('שו"ע או"ח של"א'),
    )
    check(
        "normalize 'ברכות בית שמואל' — ללא עיוות",
        normalize_reference("ברכות בית שמואל") == "ברכות בית שמואל",
        normalize_reference("ברכות בית שמואל"),
    )
    check(
        "normalize לא ממציא דף כשיש עמוד",
        normalize_reference('ברכות ב עמוד ע"א') == 'ברכות ב עמוד ע"א',
        normalize_reference('ברכות ב עמוד ע"א'),
    )
    check(
        "has_spoken_numbers: 'שלוש מאות ואחת' → True",
        has_spoken_numbers("סימן שלוש מאות ואחת"),
    )
    check(
        "has_spoken_numbers: 'ברכות א' → False",
        not has_spoken_numbers("ברכות א"),
    )
    check(
        "has_spoken_numbers: 'ברכות ألف' (אות כרך) → False",
        not has_spoken_numbers("ברכות ألف"),
    )

    # --- בלוק פרומפט ---
    block = build_catalog_block(BOOKS[:2])
    check(
        "בלוק פרומפט עם סגנון מיקום",
        "בראשית" in block and "מיקום: פרק/פסוק" in block,
        block,
    )

    check(
        "normalize 'ריטב\"א בבא מציעא נח' — שם מחלקה לא מתעוות",
        normalize_reference('ריטב"א בבא מציעא נח') == 'ריטב"א בבא מציעא נח',
        normalize_reference('ריטב"א בבא מציעא נח'),
    )
    check(
        "normalize 'שו\"ע יו\"ד סימן פז סעיף ג' → גרשיים",
        normalize_reference('שו"ע יו"ד סימן פז סעיף ג') == 'שו"ע יו"ד סימן פ"ז סעיף ג',
        normalize_reference('שו"ע יו"ד סימן פז סעיף ג'),
    )
    check(
        "normalize 'בראשית כז ג' → 'בראשית פרק כ\"ז ג'",
        normalize_reference("בראשית כז ג") == 'בראשית פרק כ"ז ג',
        normalize_reference("בראשית כז ג"),
    )
    check(
        "normalize 'של א' — 'של' לא מתעוות למספר",
        normalize_reference('שו"ע או"ח של א') == 'שו"ע או"ח של א',
        normalize_reference('שו"ע או"ח של א'),
    )
    b = find_book('רמב"ם הלכות שבת')
    check(
        "רמב\"ם: canonical מדובר ('רמב\"ם הלכות שבת')",
        b is not None and b.canonical == 'רמב"ם הלכות שבת',
        f"התקבל {b and b.canonical}",
    )

    # --- תמיכה בחומש / רמב"ם / יו"ד ---
    b = find_book("בראשית")
    check(
        "חומש: 'בראשית' → פרק/פסוק",
        b is not None and b.sections == ("פרק", "פסוק"),
        f"התקבל {b}",
    )
    check(
        "חומש: snap 'בראשית כ\"ז' → 'בראשית פרק כ\"ז'",
        bool(snap_payload('בראשית כ"ז')) and snap_payload('בראשית כ"ז')[0][2] == 'בראשית פרק כ"ז',
        f"התקבל {snap_payload('בראשית כ\"ז')[:1]}",
    )
    b = find_book("שולחן ערוך יורה דעה")
    check(
        "יו\"ד: 'שולחן ערוך יורה דעה' → שו\"ע יו\"ד",
        b is not None and b.canonical == 'שו"ע יו"ד' and b.sections == ("סימן", "סעיף"),
        f"התקבל {b}",
    )
    b = find_book('רמב"ם הלכות שבת')
    check(
        "רמב\"ם: 'רמב\"ם הלכות שבת' → פרק/הלכה",
        b is not None and b.sections == ("פרק", "הלכה"),
        f"התקבל {b}",
    )
    check(
        "books_data.json נטען",
        _MERGED_COUNT >= 800 and find_book("מדרש תנחומא") is not None,
        f"מוזגו {_MERGED_COUNT}",
    )

    # --- תקינות הקטלוג ---
    check("הקטלוג לא ריק", len(BOOKS) >= 1000, f"נמצאו {len(BOOKS)}")
    check(
        "אין כינויים שבורים (אותיות בודדות)",
        all(len(a) >= 2 for e in BOOKS for a in e.aliases),
    )
    check(
        "אזור מפרשי התלמוד קיים",
        find_book('רי"ף') is not None
        and find_book('רי"ף').category == "talmud_commentary"
        and find_book('ריטב"א').category == "talmud_commentary"
        and find_book("תוספות").category == "talmud_commentary",
    )
    check(
        "כל הרשומות עם canonical ו-aliases",
        all(e.canonical and e.aliases and e.category in _CATEGORY_HE for e in BOOKS),
    )

    print("\n" + "=" * 50)
    if failures:
        print(f"  סיכום: {failures} בדיקות נכשלו")
    else:
        print("  סיכום: כל הבדיקות עברו")
    print("=" * 50)
    return 1 if failures else 0


# ============================================================
# 6. CLI
# ============================================================

def main() -> int:
    import argparse

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="קטלוג הספרים של פרויקט השו\"ת"
    )
    parser.add_argument("--selftest", action="store_true", help="בדיקות offline")
    parser.add_argument("--snap", type=str, help="הצמדת payload לקטלוג")
    parser.add_argument("--block", action="store_true", help="הדפסת בלוק הקטלוג לפרומפט")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    if args.snap:
        results = snap_payload(args.snap)
        for score, entry, suggested in results:
            print(f"{score:.3f}  {entry.canonical}  →  {suggested}")
        return 0

    if args.block:
        print(build_catalog_block(BOOKS))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
