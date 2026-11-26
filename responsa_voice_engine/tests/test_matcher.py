"""Comprehensive matcher, routing, slot-filling, validation, and formatting tests."""

from __future__ import annotations

import pytest

from src.matcher import CommandMatcher
from src.models import Action, MatchStatus, SchemaType
from src.schema_loader import load_schema_index

_FORBIDDEN_QUOTES = ('"', "״", "'", "׳", "\u2019")


def _assert_clean_payload(payload: str) -> None:
    for q in _FORBIDDEN_QUOTES:
        assert q not in payload, f"Forbidden quote char {q!r} found in payload: {payload!r}"


@pytest.fixture(scope="module")
def matcher():
    index = load_schema_index()
    return CommandMatcher(index)


# ---------------------------------------------------------------------------
# Non-Negotiable Verified Working Payloads
# ---------------------------------------------------------------------------


class TestVerifiedWorkingPayloads:
    def test_chullin_daf_kat_amud_bet(self, matcher):
        r = matcher.match("פתח חולין דף קט עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.canonical_command == "חולין דף קט עמוד ב"
        _assert_clean_payload(r.canonical_command)

    def test_bava_kamma_lamed_amud_bet(self, matcher):
        r = matcher.match('תפתח לי בבקשה בבא קמא ל ע"ב')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.canonical_command == "בבא קמא דף ל עמוד ב"
        _assert_clean_payload(r.canonical_command)

    def test_yevamot_lamed_alef_amud_bet(self, matcher):
        r = matcher.match("יבמות דף לא עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.canonical_command == "יבמות דף לא עמוד ב"
        _assert_clean_payload(r.canonical_command)

    def test_rambam_shabbat_1_1(self, matcher):
        r = matcher.match('רמב"ם שבת פרק א הלכה א')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RAMBAM_HALACHA
        assert r.canonical_command == "רמבם הלכות שבת פרק א הלכה א"
        _assert_clean_payload(r.canonical_command)

    def test_parashat_bo(self, matcher):
        r = matcher.match("פתח פרשת בא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.PARASHA
        assert r.canonical_command == "פרשת בא"
        _assert_clean_payload(r.canonical_command)

    def test_shulchan_aruch_oc_301(self, matcher):
        r = matcher.match("שולחן ערוך אורח חיים סימן שא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן שא"
        _assert_clean_payload(r.canonical_command)

    def test_megillah_perek_bet_preserved(self, matcher):
        r = matcher.match("פתח מגילה פרק ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields.get("book") == "esther"
        assert r.canonical_command == "מגילה פרק ב"
        _assert_clean_payload(r.canonical_command)


# ---------------------------------------------------------------------------
# Action Verb Crash Regression & Whisper Punctuation
# ---------------------------------------------------------------------------


class TestActionAndPunctuationRegressions:
    def test_whisper_yiftach_olin_with_commas_and_period(self, matcher):
        r = matcher.match("יפתח, אולין, דף קט, עמוד ב.")
        assert r.status == MatchStatus.OK
        assert r.action == Action.OPEN
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields["tractate"] == "chullin"
        assert r.parsed_fields["daf"] == 109
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "חולין דף קט עמוד ב"

    def test_trailing_period_on_amud(self, matcher):
        r = matcher.match("חולין דף קט עמוד ב.")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "חולין דף קט עמוד ב"

    def test_tavi_li_maps_to_open(self, matcher):
        r = matcher.match("תביא לי חולין דף קט עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.action == Action.OPEN
        assert r.canonical_command == "חולין דף קט עמוד ב"


# ---------------------------------------------------------------------------
# Deterministic Routing Disambiguation
# ---------------------------------------------------------------------------


class TestRoutingDisambiguation:
    def test_rashi_chullin_routes_to_bavli(self, matcher):
        r = matcher.match("פתח רשי חולין דף קט עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.canonical_command == "רשי חולין דף קט עמוד ב"

    def test_rosh_berakhot_perek_siman_not_hijacked_by_rambam(self, matcher):
        r = matcher.match("פתח ראש ברכות פרק א סימן ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.ROSH_PEREK_SIMAN
        assert r.parsed_fields["tractate"] == "berakhot"
        assert r.parsed_fields["perek"] == 1
        assert r.parsed_fields["siman"] == 2
        assert r.canonical_command == "ראש ברכות פרק א סימן ב"

    def test_rosh_perek_only_does_not_copy_perek_to_siman(self, matcher):
        r = matcher.match("פתח ראש ברכות פרק ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.ROSH_PEREK_SIMAN
        assert r.parsed_fields["perek"] == 2
        assert "siman" not in r.parsed_fields
        assert r.canonical_command == "ראש ברכות פרק ב"

    def test_rashi_bereshit_routes_to_tanakh(self, matcher):
        r = matcher.match("פתח רשי בראשית פרק א פסוק א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields["book"] == "genesis"
        assert r.parsed_fields["commentary"] == "rashi"
        assert r.canonical_command == "רשי בראשית פרק א פסוק א"

    def test_prefixed_ramban_preserves_tanakh_domain(self, matcher):
        r = matcher.match("הרמבן בראשית פרק א פסוק א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields["commentary"] == "ramban"
        assert r.canonical_command == "רמבן בראשית פרק א פסוק א"

    def test_shared_commentary_alone_never_routes(self, matcher):
        r = matcher.match("פתח רשי")
        assert r.status == MatchStatus.NEEDS_FALLBACK
        assert r.schema_type is None
        assert r.canonical_command == ""


# ---------------------------------------------------------------------------
# BAVLI_DAF cases
# ---------------------------------------------------------------------------


class TestBavliDaf:
    def test_case_01_basic(self, matcher):
        r = matcher.match("פתח חולין דף קט עמוד א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "chullin"
        assert r.parsed_fields.get("daf") == 109
        assert r.parsed_fields.get("amud") == "א"

    def test_case_02_whisper_confusion(self, matcher):
        r = matcher.match("פתח פולין ת' קט עמוד א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "chullin"
        assert r.parsed_fields.get("daf") == 109

    def test_case_03_shorthand(self, matcher):
        r = matcher.match("תפתי לי בבקשה חולין קט א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "chullin"
        assert r.parsed_fields.get("daf") == 109
        assert r.parsed_fields.get("amud") == "א"

    def test_case_04_rashi_with_al(self, matcher):
        r = matcher.match('תפתח לי רש"י על בבא קמא ל ע"ב')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "bava_kamma"
        assert r.parsed_fields.get("daf") == 30
        assert r.parsed_fields.get("amud") == "ב"
        assert r.canonical_command == "רשי בבא קמא דף ל עמוד ב"

    def test_case_05_fuzzy_rashi(self, matcher):
        r = matcher.match("תפתי לי רשי על בבא קמה לא עב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "bava_kamma"
        assert r.parsed_fields.get("daf") in (30, 31)

    def test_case_06_rashi_shorthand(self, matcher):
        r = matcher.match('רש"י בבא קמא ל ב')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "bava_kamma"
        assert r.parsed_fields.get("daf") == 30
        assert r.parsed_fields.get("amud") == "ב"

    def test_case_08_rashi_chullin(self, matcher):
        r = matcher.match('רש"י חולין קט א')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "chullin"

    def test_case_09_shabbat_daf(self, matcher):
        r = matcher.match("שבת דף ב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "shabbat"
        assert r.parsed_fields.get("daf") == 2

    def test_case_25_shabbat_invalid_range(self, matcher):
        r = matcher.match("פתח שבת דף קנח עמוד א")
        assert r.status == MatchStatus.INVALID_RANGE
        assert r.canonical_command == ""
        assert "עד" in r.tts_feedback

    def test_invalid_amud_gimel(self, matcher):
        r = matcher.match("פתח חולין דף קט עמוד ג")
        assert r.status == MatchStatus.INVALID_RANGE
        assert r.canonical_command == ""
        assert "א" in r.tts_feedback and "ב" in r.tts_feedback

    def test_rashi_shefat_not_parsed_as_gematria_780(self, matcher):
        # Immediately after a commentary (non-target-work), 3-letter words like "שפת"
        # must NEVER be consumed as shorthand gematria (780)
        tokens = matcher._classify_tokens(["רשי", "שפת"])
        assert not any(t["type"] == "number" for t in tokens)
        r = matcher.match("רשי שפת")
        assert r.status == MatchStatus.NEEDS_FALLBACK
        assert r.parsed_fields.get("daf") != 780

        # And a fuzzy tractate after Rashi resolves cleanly
        r2 = matcher.match("רשי אולין דף קט עמוד ב")
        assert r2.status == MatchStatus.OK
        assert r2.parsed_fields["tractate"] == "chullin"
        assert r2.canonical_command == "רשי חולין דף קט עמוד ב"

    def test_case_30_tosafot(self, matcher):
        r = matcher.match('תוספות בבא מציעא כב ע"ב')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "bava_metzia"
        assert r.parsed_fields.get("daf") == 22
        assert r.canonical_command == "תוספות בבא מציעא דף כב עמוד ב"

    def test_case_31_navigate(self, matcher):
        r = matcher.match("קפוץ למסכת יבמות דף כ")
        assert r.status == MatchStatus.OK
        assert r.action == Action.NAVIGATE
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields.get("tractate") == "yevamot"
        assert r.parsed_fields.get("daf") == 20


# ---------------------------------------------------------------------------
# Spoken Number State Machine End-to-End
# ---------------------------------------------------------------------------


class TestSpokenNumberEndToEnd:
    def test_arabic_digits_not_summed(self, matcher):
        r = matcher.match("חולין 109 2")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["daf"] == 109
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "חולין דף קט עמוד ב"

    def test_rambam_consecutive_ones_not_summed(self, matcher):
        r = matcher.match("רמבם שבת שלוש ארבע")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RAMBAM_HALACHA
        assert r.parsed_fields["perek"] == 3
        assert r.parsed_fields["halacha"] == 4
        assert r.canonical_command == "רמבם הלכות שבת פרק ג הלכה ד"

    def test_bava_kamma_shloshim_beit_not_summed(self, matcher):
        r = matcher.match("בבא קמא שלושים בית")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["daf"] == 30
        assert r.parsed_fields["amud"] == "ב"

    def test_siman_meah_veesrim(self, matcher):
        r = matcher.match("שולחן ערוך אורח חיים סימן מאה ועשרים")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["siman"] == 120
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן קכ"

    def test_yevamot_lamed_alef_amud_bet(self, matcher):
        r = matcher.match("יבמות דף למד אלף עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["daf"] == 31
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "יבמות דף לא עמוד ב"

    def test_rashba_yevamot_mixed_lamed_alef_geresh(self, matcher):
        r = matcher.match("פתח רשב״א יבמות, למד א', עמוד ב'.")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields["commentary"] == "rashba"
        assert r.parsed_fields["tractate"] == "yevamot"
        assert r.parsed_fields["daf"] == 31
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "רשבא יבמות דף לא עמוד ב"

    def test_chullin_shnayim_with_period(self, matcher):
        r = matcher.match("חולין דף שניים עמוד א.")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["daf"] == 2
        assert r.parsed_fields["amud"] == "א"

    def test_chullin_spaced_gematria_kuf_tet(self, matcher):
        r = matcher.match("חולין דף ק ט עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["daf"] == 109
        assert r.parsed_fields["amud"] == "ב"
        assert r.canonical_command == "חולין דף קט עמוד ב"


# ---------------------------------------------------------------------------
# Multi-Word Fuzzy Matching
# ---------------------------------------------------------------------------


class TestMultiWordFuzzy:
    def test_bava_kama_fuzzy(self, matcher):
        r = matcher.match("פתח בבא קמה דף ל עמוד ב")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["tractate"] == "bava_kamma"
        assert r.canonical_command == "בבא קמא דף ל עמוד ב"

    def test_pnei_yehoshua_noisy(self, matcher):
        r = matcher.match("פתח פני יהושוע ברכות דף יב עמוד א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.BAVLI_DAF
        assert r.parsed_fields["tractate"] == "berakhot"
        assert r.parsed_fields["commentary"] == "pnei_yehoshua"
        assert r.canonical_command == "פני יהושע ברכות דף יב עמוד א"

    def test_mishnah_barur_fuzzy_not_rambam(self, matcher):
        r = matcher.match("פתח משנה ברור סימן שכח סק ד")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["nosei_keli"] == "mishnah_berurah"
        assert r.parsed_fields["siman"] == 328
        assert r.parsed_fields["seif_katan"] == 4
        assert r.canonical_command == "משנה ברורה אורח חיים סימן שכח סק ד"

    def test_shulchan_aroch_orach_chim_fuzzy(self, matcher):
        r = matcher.match("פתח שולחן ארוך אורח חים סימן שא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן שא"

    def test_ketzot_hachoshen_fuzzy(self, matcher):
        r = matcher.match("פתח קצוות החושן סימן י")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["nosei_keli"] == "ketzot_hachoshen"
        assert r.parsed_fields["section"] == "choshen_mishpat"


# ---------------------------------------------------------------------------
# TANAKH_PEREK_PASUK cases
# ---------------------------------------------------------------------------


class TestTanakhPerekPasuk:
    def test_case_07_rashi_genesis(self, matcher):
        r = matcher.match('רש"י בראשית א א')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields.get("book") == "genesis"
        assert r.parsed_fields.get("perek") == 1
        assert r.canonical_command == "רשי בראשית פרק א פסוק א"

    def test_case_26_tehillim_invalid_range(self, matcher):
        r = matcher.match("תהילים פרק קנא")
        assert r.status == MatchStatus.INVALID_RANGE
        assert r.canonical_command == ""

    def test_case_32_ramban_shemot(self, matcher):
        r = matcher.match('רמב"ן שמות פרק כא פסוק ג')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields.get("book") == "exodus"
        assert r.parsed_fields.get("perek") == 21
        assert r.canonical_command == "רמבן שמות פרק כא פסוק ג"

    def test_case_33_pinned_unambiguous(self, matcher):
        r = matcher.match("מיכה פרק ג")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.TANAKH_PEREK_PASUK
        assert r.parsed_fields.get("book") == "micah"


# ---------------------------------------------------------------------------
# RAMBAM_HALACHA cases
# ---------------------------------------------------------------------------


class TestRambamHalacha:
    def test_case_10_explicit(self, matcher):
        r = matcher.match('רמב"ם שבת פרק א הלכה א')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RAMBAM_HALACHA
        assert r.parsed_fields.get("hilchah") == "sabbath"
        assert r.parsed_fields.get("perek") == 1
        assert r.parsed_fields.get("halacha") == 1
        assert r.canonical_command == "רמבם הלכות שבת פרק א הלכה א"

    def test_case_11_shorthand(self, matcher):
        r = matcher.match('רמב"ם שבת ג ד')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RAMBAM_HALACHA
        assert r.parsed_fields.get("hilchah") == "sabbath"
        assert r.parsed_fields.get("perek") == 3
        assert r.parsed_fields.get("halacha") == 4

    def test_perek_only_valid_no_zero(self, matcher):
        r = matcher.match("רמבם הלכות שבת פרק א")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RAMBAM_HALACHA
        assert r.canonical_command == "רמבם הלכות שבת פרק א"
        assert "0" not in r.canonical_command

    def test_missing_perek_number_before_halacha_not_duplicated(self, matcher):
        r = matcher.match("רמבם שבת פרק הלכה ג")
        assert r.status == MatchStatus.NEEDS_FALLBACK
        assert r.canonical_command == ""


# ---------------------------------------------------------------------------
# PARASHA cases
# ---------------------------------------------------------------------------


class TestParasha:
    def test_case_12_bo(self, matcher):
        r = matcher.match("פתח פרשת בא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.PARASHA
        assert r.parsed_fields.get("parasha") == "bo"
        assert r.canonical_command == "פרשת בא"

    def test_case_13_whisper_confusion(self, matcher):
        r = matcher.match("פתח פרשת בוא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.PARASHA
        assert r.parsed_fields.get("parasha") == "bo"
        assert r.canonical_command == "פרשת בא"

    def test_case_14_noach_invalid_range(self, matcher):
        r = matcher.match("פרשת נח פרק כ")
        assert r.status == MatchStatus.INVALID_RANGE
        assert r.canonical_command == ""

    def test_case_15_ki_tetze(self, matcher):
        r = matcher.match("פרשת כי תיסא פרק כה")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.PARASHA
        assert r.parsed_fields.get("parasha") == "ki_teitzei"

    def test_parasha_with_rashi(self, matcher):
        r = matcher.match("פתח רשי בפרשת בא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.PARASHA
        assert r.canonical_command == "רשי פרשת בא"

    def test_parasha_invalid_commentary(self, matcher):
        r = matcher.match("תרגום יונתן פרשת בא")
        assert r.status == MatchStatus.INVALID_COMBINATION
        assert r.canonical_command == ""


# ---------------------------------------------------------------------------
# ROSH_PEREK_SIMAN & RIF_DAF cases
# ---------------------------------------------------------------------------


class TestRoshAndRif:
    def test_case_16_rosh(self, matcher):
        r = matcher.match('רא"ש בבא קמא פרק ג סימן ט')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.ROSH_PEREK_SIMAN
        assert r.parsed_fields.get("tractate") == "bava_kamma"
        assert r.parsed_fields.get("perek") == 3
        assert r.parsed_fields.get("siman") == 9
        assert r.canonical_command == "ראש בבא קמא פרק ג סימן ט"

    def test_case_17_rif(self, matcher):
        r = matcher.match('רי"ף שבת כ ב')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.RIF_DAF
        assert r.parsed_fields.get("tractate") == "shabbat"
        assert r.parsed_fields.get("daf") == 20
        assert r.parsed_fields.get("amud") == "ב"

    def test_case_18_beitzah(self, matcher):
        r = matcher.match('רי"ף ביצה י')
        assert r.status in (MatchStatus.OK, MatchStatus.INVALID_RANGE)
        assert r.schema_type == SchemaType.RIF_DAF


# ---------------------------------------------------------------------------
# HALACHA_SIMAN cases (including seif vs seif_katan & defaults)
# ---------------------------------------------------------------------------


class TestHalachaSiman:
    def test_shulchan_aruch_seif_not_sk(self, matcher):
        r = matcher.match("פתח שולחן ערוך אורח חיים סימן שא סעיף ג")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["seif"] == 3
        assert "seif_katan" not in r.parsed_fields
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן שא סעיף ג"

    def test_mishnah_berurah_seif_katan(self, matcher):
        r = matcher.match("פתח משנה ברורה סימן שכח סעיף קטן ד")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["siman"] == 328
        assert r.parsed_fields["seif_katan"] == 4
        assert r.canonical_command == "משנה ברורה אורח חיים סימן שכח סק ד"

    def test_mishnah_berurah_al_shulchan_aruch_keeps_mb(self, matcher):
        r = matcher.match("פתח משנה ברורה על שולחן ערוך סימן שכח סק ד")
        assert r.status == MatchStatus.OK
        assert r.canonical_command == "משנה ברורה אורח חיים סימן שכח סק ד"

    def test_section_without_work_defaults_to_shulchan_aruch(self, matcher):
        r = matcher.match("פתח אורח חיים סימן שא")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן שא"

    def test_seif_and_seif_katan_together(self, matcher):
        r = matcher.match("משנה ברורה סימן שכח סעיף ב סעיף קטן ד")
        assert r.status == MatchStatus.OK
        assert r.parsed_fields["seif"] == 2
        assert r.parsed_fields["seif_katan"] == 4
        assert r.canonical_command == "משנה ברורה אורח חיים סימן שכח סעיף ב סק ד"

    def test_case_20_shorthand(self, matcher):
        r = matcher.match("משנה ברורה שכח ד")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields.get("siman") == 328
        assert r.parsed_fields.get("seif_katan") == 4

    def test_case_21_ambiguous_section_exact_two_candidates(self, matcher):
        r = matcher.match("פתח שך סימן י")
        assert r.status == MatchStatus.AMBIGUOUS_SECTION
        assert r.canonical_command == ""
        labels = [c.label for c in r.top_candidates]
        assert labels == ["יורה דעה", "חושן משפט"]

    def test_case_22_explicit_section(self, matcher):
        r = matcher.match('ש"ך יו"ד סימן י')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields.get("siman") == 10

    def test_case_23_invalid_combination(self, matcher):
        r = matcher.match("קצות החושן אורח חיים סימן י")
        assert r.status == MatchStatus.INVALID_COMBINATION
        assert r.canonical_command == ""

    def test_case_27_spoken_number(self, matcher):
        r = matcher.match("שולחן ערוך אורח חיים סימן שלוש מאות ואחת")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields.get("siman") == 301

    def test_case_28_gershayim_shorthand(self, matcher):
        r = matcher.match('שו"ע או"ח ש"א')
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields.get("siman") == 301
        assert r.canonical_command == "שולחן ערוך אורח חיים סימן שא"

    def test_case_29_single_section_inference_honored(self, matcher):
        r = matcher.match("מגן אברהם סימן נ")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields.get("siman") == 50
        assert r.parsed_fields.get("section_inferred") is True
        assert r.confidence <= 75.0
        # Unambiguous OK results must not carry spurious unrelated section candidates
        assert r.top_candidates == []

    def test_shorthand_gematria_not_hijacked_by_2_letter_acronyms(self, matcher):
        cases = [
            ("שבת דף עב עמוד א", SchemaType.BAVLI_DAF, "שבת דף עב עמוד א"),
            ("תהילים פרק עא", SchemaType.TANAKH_PEREK_PASUK, "תהילים פרק עא"),
            ("שולחן ערוך אורח חיים סימן עב", SchemaType.HALACHA_SIMAN, "שולחן ערוך אורח חיים סימן עב"),
            ("פתח חולין טז א", SchemaType.BAVLI_DAF, "חולין דף טז עמוד א"),
            ("פתח חולין כח א", SchemaType.BAVLI_DAF, "חולין דף כח עמוד א"),
            ("פתח חולין מב א", SchemaType.BAVLI_DAF, "חולין דף מב עמוד א"),
            ("פתח שבת עז א", SchemaType.BAVLI_DAF, "שבת דף עז עמוד א"),
            ("פתח ברכות יד א", SchemaType.BAVLI_DAF, "ברכות דף יד עמוד א"),
            ("פתח ברכות נח א", SchemaType.BAVLI_DAF, "ברכות דף נח עמוד א"),
            ("פתח שבת צו א", SchemaType.BAVLI_DAF, "שבת דף צו עמוד א"),
            ("שולחן ערוך אורח חיים מב", SchemaType.HALACHA_SIMAN, "שולחן ערוך אורח חיים סימן מב"),
            ("שולחן ערוך יורה דעה יד", SchemaType.HALACHA_SIMAN, "שולחן ערוך יורה דעה סימן יד"),
            ("משנה ברורה טז ד", SchemaType.HALACHA_SIMAN, "משנה ברורה אורח חיים סימן טז סק ד"),
            ("פתח חולין רשי דף קט עמוד ב", SchemaType.BAVLI_DAF, "רשי חולין דף קט עמוד ב"),
            ("פתח ברכות רן דף ב עמוד א", SchemaType.BAVLI_DAF, "רן ברכות דף ב עמוד א"),
        ]
        for query, expected_schema, expected_cmd in cases:
            r = matcher.match(query)
            assert r.status == MatchStatus.OK, f"Failed for {query!r}: {r}"
            assert r.schema_type == expected_schema, f"Wrong schema for {query!r}: {r}"
            assert r.canonical_command == expected_cmd, f"Wrong cmd for {query!r}: {r.canonical_command!r}"

    def test_all_83_rambam_hilchot_exact_roundtrip(self, matcher):
        for h in matcher.index.hilchot.values():
            r = matcher.match(f"רמבם {h.canonical_name} פרק א הלכה א")
            assert r.status == MatchStatus.OK, f"Failed for {h.canonical_name!r}: {r}"
            assert r.parsed_fields.get("hilchah") == h.id, (
                f"Wrong hilchah for {h.canonical_name!r}: got {r.canonical_command!r}"
            )

    def test_rif_uncovered_tractate_is_deterministic_invalid_combination(self, matcher):
        r = matcher.match("ריף חגיגה דף ב")
        assert r.status == MatchStatus.INVALID_COMBINATION
        assert r.canonical_command == ""
        assert "אין ריף" in r.tts_feedback and "חגיגה" in r.tts_feedback

    def test_whisper_taz_yoreh_deah_eiman_tzadik_seif_bet(self, matcher):
        r = matcher.match("תשלח תז יורה דעה עימן צדיק סעיף ב'")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["nosei_keli"] == "taz"
        assert r.parsed_fields["section"] == "yoreh_deah"
        assert r.parsed_fields["siman"] == 90
        assert r.parsed_fields["seif"] == 2
        assert r.canonical_command == "טז יורה דעה סימן צ סעיף ב"

    def test_whisper_niftach_taz_not_hijacked_by_pitchei_teshuvah(self, matcher):
        r = matcher.match("נפתח תז יורה דעה סימן צדיק תשב")
        assert r.status == MatchStatus.OK
        assert r.schema_type == SchemaType.HALACHA_SIMAN
        assert r.parsed_fields["nosei_keli"] == "taz"
        assert r.parsed_fields["section"] == "yoreh_deah"
        assert r.parsed_fields["siman"] == 90
        assert r.canonical_command == "טז יורה דעה סימן צ"

    def test_missing_primary_number_never_steals_secondary_structural_number(self, matcher):
        # "חולין עמוד ב" has no daf -> must NOT steal amud=2 as daf=2
        r1 = matcher.match("חולין עמוד ב")
        assert r1.status == MatchStatus.NEEDS_FALLBACK
        assert r1.canonical_command == ""
        # "בראשית פסוק ג" has no perek -> must NOT steal pasuk=3 as perek=3
        r2 = matcher.match("בראשית פסוק ג")
        assert r2.status == MatchStatus.NEEDS_FALLBACK
        assert r2.canonical_command == ""
        # "יורה דעה סעיף ב" has no siman -> must NOT steal seif=2 as siman=2
        r3 = matcher.match("יורה דעה סעיף ב")
        assert r3.status == MatchStatus.NEEDS_FALLBACK
        assert r3.canonical_command == ""


# ---------------------------------------------------------------------------
# INVALID_COMBINATION & Cross-cutting
# ---------------------------------------------------------------------------


class TestInvalidCombination:
    def test_case_24_or_hachaim(self, matcher):
        r = matcher.match("אור החיים ישעיהו פרק א")
        assert r.status == MatchStatus.INVALID_COMBINATION
        assert r.canonical_command == ""


class TestCrossCutting:
    def test_ok_results_have_clean_canonical_command(self, matcher):
        queries = [
            "פתח חולין דף קט עמוד א",
            'רמב"ם שבת פרק א הלכה א',
            "פתח פרשת בא",
            'שו"ע או"ח ש"א',
            'רש"י בבא קמא ל ע"ב',
        ]
        for q in queries:
            r = matcher.match(q)
            assert r.status == MatchStatus.OK
            assert r.canonical_command
            _assert_clean_payload(r.canonical_command)

    def test_non_ok_results_never_produce_canonical_command(self, matcher):
        queries = [
            "שבת דף קנח",
            "פתח חולין דף קט עמוד ג",
            'ש"ך סימן י',
            "קצות החושן אורח חיים סימן י",
            "אור החיים ישעיהו פרק א",
            "xyzzy unknown text",
        ]
        for q in queries:
            r = matcher.match(q)
            assert r.status != MatchStatus.OK
            assert r.canonical_command == ""
            assert r.tts_feedback
            assert r.latency_ms >= 0
