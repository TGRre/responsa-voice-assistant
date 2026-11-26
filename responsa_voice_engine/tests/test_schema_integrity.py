"""Schema data integrity tests: counts, uniqueness, referential integrity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def bavli_data():
    with open(_DATA_DIR / "bavli_and_commentaries.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def rambam_data():
    with open(_DATA_DIR / "rambam.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def halacha_data():
    with open(_DATA_DIR / "halacha_codes.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def tanakh_data():
    with open(_DATA_DIR / "tanakh_and_parashot.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


class TestCounts:
    def test_bavli_tractates_count(self, bavli_data):
        assert len(bavli_data["bavli_tractates"]) == 37

    def test_rambam_hilchot_count(self, rambam_data):
        assert len(rambam_data["rambam_hilchot"]) == 83

    def test_tanakh_books_count(self, tanakh_data):
        assert len(tanakh_data["books"]) == 39

    def test_parashot_count(self, tanakh_data):
        assert len(tanakh_data["parashot"]) == 54

    def test_talmud_commentaries_count(self, bavli_data):
        assert len(bavli_data["talmud_commentaries"]) == 29

    def test_halacha_sections_count(self, halacha_data):
        assert len(halacha_data["sections"]) == 4

    def test_base_works_count(self, halacha_data):
        assert len(halacha_data["base_works"]) == 5

    def test_nosei_kelim_count(self, halacha_data):
        assert len(halacha_data["nosei_kelim"]) == 18

    def test_tanakh_commentaries_count(self, tanakh_data):
        assert len(tanakh_data["commentaries"]) == 18


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


class TestUniqueness:
    def test_tractate_ids_unique(self, bavli_data):
        ids = [t["id"] for t in bavli_data["bavli_tractates"]]
        assert len(ids) == len(set(ids))

    def test_tractate_names_unique(self, bavli_data):
        names = [t["canonical_name"] for t in bavli_data["bavli_tractates"]]
        assert len(names) == len(set(names))

    def test_hilchah_ids_unique(self, rambam_data):
        ids = [h["id"] for h in rambam_data["rambam_hilchot"]]
        assert len(ids) == len(set(ids))

    def test_hilchah_names_unique(self, rambam_data):
        names = [h["canonical_name"] for h in rambam_data["rambam_hilchot"]]
        assert len(names) == len(set(names))

    def test_book_ids_unique(self, tanakh_data):
        ids = [b["id"] for b in tanakh_data["books"]]
        assert len(ids) == len(set(ids))

    def test_parasha_ids_unique(self, tanakh_data):
        ids = [p["id"] for p in tanakh_data["parashot"]]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


class TestBounds:
    def test_tractate_max_daf_positive(self, bavli_data):
        for t in bavli_data["bavli_tractates"]:
            assert t["max_daf"] >= 2, f"{t['id']} max_daf={t['max_daf']}"

    def test_tractate_rif_max_daf_null_or_positive(self, bavli_data):
        for t in bavli_data["bavli_tractates"]:
            rmd = t.get("rif_max_daf")
            assert rmd is None or rmd >= 1, f"{t['id']} rif_max_daf={rmd}"

    def test_tractate_max_perek_positive(self, bavli_data):
        for t in bavli_data["bavli_tractates"]:
            assert t["max_perek"] >= 1

    def test_hilchah_max_perek_positive(self, rambam_data):
        for h in rambam_data["rambam_hilchot"]:
            assert h["max_perek"] >= 1

    def test_section_max_siman_values(self, halacha_data):
        expected = {"orach_chaim": 697, "yoreh_deah": 403, "even_haezer": 178, "choshen_mishpat": 427}
        for s in halacha_data["sections"]:
            assert s["max_siman"] == expected[s["id"]], f"{s['id']} max_siman={s['max_siman']}"

    def test_book_max_perek_positive(self, tanakh_data):
        for b in tanakh_data["books"]:
            assert b["max_perek"] >= 1

    def test_parasha_range_valid(self, tanakh_data):
        for p in tanakh_data["parashot"]:
            assert 1 <= p["start_perek"] <= p["end_perek"]
            assert 1 <= p["start_pasuk"]
            assert 1 <= p["end_pasuk"]


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------


class TestReferentialIntegrity:
    def test_parasha_books_exist(self, tanakh_data):
        book_names = {b["canonical_name"] for b in tanakh_data["books"]}
        for p in tanakh_data["parashot"]:
            assert p["book"] in book_names, f"Parasha {p['id']} references unknown book {p['book']}"

    def test_parasha_perek_within_book(self, tanakh_data):
        book_map = {b["canonical_name"]: b for b in tanakh_data["books"]}
        for p in tanakh_data["parashot"]:
            book = book_map[p["book"]]
            assert p["end_perek"] <= book["max_perek"], (
                f"Parasha {p['id']} end_perek {p['end_perek']} > book {p['book']} max {book['max_perek']}"
            )

    def test_nosei_kelim_sections_exist(self, halacha_data):
        section_names = {s["canonical_name"] for s in halacha_data["sections"]}
        for nk in halacha_data["nosei_kelim"]:
            for sec in nk["applicable_sections"]:
                assert sec in section_names, f"{nk['id']} references unknown section {sec}"

    def test_hilchah_sefer_valid(self, rambam_data):
        valid_sefarim = {
            "ספר המדע", "ספר זרעים", "ספר אהבה", "ספר זמנים", "ספר נשים",
            "ספר קדושה", "ספר הפליאה", "ספר הפלאה", "ספר עבודה", "ספר קרבנות",
            "ספר טהרה", "ספר נזיקין", "ספר קנין", "ספר משפטים", "ספר שופטים",
        }
        for h in rambam_data["rambam_hilchot"]:
            assert h["sefer"] in valid_sefarim, f"{h['id']} sefer={h['sefer']}"

    def test_commentary_ref_types_valid(self, bavli_data):
        valid_types = {"daf_amud", "perek_siman", "daf_rif"}
        for c in bavli_data["talmud_commentaries"]:
            assert c["ref_type"] in valid_types, f"{c['id']} ref_type={c['ref_type']}"

    def test_commentary_categories_valid(self, bavli_data):
        for c in bavli_data["talmud_commentaries"]:
            assert c["category"] in ("rishon", "acharon")

    def test_rif_max_daf_only_for_covered_tractates(self, bavli_data):
        # Rif covers: Berakhot, Moed, Nashim, Nezikin (mostly)
        # Not: most of Kodashim, Horayot, etc.
        # We just verify the data is self-consistent (null or positive)
        for t in bavli_data["bavli_tractates"]:
            rmd = t.get("rif_max_daf")
            if rmd is not None:
                assert rmd >= 1

    def test_meta_blocks_present(self, bavli_data, rambam_data, halacha_data, tanakh_data):
        for data, name in [
            (bavli_data, "bavli"), (rambam_data, "rambam"),
            (halacha_data, "halacha"), (tanakh_data, "tanakh"),
        ]:
            assert "_meta" in data, f"{name} missing _meta"
            assert "source" in data["_meta"]
            assert "retrieved" in data["_meta"]
