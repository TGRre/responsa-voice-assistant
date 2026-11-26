"""Tests for Hebrew text utilities: gematria, phonetics, prefix stripping, structural fixes."""

from __future__ import annotations

import pytest

from src.hebrew_utils import (
    clean_spoken_command,
    fix_structural_errors,
    int_to_gematria,
    normalize_key,
    normalize_phonetic,
    parse_hebrew_number,
    parse_spoken_number,
    strip_prefix_variants,
)
from src.models import Action


# ---------------------------------------------------------------------------
# parse_hebrew_number
# ---------------------------------------------------------------------------


class TestParseHebrewNumber:
    def test_gematria_basic(self):
        assert parse_hebrew_number("א") == 1
        assert parse_hebrew_number("ט") == 9
        assert parse_hebrew_number("י") == 10
        assert parse_hebrew_number("ק") == 100
        assert parse_hebrew_number("ת") == 400

    def test_gematria_compound(self):
        assert parse_hebrew_number("קט") == 109  # 100 + 9
        assert parse_hebrew_number("קנז") == 157  # 100 + 50 + 7
        assert parse_hebrew_number("שא") == 301  # 300 + 1

    def test_gematria_tev_vav_tev_zayin(self):
        assert parse_hebrew_number("טו") == 15
        assert parse_hebrew_number("טז") == 16

    def test_gematria_with_gershayim_and_trailing_punct(self):
        assert parse_hebrew_number('קנ"ז') == 157
        assert parse_hebrew_number('ש"א') == 301
        assert parse_hebrew_number("ב.") == 2
        assert parse_hebrew_number("א!") == 1
        assert parse_hebrew_number("קט?") == 109
        assert parse_hebrew_number("ב׳") == 2

    def test_arabic_numerals(self):
        assert parse_hebrew_number("697") == 697
        assert parse_hebrew_number("15") == 15
        assert parse_hebrew_number("1") == 1

    def test_final_letters(self):
        assert parse_hebrew_number("ך") == 20
        assert parse_hebrew_number("ם") == 40
        assert parse_hebrew_number("ן") == 50

    def test_invalid(self):
        assert parse_hebrew_number("abc") is None
        assert parse_hebrew_number("") is None
        assert parse_hebrew_number("0") is None

    def test_out_of_range(self):
        assert parse_hebrew_number("1000") is None


# ---------------------------------------------------------------------------
# int_to_gematria
# ---------------------------------------------------------------------------


class TestIntToGematria:
    def test_basic(self):
        assert int_to_gematria(1) == "א"
        assert int_to_gematria(9) == "ט"
        assert int_to_gematria(10) == "י"

    def test_special_15_16(self):
        assert int_to_gematria(15) == "טו"
        assert int_to_gematria(16) == "טז"

    def test_compound(self):
        assert int_to_gematria(157) == "קנז"
        assert int_to_gematria(301) == "שא"
        assert int_to_gematria(109) == "קט"

    def test_round_trip(self):
        for n in range(2, 1000):
            gem = int_to_gematria(n)
            parsed = parse_hebrew_number(gem)
            assert parsed == n, f"Round trip failed for {n}: {gem} -> {parsed}"

    def test_out_of_range(self):
        with pytest.raises(ValueError):
            int_to_gematria(0)
        with pytest.raises(ValueError):
            int_to_gematria(1000)


# ---------------------------------------------------------------------------
# parse_spoken_number (strict descending-magnitude state machine)
# ---------------------------------------------------------------------------


