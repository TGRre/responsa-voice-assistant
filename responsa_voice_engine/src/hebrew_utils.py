"""Hebrew text utilities: cleaning, normalization, phonetic folding, gematria."""

from __future__ import annotations

import re
import unicodedata

from src.models import Action, CleanedCommand

# ---------------------------------------------------------------------------
# Precompiled regexes (hot-path safe: compiled once at import)
# ---------------------------------------------------------------------------

_NIQQUD_RE = re.compile(r"[\u0591-\u05C7]")
_PUNCT_RE = re.compile(r"[\"״'׳\u2019\-\.,!?;:]")
_GERSHAYIM_RE = re.compile(r"[\"״׳'\u2019]")
_SPACES_RE = re.compile(r"\s+")
_STRAY_PUNCT_RE = re.compile(r"[,;:\.\?\!]")
_CONNECTOR_AL_RE = re.compile(r"\s+על\s+")

# Quote/geresh/gershayim character class for structural normalization
_AQ = r"['\"\u2019\u05f3\u05f4]"
_END = r"(?=\s|$)"

# Structural fix patterns
_DAF_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bת{_AQ}{_END}"), "דף"),
    (re.compile(rf"\bד{_AQ}{_END}"), "דף"),
    (re.compile(r"\bדפ\b"), "דף"),
]
_AMUD_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bאמוד\b"), "עמוד"),
    (re.compile(rf"\bע{_AQ}א{_END}"), "עמוד א"),
    (re.compile(r"\bעמוד\s+אלף\b"), "עמוד א"),
    (re.compile(r"\bעמוד\s+1\b"), "עמוד א"),
    (re.compile(r"\bעמוד\s+ראשון\b"), "עמוד א"),
    (re.compile(rf"\bעמוד\s+א{_AQ}{_END}"), "עמוד א"),
    (re.compile(rf"\bע{_AQ}ב{_END}"), "עמוד ב"),
    (re.compile(r"\bעמוד\s+בית\b"), "עמוד ב"),
    (re.compile(r"\bעמוד\s+2\b"), "עמוד ב"),
    (re.compile(r"\bעמוד\s+שני\b"), "עמוד ב"),
    (re.compile(rf"\bעמוד\s+ב{_AQ}{_END}"), "עמוד ב"),
]
# Bare עא/עב (without quotes) are ONLY amud abbreviations when NOT preceded by
# a number-expecting structural marker (otherwise עא=71, עב=72 in gematria!)
_BARE_AMUD_RE = re.compile(
    r"(?:(דף|פרק|סימן|פסוק|הלכה|סעיף_קטן|סעיף)\s+)?\b(עא|עב)\b"
)
_PEREK_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bפרק\s+ראשון\b"), "פרק א"),
    (re.compile(r"\bפרק\s+אלף\b"), "פרק א"),
    (re.compile(r"\bפרק\s+1\b"), "פרק א"),
    (re.compile(r"\bפרק\s+שני\b"), "פרק ב"),
    (re.compile(r"\bפרק\s+בית\b"), "פרק ב"),
    (re.compile(r"\bפרק\s+2\b"), "פרק ב"),
]
_HALACHA_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bהלכה\s+שנייה\b"), "הלכה ב"),
    (re.compile(r"\bהלכה\s+בית\b"), "הלכה ב"),
    (re.compile(r"\bהלכה\s+2\b"), "הלכה ב"),
    (re.compile(r"\bהלכה\s+ראשונה\b"), "הלכה א"),
    (re.compile(r"\bהלכה\s+אלף\b"), "הלכה א"),
    (re.compile(r"\bהלכה\s+1\b"), "הלכה א"),
]
_PASUK_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bפס{_AQ}{_END}"), "פסוק"),
    (re.compile(r"\bפסוק\s+ראשון\b"), "פסוק א"),
    (re.compile(r"\bפסוק\s+אלף\b"), "פסוק א"),
    (re.compile(r"\bפסוק\s+1\b"), "פסוק א"),
]
_SIMAN_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bסי{_AQ}{_END}"), "סימן"),
    (re.compile(rf"\bסימ{_AQ}{_END}"), "סימן"),
    (re.compile(r"\b(?:עימן|שימן|צימן|סמן)\b"), "סימן"),
]
_SEIF_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bסע{_AQ}{_END}"), "סעיף"),
    (re.compile(rf"\bס{_AQ}ק{_END}"), "סעיף_קטן"),
    (re.compile(r"\bסק\b"), "סעיף_קטן"),
    (re.compile(r"\bסעיף\s+קטן\b"), "סעיף_קטן"),
    (re.compile(rf"\bסעיף\s+ק{_AQ}{_END}"), "סעיף_קטן"),
]
_PARASHA_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[בל]פרשת\s+השבוע\b"), "פרשת"),
    (re.compile(r"\bפרשת\s+השבוע\b"), "פרשת"),
    (re.compile(r"\b[בל]פרשת\b"), "פרשת"),
    (re.compile(r"\b[בל]?פרשה\b"), "פרשת"),
]
_SPLIT_BOOK_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(שמואל|מלכים|דברי\s+הימים)\s+(?:אלף|ראשון|1)\b"), r"\1 א"),
    (re.compile(r"(שמואל|מלכים|דברי\s+הימים)\s+(?:בית|שני|2)\b"), r"\1 ב"),
]

# Anchored action phrase patterns (start of utterance after optional lead-in)
_LEAD = r"^(?:(?:בבקשה|אני\s+רוצה)[\s,:]+)*"
_ACT_END = r"(?=\s|[,.;:!?]|$)"

_ACTION_PATTERNS: list[tuple[re.Pattern[str], Action]] = [
    (re.compile(rf"^(?:בבקשה[\s,:]+)?אני\s+רוצה\s+לפתוח(?:\s+לי)?{_ACT_END}"), Action.OPEN),
    (re.compile(rf"{_LEAD}(?:פתח|תפתחי?|יפתח|אפתח|נפתח|לפתוח|פתוח|תפתי|תשלחי?)\s+לי{_ACT_END}"), Action.OPEN),
    (re.compile(rf"{_LEAD}(?:פתח|תפתחי?|יפתח|אפתח|נפתח|לפתוח|פתוח|תפתי|תשלחי?){_ACT_END}"), Action.OPEN),
    (re.compile(rf"{_LEAD}ת?ביא(?:י)?\s+לי{_ACT_END}"), Action.OPEN),
    (re.compile(rf"{_LEAD}ת?עבור(?:י)?\s+אל{_ACT_END}"), Action.NAVIGATE),
    (re.compile(rf"{_LEAD}ת?עבור(?:י)?\s+ל"), Action.NAVIGATE),
    (re.compile(rf"{_LEAD}ת?קפוץ\s+ל"), Action.NAVIGATE),
    (
        re.compile(
            rf"{_LEAD}(?:[תנאימ]?חפש(?:י)?|לחפש|[תנאי]?מצא(?:י)?|מוצא|למצוא|תמצוא)(?:\s+לי)?{_ACT_END}"
        ),
        Action.SEARCH,
    ),
]

_SEARCH_LEAD_FILLER_RE = re.compile(
    r"^(?:(?:בבקשה|אה|אממ|את\s+המילה|את\s+הביטוי|המילה|הביטוי|את)[\s,:]+)+"
)

_FILLERS = (
    "בבקשה",
    "את",
    "אה",
    "אממ",
    "הספר",
    "מסכת",
    "במסכת",
)
_FILLERS_RE = re.compile(r"\b(?:" + "|".join(re.escape(f) for f in _FILLERS) + r")\b")