class TestParseSpokenNumber:
    def test_single_word_teen(self):
        assert parse_spoken_number(["תשע", "עשרה"], 0) == (19, 2)

    def test_hundreds(self):
        assert parse_spoken_number(["מאה", "ו", "תשע"], 0) == (109, 3)
        assert parse_spoken_number(["מאה", "ועשרים"], 0) == (120, 2)
        assert parse_spoken_number(["שלוש", "מאות", "ושישים"], 0) == (360, 3)

    def test_three_hundred_one(self):
        assert parse_spoken_number(["שלוש", "מאות", "ו", "אחת"], 0) == (301, 4)
        assert parse_spoken_number(["שלוש", "מאות", "ואחת"], 0) == (301, 3)

    def test_999(self):
        assert parse_spoken_number(["תשע", "מאות", "תשעים", "ו", "תשע"], 0) == (999, 5)

    def test_shnayim_and_letter_names(self):
        assert parse_spoken_number(["שניים"], 0) == (2, 1)
        assert parse_spoken_number(["למד", "אלף"], 0) == (31, 2)
        assert parse_spoken_number(["קוף", "טית"], 0) == (109, 2)
        assert parse_spoken_number(["הא"], 0) == (5, 1)
        assert parse_spoken_number(["וו"], 0) == (6, 1)
        assert parse_spoken_number(["חת"], 0) == (8, 1)
        assert parse_spoken_number(["טת"], 0) == (9, 1)
        assert parse_spoken_number(["פא"], 0) == (80, 1)
        assert parse_spoken_number(["תו"], 0) == (400, 1)

    def test_spaced_single_letter_gematria(self):
        assert parse_spoken_number(["ק", "ט", "עמוד", "ב"], 0) == (109, 2)
        assert parse_spoken_number(["ק", "נ", "ז"], 0) == (157, 3)
        # Mixed letter-name + single Hebrew letter ("למד א'", "ק' טית", "קוף ט'")
        assert parse_spoken_number(["למד", "א'", "עמוד", "ב"], 0) == (31, 2)
        assert parse_spoken_number(["ק'", "טית", "עמוד", "ב"], 0) == (109, 2)
        assert parse_spoken_number(["קוף", "ט'"], 0, allow_spaced_gematria=False) == (109, 2)
        # Trailing amud letter after letter-name stays separate when allow_spaced_gematria=False
        assert parse_spoken_number(["למד", "ב"], 0, allow_spaced_gematria=False) == (30, 1)
        # Non-descending letters must not merge
        assert parse_spoken_number(["ג", "ד"], 0) is None

    def test_never_sums_unrelated_consecutive_numbers(self):
        # Arabic digits never sum
        assert parse_spoken_number(["109", "2"], 0) == (109, 1)
        assert parse_spoken_number(["109", "2"], 1) == (2, 2)
        # Ones + ones never sum
        assert parse_spoken_number(["שלוש", "ארבע"], 0) == (3, 1)
        assert parse_spoken_number(["שלוש", "ארבע"], 1) == (4, 2)
        # Spoken tens + letter-name ("שלושים בית") never sum
        assert parse_spoken_number(["שלושים", "בית"], 0) == (30, 1)
        assert parse_spoken_number(["שלושים", "בית"], 1) == (2, 2)
        # Tens + tens never sum
        assert parse_spoken_number(["עשרים", "שלושים"], 0) == (20, 1)

    def test_no_unsafe_short_gematria_fallback(self):
        # Short 2-letter Hebrew words must NOT be parsed by parse_spoken_number
        assert parse_spoken_number(["בא"], 0) is None
        assert parse_spoken_number(["נח"], 0) is None
        assert parse_spoken_number(["קט", "עמוד", "א"], 0) is None

    def test_arabic_passthrough(self):
        assert parse_spoken_number(["157", "עמוד", "א"], 0) == (157, 1)

    def test_no_number(self):
        assert parse_spoken_number(["שבת", "דף"], 0) is None


# ---------------------------------------------------------------------------
# normalize_key
# ---------------------------------------------------------------------------


class TestNormalizeKey:
    def test_strips_punctuation(self):
        assert normalize_key('רש"י') == "רשי"
        assert normalize_key('ע"ב') == "עב"
        assert normalize_key("א-ב") == "אב"

    def test_strips_niqqud(self):
        assert normalize_key("בְּרֵאשִׁית") == "בראשית"

    def test_preserves_hebrew(self):
        assert normalize_key("ברכות") == "ברכות"


# ---------------------------------------------------------------------------
# normalize_phonetic
# ---------------------------------------------------------------------------