# Hebrew gematria letter values
_GEMATRIA_VALUES: dict[str, int] = {
    "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
    "י": 10, "כ": 20, "ל": 30, "מ": 40, "נ": 50, "ס": 60, "ע": 70, "פ": 80,
    "צ": 90, "ק": 100, "ר": 200, "ש": 300, "ת": 400,
    "ך": 20, "ם": 40, "ן": 50, "ף": 80, "ץ": 90,
}

# Spoken number words (both genders) — no single Hebrew letters (handled by gematria)
_SPOKEN_ONES: dict[str, int] = {
    "אחת": 1, "אחד": 1, "ראשון": 1, "ראשונה": 1,
    "ואחת": 1, "ואחד": 1,
    "שתיים": 2, "שנים": 2, "שניים": 2, "שני": 2, "שנייה": 2, "שתים": 2,
    "ושתיים": 2, "ושנים": 2, "ושניים": 2,
    "שלוש": 3, "שלושה": 3, "שלישית": 3, "שלישי": 3,
    "ושלוש": 3, "ושלושה": 3,
    "ארבע": 4, "ארבעה": 4, "רביעית": 4, "רביעי": 4,
    "וארבע": 4, "וארבעה": 4,
    "חמש": 5, "חמישה": 5, "חמישית": 5, "חמישי": 5,
    "וחמש": 5, "וחמישה": 5,
    "שש": 6, "שישה": 6, "שישית": 6, "שישי": 6,
    "ושש": 6, "ושישה": 6,
    "שבע": 7, "שבעה": 7, "שביעית": 7, "שביעי": 7,
    "ושבע": 7, "ושבעה": 7,
    "שמונה": 8, "שמינית": 8, "שמיני": 8,
    "ושמונה": 8,
    "תשע": 9, "תשעה": 9, "תשיעית": 9, "תשיעי": 9,
    "ותשע": 9, "ותשעה": 9,
}

# Hebrew letter names → gematria values (for spoken "למד אלף" = ל א = 31)
_HEBREW_LETTER_NAMES: dict[str, int] = {
    "אלף": 1,
    "בית": 2, "בת": 2,
    "גימל": 3, "גמל": 3,
    "דלת": 4,
    "הא": 5, "הי": 5,
    "וו": 6, "ואו": 6,
    "זין": 7,
    "חית": 8, "חת": 8,
    "טית": 9, "טת": 9,
    "יוד": 10,
    "כף": 20,
    "למד": 30,
    "מם": 40,
    "נון": 50,
    "סמך": 60,
    "עין": 70,
    "פה": 80, "פא": 80,
    "צדי": 90, "צדיק": 90,
    "קוף": 100,
    "ריש": 200,
    "שין": 300,
    "תיו": 400, "תו": 400,
}

_SPOKEN_TEENS: dict[str, int] = {
    "עשרה": 10, "עשר": 10, "עשירית": 10, "עשירי": 10,
    "אחת_עשרה": 11, "אחד_עשר": 11,
    "שתים_עשרה": 12, "שנים_עשר": 12, "שתיים_עשרה": 12, "שניים_עשר": 12,
    "שלוש_עשרה": 13, "שלושה_עשר": 13,
    "ארבע_עשרה": 14, "ארבעה_עשר": 14,
    "חמש_עשרה": 15, "חמישה_עשר": 15,
    "שש_עשרה": 16, "שישה_עשר": 16,
    "שבע_עשרה": 17, "שבעה_עשר": 17,
    "שמונה_עשרה": 18, "שמונה_עשר": 18,
    "תשע_עשרה": 19, "תשעה_עשר": 19,
}

_SPOKEN_TENS: dict[str, int] = {
    "עשרים": 20, "שלושים": 30, "ארבעים": 40, "חמישים": 50,
    "שישים": 60, "שבעים": 70, "שמונים": 80, "תשעים": 90,
}

_ORDINAL_TO_CARDINAL: dict[str, str] = {
    "ראשון": "אחד", "ראשונה": "אחת",
    "שני": "שנים", "שנייה": "שתיים",
    "שלישי": "שלושה", "שלישית": "שלוש",
    "רביעי": "ארבעה", "רביעית": "ארבע",
    "חמישי": "חמישה", "חמישית": "חמש",
    "שישי": "שישה", "שישית": "שש",
    "שביעי": "שבעה", "שביעית": "שבע",
    "שמיני": "שמונה", "שמינית": "שמונה",
    "תשיעי": "תשעה", "תשיעית": "תשע",
    "עשירי": "עשרה", "עשירית": "עשר",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clean_spoken_command(text: str) -> CleanedCommand:
    """Extract action phrase and strip fillers from a raw Whisper transcript."""
    text = text.strip()
    action = Action.OPEN  # default
    for pattern, act in _ACTION_PATTERNS:
        m = pattern.match(text)
        if m:
            action = act
            text = text[m.end():].lstrip(" ,;:")
            break

    if action == Action.SEARCH:
        # Preserve interior words like "על" / "מסכת" / "את" for literal Ctrl+F search;
        # only strip leading conversational fillers ("בבקשה", "את", "את המילה").
        text = _SEARCH_LEAD_FILLER_RE.sub("", text)
        text = _SPACES_RE.sub(" ", text).strip()
        return CleanedCommand(action=action, text=text)

    # Strip fillers (precompiled single regex)
    text = _FILLERS_RE.sub(" ", text)

    # Strip connector preposition 'על' between commentary and book
    # e.g. 'רש"י על חולין' → 'רש"י חולין'
    text = _CONNECTOR_AL_RE.sub(" ", text)

    text = _SPACES_RE.sub(" ", text).strip()
    return CleanedCommand(action=action, text=text)


def normalize_key(text: str) -> str:
    """Strip niqqud and punctuation for exact-match key generation."""
    text = unicodedata.normalize("NFKD", text)
    text = _NIQQUD_RE.sub("", text)
    text = _PUNCT_RE.sub("", text)
    return _SPACES_RE.sub(" ", text).strip()


def normalize_phonetic(text: str) -> str:
    """Fold Hebrew letters to phonetic equivalence classes for fuzzy matching.

    Merges: finals→regular, ט→ת, א/ע→א, כ/ק→כ.
    Never merges: ח↔ק (directly or transitively), ב↔ו.
    """
    text = normalize_key(text)
    result = []
    for ch in text:
        if ch in "םןץףך":
            result.append({"ם": "מ", "ן": "נ", "ץ": "צ", "ף": "פ", "ך": "כ"}[ch])
        elif ch == "ט":
            result.append("ת")
        elif ch == "ע":
            result.append("א")
        elif ch == "ק":
            result.append("כ")
        else:
            result.append(ch)
    return "".join(result)


def strip_prefix_variants(token: str) -> list[str]:
    """Return [original, stripped...] for Hebrew prefixes ה/ב/ל/מ.

    The original form is always first so matching tries it before stripped
    variants, preventing corruption of real words (ברכות, מגילה, הלכות).
    """
    if len(token) < 3:
        return [token]
    variants = [token]
    for prefix in "הבלמ":
        if token[0] == prefix and len(token) > 2:
            stripped = token[1:]
            if stripped not in variants:
                variants.append(stripped)
    return variants


def _replace_bare_amud(match: re.Match[str]) -> str:
    # If preceded by a number-expecting structural word, עא=71 / עב=72 is a number!
    if match.group(1):
        return match.group(0)
    return "עמוד א" if match.group(2) == "עא" else "עמוד ב"


def fix_structural_errors(text: str) -> str:
    """Fix common Whisper mis-transcriptions of structural markers."""
    # Strip commas, periods, question/exclamation marks, semicolons, colons
    # (preserves quotes/geresh/gershayim until structural regexes run)
    text = _STRAY_PUNCT_RE.sub(" ", text)
    for pattern, repl in _DAF_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _AMUD_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _PEREK_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _HALACHA_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _PASUK_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _SIMAN_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _SEIF_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _PARASHA_FIXES:
        text = pattern.sub(repl, text)
    for pattern, repl in _SPLIT_BOOK_FIXES:
        text = pattern.sub(repl, text)
    # Run bare עא/עב replacement AFTER structural words (דף/פרק/סימן/...) are normalized
    text = _BARE_AMUD_RE.sub(_replace_bare_amud, text)
    return _SPACES_RE.sub(" ", text).strip()


def is_valid_gematria_order(token: str) -> bool:
    """Return True if `token` is a pure Arabic numeral or canonical-order Hebrew gematria.

    Canonical Hebrew gematria (1-999) has strictly descending letter values,
    with the sole exception of repeated ת (400+400 for 800-999).
    Prevents words like 'רשי' (200<300), 'שבת' (2<400), or 'בק' (2<100) from
    being treated as shorthand numbers.
    """
    cleaned = _GERSHAYIM_RE.sub("", token).strip(".,!?;: \t\n\r")
    if not cleaned or len(cleaned) > 5:
        return False
    if cleaned.isdigit():
        n = int(cleaned)
        return 1 <= n <= 999
    prev = 1000
    total = 0
    for ch in cleaned:
        val = _GEMATRIA_VALUES.get(ch)
        if val is None:
            return False
        if val > prev or (val == prev and val != 400):
            return False
        prev = val
        total += val
    return 1 <= total <= 999


def parse_hebrew_number(token: str) -> int | None:
    """Parse a Hebrew gematria or Arabic numeral token to an integer (≤999).

    Handles: standard gematria, טו/טז, geresh/gershayim, Arabic digits,
    and strips trailing punctuation.
    Only accepts short tokens (≤5 chars) to avoid matching spoken Hebrew words.
    Returns None if the token is not a valid number.
    """
    token = _GERSHAYIM_RE.sub("", token).strip(".,!?;: \t\n\r")
    if not token or len(token) > 5:
        return None

    # Arabic numerals
    if token.isdigit():
        n = int(token)
        return n if 1 <= n <= 999 else None

    # Hebrew gematria — only pure gematria letters
    total = 0
    for ch in token:
        val = _GEMATRIA_VALUES.get(ch)
        if val is None:
            return None
        total += val

    if total == 0 or total > 999:
        return None

    return total


def _clean_num_tok(tok: str) -> str:
    return _GERSHAYIM_RE.sub("", tok).strip(".,!?;: \t\n\r")


def _strip_optional_vav(tok: str) -> str:
    if len(tok) > 1 and tok[0] == "ו":
        return tok[1:]
    return tok


def _single_gematria_value(tok: str) -> int | None:
    cleaned = _clean_num_tok(tok)
    if len(cleaned) == 1:
        return _GEMATRIA_VALUES.get(cleaned)
    return None


def parse_spoken_number(
    words: list[str],
    i: int,
    *,
    allow_spaced_gematria: bool = True,
) -> tuple[int, int] | None:
    """Parse a spoken Hebrew number, Arabic numeral, letter-name sequence, or
    spaced single-letter gematria starting at words[i].

    Uses a strict descending-magnitude state machine so unrelated consecutive
    numbers (e.g. "109 2", "שלוש ארבע", "שלושים בית") are NEVER summed.
    Returns (value, next_index) or None if no valid number starts at i.
    """
    n_words = len(words)
    if i >= n_words:
        return None

    w0 = _clean_num_tok(words[i])
    if not w0:
        return None

    # 1. Standalone Arabic digit token — immediately terminates (never sum adjacent digits)
    if w0.isdigit():
        val = int(w0)
        return (val, i + 1) if 1 <= val <= 999 else None

    # 2. Spoken Hebrew letter-names ("למד אלף" = 31, mixed "למד א'" = 31, "קוף ט'" = 109)
    #    Strictly descending gematria values. Single-letter א/ב only merges when allow_spaced_gematria=True
    #    (so trailing shorthand amud like "למד ב" without "עמוד" stays separate).
    if w0 in _HEBREW_LETTER_NAMES:
        total = _HEBREW_LETTER_NAMES[w0]
        prev = total
        j = i + 1
        while j < n_words:
            wj = _clean_num_tok(words[j])
            val = _HEBREW_LETTER_NAMES.get(wj)
            if val is None:
                sv = _single_gematria_value(words[j])
                if sv is not None and (allow_spaced_gematria or sv > 2):
                    val = sv
            if val is not None and val < prev and total + val <= 999:
                total += val
                prev = val
                j += 1
            else:
                break
        return (total, j) if 1 <= total <= 999 else None

    # 3. Spaced single-letter gematria ("ק ט" = 109, "ק נ ז" = 157, mixed "ק' טית" = 109)
    #    Strictly descending, >=2 tokens.
    if allow_spaced_gematria:
        g0 = _single_gematria_value(words[i])
        if g0 is not None and i + 1 < n_words:
            g1 = _single_gematria_value(words[i + 1])
            if g1 is None:
                g1 = _HEBREW_LETTER_NAMES.get(_clean_num_tok(words[i + 1]))
            if g1 is not None and g1 < g0 and g0 + g1 <= 999:
                total = g0 + g1
                prev = g1
                j = i + 2
                while j < n_words:
                    gj = _single_gematria_value(words[j])
                    if gj is None:
                        gj = _HEBREW_LETTER_NAMES.get(_clean_num_tok(words[j]))
                    if gj is not None and gj < prev and total + gj <= 999:
                        total += gj
                        prev = gj
                        j += 1
                    else:
                        break
                return (total, j) if 1 <= total <= 999 else None

    # 4. Spoken Hebrew numbers — strict descending magnitude state machine:
    #    START -> (hundreds -> tens -> ones) | (hundreds -> teens) | (tens -> ones) | teens | ones
    j = i
    total = 0
    state = "start"  # "start" | "hundreds" | "tens" | "done"

    while j < n_words and state != "done":
        raw_j = j
        w = _clean_num_tok(words[j])
        if not w:
            break

        # Allow standalone connector "ו" only after a higher magnitude and only if followed by a valid continuation
        consumed_standalone_vav = False
        if w == "ו":
            if state in ("hundreds", "tens") and j + 1 < n_words:
                j += 1
                w = _clean_num_tok(words[j])
                consumed_standalone_vav = True
            else:
                break

        # Strip optional leading "ו" on tens/teens/ones after a higher magnitude
        candidates = [w]
        if state in ("hundreds", "tens"):
            stripped = _strip_optional_vav(w)
            if stripped != w:
                candidates.append(stripped)

        if state == "start":
            # Hundreds: "מאתיים" | "מאה" | "<ones> מאות"
            if w == "מאתיים":
                total = 200
                j += 1
                state = "hundreds"
                continue
            if w == "מאה":
                total = 100
                j += 1
                state = "hundreds"
                continue
            if j + 1 < n_words and _clean_num_tok(words[j + 1]) == "מאות":
                ones_lookup = _ORDINAL_TO_CARDINAL.get(w, w)
                ones_val = _SPOKEN_ONES.get(ones_lookup)
                if ones_val is not None and 1 <= ones_val <= 9:
                    total = ones_val * 100
                    j += 2
                    state = "hundreds"
                    continue

            # Compound teens ("תשע עשרה", "שניים עשר")
            if j + 1 < n_words:
                nxt = _clean_num_tok(words[j + 1])
                compound = f"{w}_{nxt}"
                if compound in _SPOKEN_TEENS:
                    total = _SPOKEN_TEENS[compound]
                    j += 2
                    state = "done"
                    continue

            # Single-word teen ("עשר", "עשרה", "עשירי", "עשירית")
            lookup = _ORDINAL_TO_CARDINAL.get(w, w)
            if lookup in _SPOKEN_TEENS:
                total = _SPOKEN_TEENS[lookup]
                j += 1
                state = "done"
                continue

            # Tens ("עשרים" .. "תשעים")
            if w in _SPOKEN_TENS:
                total = _SPOKEN_TENS[w]
                j += 1
                state = "tens"
                continue

            # Ones ("אחת" .. "תשעה", ordinals, "שניים")
            if lookup in _SPOKEN_ONES:
                total = _SPOKEN_ONES[lookup]
                j += 1
                state = "done"
                continue

            break

        elif state == "hundreds":
            matched = False
            # Compound teens after hundreds ("מאה ושלוש עשרה")
            if j + 1 < n_words:
                nxt = _clean_num_tok(words[j + 1])
                for cand in candidates:
                    compound = f"{cand}_{nxt}"
                    if compound in _SPOKEN_TEENS:
                        total += _SPOKEN_TEENS[compound]
                        j += 2
                        state = "done"
                        matched = True
                        break
            if matched:
                continue

            # Single-word teen ("מאה ועשר")
            for cand in candidates:
                lookup = _ORDINAL_TO_CARDINAL.get(cand, cand)
                if lookup in _SPOKEN_TEENS:
                    total += _SPOKEN_TEENS[lookup]
                    j += 1
                    state = "done"
                    matched = True
                    break
            if matched:
                continue

            # Tens ("מאה ועשרים", "שלוש מאות ושישים")
            for cand in candidates:
                if cand in _SPOKEN_TENS:
                    total += _SPOKEN_TENS[cand]
                    j += 1
                    state = "tens"
                    matched = True
                    break
            if matched:
                continue

            # Ones ("שלוש מאות ואחת", "מאה ו תשע")
            for cand in candidates:
                lookup = _ORDINAL_TO_CARDINAL.get(cand, cand)
                if lookup in _SPOKEN_ONES:
                    total += _SPOKEN_ONES[lookup]
                    j += 1
                    state = "done"
                    matched = True
                    break
            if matched:
                continue

            if consumed_standalone_vav:
                j = raw_j
            break

        elif state == "tens":
            matched = False
            # Only ones may follow tens (NEVER another tens, teens, hundreds, or letter-name like "בית")
            for cand in candidates:
                lookup = _ORDINAL_TO_CARDINAL.get(cand, cand)
                if lookup in _SPOKEN_ONES:
                    total += _SPOKEN_ONES[lookup]
                    j += 1
                    state = "done"
                    matched = True
                    break
            if matched:
                continue

            if consumed_standalone_vav:
                j = raw_j
            break

    if state != "start" and 1 <= total <= 999:
        return total, j

    return None


def int_to_gematria(n: int) -> str:
    """Convert an integer (1-999) to Hebrew gematria without gershayim."""
    if n < 1 or n > 999:
        raise ValueError(f"int_to_gematria: {n} out of range 1-999")

    # Special cases: טו=15, טז=16 (avoid spelling God's name)
    if n == 15:
        return "טו"
    if n == 16:
        return "טז"

    units = ["", "א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט"]
    tens = ["", "י", "כ", "ל", "מ", "נ", "ס", "ע", "פ", "צ"]
    hundreds = ["", "ק", "ר", "ש", "ת"]

    h = n // 100
    t = (n % 100) // 10
    u = n % 10

    parts = []
    if h > 0:
        if h <= 4:
            parts.append(hundreds[h])
        else:
            # 500+: ת + remainder
            parts.append("ת")
            rest = (h - 4) * 100
            while rest >= 400:
                parts.append("ת")
                rest -= 400
            if rest >= 100:
                parts.append(hundreds[rest // 100])

    # Special טו/טז in the units position
    if t == 1 and u == 5:
        parts.append("טו")
    elif t == 1 and u == 6:
        parts.append("טז")
    else:
        if t > 0:
            parts.append(tens[t])
        if u > 0:
            parts.append(units[u])

    return "".join(parts)