class TestNormalizePhonetic:
    def test_finals_folded(self):
        assert normalize_phonetic("ם") == "מ"
        assert normalize_phonetic("ן") == "נ"
        assert normalize_phonetic("ץ") == "צ"
        assert normalize_phonetic("ף") == "פ"
        assert normalize_phonetic("ך") == "כ"

    def test_tet_folds_to_tav(self):
        assert normalize_phonetic("ט") == "ת"

    def test_aleph_ayin_merged(self):
        assert normalize_phonetic("א") == normalize_phonetic("ע")

    def test_kaf_kuf_merged(self):
        assert normalize_phonetic("כ") == normalize_phonetic("ק")

    def test_never_merge_chet_kuf(self):
        assert normalize_phonetic("ח") != normalize_phonetic("ק")
        assert normalize_phonetic("ח") != normalize_phonetic("כ")

    def test_never_merge_bet_vav(self):
        assert normalize_phonetic("ב") != normalize_phonetic("ו")

    def test_safety_noach_vs_ba(self):
        assert normalize_phonetic("נח") != normalize_phonetic("בא")

    def test_safety_noach_vs_tzav(self):
        assert normalize_phonetic("נח") != normalize_phonetic("צו")


# ---------------------------------------------------------------------------
# strip_prefix_variants
# ---------------------------------------------------------------------------


class TestStripPrefixVariants:
    def test_strips_he(self):
        assert "רשבא" in strip_prefix_variants("הרשבא")

    def test_strips_bet(self):
        assert "חולין" in strip_prefix_variants("בחולין")

    def test_strips_lamed(self):
        assert 'רמב"ם' in strip_prefix_variants('לרמב"ם')

    def test_original_first(self):
        variants = strip_prefix_variants("בחולין")
        assert variants[0] == "בחולין"

    def test_short_token_unchanged(self):
        assert strip_prefix_variants("אב") == ["אב"]

    def test_real_words_not_corrupted(self):
        for word in ["ברכות", "מגילה", "הלכות"]:
            variants = strip_prefix_variants(word)
            assert variants[0] == word


# ---------------------------------------------------------------------------
# fix_structural_errors
# ---------------------------------------------------------------------------


class TestFixStructuralErrors:
    def test_daf_markers(self):
        assert "דף" in fix_structural_errors("ת' קט")
        assert "דף" in fix_structural_errors("ת׳ קט")
        assert "דף" in fix_structural_errors("ד' קט")
        assert "דף" in fix_structural_errors("דפ קט")

    def test_amud_markers_and_trailing_punct(self):
        assert "עמוד א" in fix_structural_errors('דף קט ע"א')
        assert "עמוד א" in fix_structural_errors("דף קט ע״א.")
        assert "עמוד א" in fix_structural_errors("דף קט עא")
        assert "עמוד א" in fix_structural_errors("דף קט עמוד אלף")
        assert "עמוד א" in fix_structural_errors("דף קט עמוד א'")
        assert "עמוד ב" in fix_structural_errors('דף קט ע"ב.')
        assert "עמוד ב" in fix_structural_errors("דף קט עמוד ב׳!")
        assert fix_structural_errors("חולין דף קט עמוד ב.") == "חולין דף קט עמוד ב"
        # Bare עא/עב preceded by number-expecting structural markers are gematria 71/72!
        assert fix_structural_errors("שבת דף עב עמוד א") == "שבת דף עב עמוד א"
        assert fix_structural_errors("תהילים פרק עא") == "תהילים פרק עא"
        assert fix_structural_errors("שולחן ערוך אורח חיים סימן עב") == "שולחן ערוך אורח חיים סימן עב"
        assert fix_structural_errors("ד' עב עמוד א") == "דף עב עמוד א"
        assert fix_structural_errors("חולין דף עא עב") == "חולין דף עא עמוד ב"

    def test_perek_markers(self):
        assert "פרק א" in fix_structural_errors("פרק ראשון")
        assert "פרק א" in fix_structural_errors("פרק אלף")
        assert "פרק ב" in fix_structural_errors("פרק שני")

    def test_pasuk_markers(self):
        assert "פסוק" in fix_structural_errors("פס' א")
        assert "פסוק" in fix_structural_errors("פס׳ ב")

    def test_siman_markers(self):
        assert "סימן" in fix_structural_errors("סי' י")
        assert "סימן" in fix_structural_errors("סי׳ י")
        assert "סימן" in fix_structural_errors("סימ' י")

    def test_seif_vs_seif_katan(self):
        assert fix_structural_errors("סע' ד") == "סעיף ד"
        assert fix_structural_errors("סע׳ ד") == "סעיף ד"
        assert fix_structural_errors('ס"ק ד') == "סעיף_קטן ד"
        assert fix_structural_errors("ס״ק ד") == "סעיף_קטן ד"
        assert fix_structural_errors("סק ד") == "סעיף_קטן ד"
        assert fix_structural_errors("סעיף קטן ד") == "סעיף_קטן ד"

    def test_parasha_markers(self):
        assert "פרשת" in fix_structural_errors("פרשה בראשית")
        assert "פרשת" in fix_structural_errors("פרשת השבוע בראשית")
        assert fix_structural_errors("בפרשת בא") == "פרשת בא"
        assert fix_structural_errors("לפרשת נח") == "פרשת נח"

    def test_split_books(self):
        assert "שמואל א" in fix_structural_errors("שמואל אלף")
        assert "שמואל א" in fix_structural_errors("שמואל ראשון")
        assert "שמואל א" in fix_structural_errors("שמואל 1")
        assert "מלכים ב" in fix_structural_errors("מלכים בית")
        assert "מלכים ב" in fix_structural_errors("מלכים שני")
        assert "דברי הימים א" in fix_structural_errors("דברי הימים ראשון")
        assert "דברי הימים ב" in fix_structural_errors("דברי הימים שני")


# ---------------------------------------------------------------------------
# clean_spoken_command
# ---------------------------------------------------------------------------


class TestCleanSpokenCommand:
    def test_open_action_variants(self):
        for text in [
            "פתח לי חולין דף קט",
            "תפתי לי בבקשה חולין",
            'תפתח לי רש"י על בבא קמא',
            "תפתחי חולין דף קט",
            "יפתח, אולין, דף קט, עמוד ב.",
            "אפתח חולין דף קט",
            "בבקשה פתח חולין דף קט",
            "אני רוצה לפתוח חולין דף קט",
            "תביא לי מכות דף ג",
        ]:
            result = clean_spoken_command(text)
            assert result.action == Action.OPEN, f"Failed for {text!r}: {result}"

    def test_navigate_action(self):
        assert clean_spoken_command("קפוץ למסכת יבמות דף כ").action == Action.NAVIGATE
        assert clean_spoken_command("עבור אל חולין דף קט").action == Action.NAVIGATE
        assert clean_spoken_command("תעבור אל ברכות דף ב").action == Action.NAVIGATE
        assert clean_spoken_command("עבור לברכות דף ב").action == Action.NAVIGATE

    def test_search_action(self):
        for text, expected in [
            ("חפש דינא דמלכותא", "דינא דמלכותא"),
            ("מצא דינא דמלכותא", "דינא דמלכותא"),
            ("תמצא לי דינא דמלכותא", "דינא דמלכותא"),
            ("נמצא מלמד", "מלמד"),
            ("למצוא את המילה מלמד", "מלמד"),
            ("מחפש על מנת", "על מנת"),
        ]:
            result = clean_spoken_command(text)
            assert result.action == Action.SEARCH, f"Failed for {text!r}"
            assert result.text == expected, f"Failed for {text!r}: {result.text!r}"

    def test_anchored_does_not_match_mid_utterance_entity(self):
        result = clean_spoken_command("פתחי תשובה יורה דעה סימן א")
        assert result.action == Action.OPEN
        assert "פתחי תשובה" in result.text

    def test_strips_fillers(self):
        result = clean_spoken_command("פתח בבקשה את חולין")
        assert "בבקשה" not in result.text
        assert "את" not in result.text
        assert "חולין" in result.text

    def test_strips_connector_al(self):
        result = clean_spoken_command('רש"י על חולין')
        assert "על" not in result.text
        assert "רש" in result.text
        assert "חולין" in result.text
