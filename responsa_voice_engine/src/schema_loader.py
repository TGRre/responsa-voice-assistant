"""Schema loader: reads data JSONs, builds SchemaIndex, validates parsed fields."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

from src.hebrew_utils import normalize_key, normalize_phonetic
from src.models import (
    BaseWork,
    Candidate,
    EntityHit,
    HalachaSection,
    Hilchah,
    MatchStatus,
    NoseKeli,
    Parasha,
    SchemaType,
    TanakhBook,
    TanakhCommentary,
    Tractate,
    TalmudCommentary,
    ValidationOutcome,
    DOMAIN_BAVLI_COMMENTARY,
    DOMAIN_BASE_WORK,
    DOMAIN_HALACHA_SECTION,
    DOMAIN_HILCHAH,
    DOMAIN_NOSE_KELI,
    DOMAIN_PARASHA,
    DOMAIN_TANAKH_BOOK,
    DOMAIN_TANAKH_COMMENTARY,
    DOMAIN_TRACTATE,
)

def _resolve_default_data_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "responsa_voice_engine" / "data"  # type: ignore[attr-defined]
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent.parent / "data"


_DEFAULT_DATA_DIR = _resolve_default_data_dir()


class SchemaIndex:
    """Precompiled index over all catalog entities for fast matching and validation."""

    def __init__(self) -> None:
        # by_id maps
        self.tractates: dict[str, Tractate] = {}
        self.bavli_commentaries: dict[str, TalmudCommentary] = {}
        self.hilchot: dict[str, Hilchah] = {}
        self.halacha_sections: dict[str, HalachaSection] = {}
        self.base_works: dict[str, BaseWork] = {}
        self.nosei_kelim: dict[str, NoseKeli] = {}
        self.tanakh_books: dict[str, TanakhBook] = {}
        self.tanakh_commentaries: dict[str, TanakhCommentary] = {}
        self.parashot: dict[str, Parasha] = {}

        # Tier-1: exact normalized key → EntityHit (primary match)
        self.exact_map: dict[str, EntityHit] = {}

        # Alternative matches per key (multi-domain, e.g. "מגילה" = tractate + Tanakh book,
        # "רשי" = bavli_commentary + tanakh_commentary)
        self._alt_map: dict[str, list[EntityHit]] = {}

        # Tier-2: per-domain list of (normalized_key, EntityHit)
        self.fuzzy_buckets: dict[str, list[tuple[str, EntityHit]]] = {
            DOMAIN_TRACTATE: [],
            DOMAIN_BAVLI_COMMENTARY: [],
            DOMAIN_HILCHAH: [],
            DOMAIN_HALACHA_SECTION: [],
            DOMAIN_BASE_WORK: [],
            DOMAIN_NOSE_KELI: [],
            DOMAIN_TANAKH_BOOK: [],
            DOMAIN_TANAKH_COMMENTARY: [],
            DOMAIN_PARASHA: [],
        }

        # Fuzzy candidate shortlists: (first_letter, length_band) → [(key, EntityHit)]
        self._fuzzy_shortlists: dict[tuple[str, int], list[tuple[str, EntityHit]]] = {}

        # Precomputed phonetic keys for fuzzy matching
        self._phonetic_keys: dict[str, str] = {}
        # Exact phonetic map for len>=3 keys (e.g. "תור"->"טור", "שבט"->"שבת")
        self._phonetic_exact_map: dict[str, str] = {}

        # Pre-flattened C++ rapidfuzz arrays by word count (1..max_word_count)
        self.max_word_count: int = 1
        self._flat_raw_keys: dict[int, list[str]] = {}
        self._flat_phon_keys: dict[int, list[str]] = {}
        self._flat_hits: dict[int, list[EntityHit]] = {}

    # ------------------------------------------------------------------
    # Entity registration
    # ------------------------------------------------------------------

    def _register(
        self, entity_id: str, canonical_name: str, domain: str,
        aliases: tuple[str, ...], score: float = 100.0,
    ) -> None:
        seen_keys: set[str] = set()
        for surface in (canonical_name, *aliases):
            key = normalize_key(surface)
            if not key:
                continue
            hit = EntityHit(
                entity_id=entity_id,
                canonical_name=canonical_name,
                domain=domain,
                score=score,
                matched_surface=surface,
            )
            if key not in self.exact_map:
                self.exact_map[key] = hit
            elif (
                self.exact_map[key].entity_id != entity_id
                or self.exact_map[key].domain != domain
            ):
                alts = self._alt_map.setdefault(key, [])
                if not any(
                    h.entity_id == entity_id and h.domain == domain for h in alts
                ):
                    alts.append(hit)

            self._phonetic_keys[key] = normalize_phonetic(surface)

            if key not in seen_keys:
                seen_keys.add(key)
                self.fuzzy_buckets[domain].append((key, hit))

    def _build_shortlists(self) -> None:
        """Build un-truncated shortlists and flat C++ rapidfuzz arrays for all word counts."""
        self._fuzzy_shortlists.clear()
        self._phonetic_exact_map.clear()
        self._flat_raw_keys = {}
        self._flat_phon_keys = {}
        self._flat_hits = {}
        self.max_word_count = 1

        # Deduplicate flat arrays by normalized key (exact_map + _alt_map recover all domains)
        seen_by_wc: dict[int, set[str]] = {}

        for entries in self.fuzzy_buckets.values():
            for key, hit in entries:
                if not key:
                    continue
                first = key[0]
                length = len(key)
                band = 0 if length <= 3 else (1 if length <= 6 else 2)
                self._fuzzy_shortlists.setdefault((first, band), []).append((key, hit))

                wc = key.count(" ") + 1
                if wc > self.max_word_count:
                    self.max_word_count = wc
                seen = seen_by_wc.setdefault(wc, set())
                if key not in seen:
                    seen.add(key)
                    pk = self._phonetic_keys.get(key, key)
                    self._flat_raw_keys.setdefault(wc, []).append(key)
                    self._flat_phon_keys.setdefault(wc, []).append(pk)
                    self._flat_hits.setdefault(wc, []).append(self.exact_map.get(key, hit))
                    if wc == 1 and length >= 3 and pk not in self._phonetic_exact_map:
                        self._phonetic_exact_map[pk] = key

    def get_fuzzy_candidates(self, token_key: str) -> list[tuple[str, EntityHit]]:
        """Get fuzzy match candidates bucketed by first letter × length band (no truncation)."""
        if not token_key:
            return []
        first = token_key[0]
        length = len(token_key)
        band = 0 if length <= 3 else (1 if length <= 6 else 2)
        results: list[tuple[str, EntityHit]] = []
        for b in (band - 1, band, band + 1):
            if b < 0:
                continue
            bucket = self._fuzzy_shortlists.get((first, b))
            if bucket:
                results.extend(bucket)
        return results

    def lookup_exact_key(self, key: str) -> list[EntityHit]:
        """Return primary + all alternative EntityHits registered under `key`."""
        primary = self.exact_map.get(key)
        if primary is None:
            return []
        alts = self._alt_map.get(key)
        if not alts:
            return [primary]
        return [primary, *alts]

    def fuzzy_match(
        self,
        key: str,
        phon_key: str,
        word_count: int,
        threshold: float,
        *,
        use_wratio: bool = True,
    ) -> tuple[str, float, list[EntityHit]] | None:
        """C++-accelerated rapidfuzz search over pre-flattened arrays.

        Computes exact blended score: 0.7 * raw_score + 0.3 * phon_score.
        Returns (matched_key, best_score, all_domain_hits) or None.
        """
        if word_count == 1 and len(key) >= 3 and phon_key in self._phonetic_exact_map:
            matched_key = self._phonetic_exact_map[phon_key]
            hits = self.lookup_exact_key(matched_key)
            if hits:
                return matched_key, 92.0, hits

        raw_keys = self._flat_raw_keys.get(word_count)
        if not raw_keys:
            return None
        phon_keys = self._flat_phon_keys[word_count]
        scorer = fuzz.WRatio if use_wratio else fuzz.ratio

        # Mathematical lower bound: 0.7 * raw + 0.3 * 100 >= threshold => raw >= (threshold - 30) / 0.7
        # Use limit=None so phonetic matches are never dropped before blending.
        raw_cutoff = max(0.0, (threshold - 30.0) / 0.7)
        raw_matches = process.extract(
            key, raw_keys, scorer=scorer, score_cutoff=raw_cutoff, limit=None,
        )
        if not raw_matches:
            return None

        best_idx = -1
        best_score = 0.0
        for _matched_str, raw_score, idx in raw_matches:
            phon_score = scorer(phon_key, phon_keys[idx]) if phon_key else 0.0
            blended = 0.7 * raw_score + 0.3 * phon_score
            if blended >= threshold and blended > best_score:
                best_score = blended
                best_idx = idx

        if best_idx < 0:
            return None

        matched_key = raw_keys[best_idx]
        hits = self.lookup_exact_key(matched_key)
        if not hits:
            hits = [self._flat_hits[word_count][best_idx]]
        return matched_key, best_score, hits

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_parsed_fields(
        self, schema_type: SchemaType, fields: dict,
    ) -> ValidationOutcome:
        """Validate parsed fields against catalog bounds. Single source of truth."""
        if schema_type == SchemaType.BAVLI_DAF:
            return self._validate_bavli_daf(fields)
        if schema_type == SchemaType.ROSH_PEREK_SIMAN:
            return self._validate_rosh_perek_siman(fields)
        if schema_type == SchemaType.RIF_DAF:
            return self._validate_rif_daf(fields)
        if schema_type == SchemaType.RAMBAM_HALACHA:
            return self._validate_rambam_halacha(fields)
        if schema_type == SchemaType.HALACHA_SIMAN:
            return self._validate_halacha_siman(fields)
        if schema_type == SchemaType.TANAKH_PEREK_PASUK:
            return self._validate_tanakh_perek_pasuk(fields)
        if schema_type == SchemaType.PARASHA:
            return self._validate_parasha(fields)
        return ValidationOutcome(
            status=MatchStatus.NEEDS_FALLBACK,
            errors=(f"Unknown schema type: {schema_type}",),
        )

    def _validate_bavli_daf(self, fields: dict) -> ValidationOutcome:
        tractate_id = fields.get("tractate")
        daf = fields.get("daf")
        amud = fields.get("amud")
        if amud is None:
            amud = "א"
        tractate = self.tractates.get(tractate_id)
        if tractate is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown tractate: {tractate_id}",),
            )
        if daf is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing daf number",),
            )
        if not isinstance(daf, int) or daf < 2 or daf > tractate.max_daf:
            max_gemara = self._int_to_gem_str(tractate.max_daf)
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Daf {daf} out of range 2-{tractate.max_daf} for {tractate.canonical_name}",
                ),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "max_daf": tractate.max_daf,
                    "max_daf_gematria": max_gemara,
                },
            )
        if amud not in ("א", "ב"):
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(f"Invalid amud {amud} for {tractate.canonical_name}",),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "daf": daf,
                    "amud": amud,
                    "invalid_amud": True,
                },
            )
        result_fields: dict = {
            "tractate": tractate_id,
            "tractate_name": tractate.canonical_name,
            "daf": daf,
            "amud": amud,
            "max_daf": tractate.max_daf,
        }
        commentary_id = fields.get("commentary")
        if commentary_id:
            comm = self.bavli_commentaries.get(commentary_id)
            if comm is not None:
                result_fields["commentary"] = comm.id
                result_fields["commentary_name"] = comm.canonical_name
            elif fields.get("commentary_name"):
                result_fields["commentary"] = commentary_id
                result_fields["commentary_name"] = fields["commentary_name"]
        return ValidationOutcome(
            status=MatchStatus.OK,
            normalized_fields=result_fields,
        )

    def _validate_rosh_perek_siman(self, fields: dict) -> ValidationOutcome:
        tractate_id = fields.get("tractate")
        perek = fields.get("perek")
        siman = fields.get("siman")
        tractate = self.tractates.get(tractate_id)
        if tractate is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown tractate: {tractate_id}",),
            )
        if perek is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing perek",),
            )
        if not isinstance(perek, int) or perek < 1 or perek > tractate.max_perek:
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Perek {perek} out of range 1-{tractate.max_perek} for {tractate.canonical_name}",
                ),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "max_perek": tractate.max_perek,
                },
            )
        result_fields: dict = {
            "tractate": tractate_id,
            "tractate_name": tractate.canonical_name,
            "perek": perek,
        }
        if siman is not None:
            if not isinstance(siman, int) or siman < 1:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(f"Siman {siman} must be ≥ 1",),
                    normalized_fields=result_fields,
                )
            result_fields["siman"] = siman
        return ValidationOutcome(
            status=MatchStatus.OK,
            normalized_fields=result_fields,
        )

    def _validate_rif_daf(self, fields: dict) -> ValidationOutcome:
        tractate_id = fields.get("tractate")
        daf = fields.get("daf")
        amud = fields.get("amud")
        if amud is None:
            amud = "א"
        tractate = self.tractates.get(tractate_id)
        if tractate is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown tractate: {tractate_id}",),
            )
        if tractate.rif_max_daf is None:
            return ValidationOutcome(
                status=MatchStatus.INVALID_COMBINATION,
                errors=(
                    f"No Rif text available for {tractate.canonical_name}",
                ),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "rif_available": False,
                },
            )
        if daf is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing daf number",),
            )
        if not isinstance(daf, int) or daf < 1 or daf > tractate.rif_max_daf:
            max_gem = self._int_to_gem_str(tractate.rif_max_daf)
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Rif daf {daf} out of range 1-{tractate.rif_max_daf} for {tractate.canonical_name}",
                ),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "rif_max_daf": tractate.rif_max_daf,
                    "rif_max_daf_gematria": max_gem,
                },
            )
        if amud not in ("א", "ב"):
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(f"Invalid amud {amud} for Rif {tractate.canonical_name}",),
                normalized_fields={
                    "tractate": tractate_id,
                    "tractate_name": tractate.canonical_name,
                    "daf": daf,
                    "amud": amud,
                    "invalid_amud": True,
                },
            )
        result_fields: dict = {
            "tractate": tractate_id,
            "tractate_name": tractate.canonical_name,
            "daf": daf,
            "amud": amud,
            "rif_max_daf": tractate.rif_max_daf,
        }
        commentary_id = fields.get("commentary")
        if commentary_id:
            comm = self.bavli_commentaries.get(commentary_id)
            if comm is not None:
                result_fields["commentary"] = comm.id
                result_fields["commentary_name"] = comm.canonical_name
        return ValidationOutcome(
            status=MatchStatus.OK,
            normalized_fields=result_fields,
        )

    def _validate_rambam_halacha(self, fields: dict) -> ValidationOutcome:
        hilchah_id = fields.get("hilchah")
        perek = fields.get("perek")
        halacha = fields.get("halacha")
        hilchah = self.hilchot.get(hilchah_id)
        if hilchah is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown hilchah: {hilchah_id}",),
            )
        if perek is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing perek",),
            )
        if not isinstance(perek, int) or perek < 1 or perek > hilchah.max_perek:
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Perek {perek} out of range 1-{hilchah.max_perek} for {hilchah.canonical_name}",
                ),
                normalized_fields={
                    "hilchah": hilchah_id,
                    "hilchah_name": hilchah.canonical_name,
                    "max_perek": hilchah.max_perek,
                },
            )
        result_fields: dict = {
            "hilchah": hilchah_id,
            "hilchah_name": hilchah.canonical_name,
            "sefer": hilchah.sefer,
            "perek": perek,
        }
        if halacha is not None:
            if not isinstance(halacha, int) or halacha < 1:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(f"Halacha {halacha} must be ≥ 1",),
                    normalized_fields=result_fields,
                )
            result_fields["halacha"] = halacha
        return ValidationOutcome(
            status=MatchStatus.OK,
            normalized_fields=result_fields,
        )

    def _validate_halacha_siman(self, fields: dict) -> ValidationOutcome:
        nosei_keli_id = fields.get("nosei_keli")
        base_work_id = fields.get("base_work")
        section_id = fields.get("section")
        siman = fields.get("siman")

        # Resolve nosei keli or base work (nosei_keli takes precedence over base_work)
        nk = self.nosei_kelim.get(nosei_keli_id) if nosei_keli_id else None
        bw = self.base_works.get(base_work_id) if base_work_id else None
        # Backward compat: caller passed a base_work id in nosei_keli
        if nk is None and bw is None and nosei_keli_id:
            bw = self.base_works.get(nosei_keli_id)

        # Default to Shulchan Aruch when section + siman are given without an explicit work
        if nk is None and bw is None and section_id and siman is not None:
            bw = self.base_works.get("shulchan_aruch")

        if nk is None and bw is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown nosei keli or base work: {nosei_keli_id or base_work_id}",),
            )

        if siman is None or not isinstance(siman, int) or siman < 1:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing or invalid siman number",),
            )

        work_id = nk.id if nk is not None else bw.id
        work_name = nk.canonical_name if nk is not None else bw.canonical_name

        # Get applicable sections
        if nk is not None:
            applicable = nk.applicable_sections
        else:
            applicable = tuple(s.canonical_name for s in self.halacha_sections.values())

        # Resolve section
        section_obj = None
        inferred = False
        if section_id:
            section_obj = self.halacha_sections.get(section_id)
            if section_obj is None:
                for s in self.halacha_sections.values():
                    if s.canonical_name == section_id or s.id == section_id:
                        section_obj = s
                        break
            if section_obj is None:
                return ValidationOutcome(
                    status=MatchStatus.NEEDS_FALLBACK,
                    errors=(f"Unknown section: {section_id}",),
                )
            if section_obj.canonical_name not in applicable:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_COMBINATION,
                    errors=(
                        f"{work_name} does not apply to {section_obj.canonical_name}",
                    ),
                    normalized_fields={
                        "nosei_keli": work_id,
                        "work_name": work_name,
                        "section": section_obj.id,
                        "section_name": section_obj.canonical_name,
                        "applicable_sections": list(applicable),
                    },
                )
        else:
            # Auto-infer section
            if len(applicable) == 1:
                section_name = applicable[0]
                for s in self.halacha_sections.values():
                    if s.canonical_name == section_name:
                        section_obj = s
                        inferred = True
                        break
            elif len(applicable) > 1:
                candidates = []
                for sec_name in applicable:
                    for s in self.halacha_sections.values():
                        if s.canonical_name == sec_name:
                            candidates.append(Candidate(
                                label=s.canonical_name,
                                entity_id=s.id,
                                score=0.0,
                            ))
                            break
                return ValidationOutcome(
                    status=MatchStatus.AMBIGUOUS_SECTION,
                    errors=(
                        f"Section ambiguous for {work_name}: {', '.join(applicable)}",
                    ),
                    normalized_fields={
                        "nosei_keli": work_id,
                        "nosei_keli_name": work_name,
                        "siman": siman,
                        "candidates": [c.label for c in candidates],
                        "candidate_ids": [c.entity_id for c in candidates],
                    },
                )

        if section_obj is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Could not resolve section",),
            )

        # Validate siman range
        if siman > section_obj.max_siman:
            max_gem = self._int_to_gem_str(section_obj.max_siman)
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Siman {siman} out of range 1-{section_obj.max_siman} for {section_obj.canonical_name}",
                ),
                normalized_fields={
                    "section": section_obj.id,
                    "section_name": section_obj.canonical_name,
                    "max_siman": section_obj.max_siman,
                    "max_siman_gematria": max_gem,
                },
            )

        result_fields: dict = {
            "nosei_keli": work_id,
            "nosei_keli_name": work_name,
            "section": section_obj.id,
            "section_name": section_obj.canonical_name,
            "siman": siman,
        }
        if inferred:
            result_fields["section_inferred"] = True

        seif = fields.get("seif")
        seif_katan = fields.get("seif_katan")
        if seif is not None:
            if not isinstance(seif, int) or seif < 1:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(f"Seif {seif} must be ≥ 1",),
                    normalized_fields=result_fields,
                )
            result_fields["seif"] = seif
        if seif_katan is not None:
            if not isinstance(seif_katan, int) or seif_katan < 1:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(f"Seif katan {seif_katan} must be ≥ 1",),
                    normalized_fields=result_fields,
                )
            result_fields["seif_katan"] = seif_katan

        return ValidationOutcome(status=MatchStatus.OK, normalized_fields=result_fields)

    def _validate_tanakh_perek_pasuk(self, fields: dict) -> ValidationOutcome:
        book_id = fields.get("book")
        perek = fields.get("perek")
        pasuk = fields.get("pasuk")
        commentary_id = fields.get("commentary")

        book = self.tanakh_books.get(book_id)
        if book is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown book: {book_id}",),
            )
        if perek is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=("Missing perek",),
            )
        if not isinstance(perek, int) or perek < 1 or perek > book.max_perek:
            max_gem = self._int_to_gem_str(book.max_perek)
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(
                    f"Perek {perek} out of range 1-{book.max_perek} for {book.canonical_name}",
                ),
                normalized_fields={
                    "book": book_id,
                    "book_name": book.canonical_name,
                    "max_perek": book.max_perek,
                    "max_perek_gematria": max_gem,
                },
            )

        if pasuk is not None and (not isinstance(pasuk, int) or pasuk < 1):
            return ValidationOutcome(
                status=MatchStatus.INVALID_RANGE,
                errors=(f"Pasuk {pasuk} must be ≥ 1",),
                normalized_fields={"book": book_id, "book_name": book.canonical_name},
            )

        # Validate commentary compatibility
        commentary_obj = None
        if commentary_id:
            commentary_obj = self.tanakh_commentaries.get(commentary_id)
            if commentary_obj is None:
                return ValidationOutcome(
                    status=MatchStatus.NEEDS_FALLBACK,
                    errors=(f"Unknown commentary: {commentary_id}",),
                )
            if book.division not in commentary_obj.divisions:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_COMBINATION,
                    errors=(
                        f"{commentary_obj.canonical_name} does not cover {book.division} "
                        f"(covers: {', '.join(commentary_obj.divisions)})",
                    ),
                    normalized_fields={
                        "book": book_id,
                        "book_name": book.canonical_name,
                        "commentary": commentary_id,
                        "commentary_name": commentary_obj.canonical_name,
                        "book_division": book.division,
                        "commentary_divisions": list(commentary_obj.divisions),
                    },
                )

        # Preserve "מגילה" when user specifically said "מגילה" for Esther
        display_book_name = book.canonical_name
        if book.id == "esther" and fields.get("book_name") == "מגילה":
            display_book_name = "מגילה"

        result_fields: dict = {
            "book": book_id,
            "book_name": display_book_name,
            "perek": perek,
        }
        if pasuk is not None:
            result_fields["pasuk"] = pasuk
        if commentary_obj is not None:
            result_fields["commentary"] = commentary_obj.id
            result_fields["commentary_name"] = commentary_obj.canonical_name

        return ValidationOutcome(status=MatchStatus.OK, normalized_fields=result_fields)

    def _validate_parasha(self, fields: dict) -> ValidationOutcome:
        parasha_id = fields.get("parasha")
        perek = fields.get("perek")
        pasuk = fields.get("pasuk")
        commentary_id = fields.get("commentary")

        parasha = self.parashot.get(parasha_id)
        if parasha is None:
            return ValidationOutcome(
                status=MatchStatus.NEEDS_FALLBACK,
                errors=(f"Unknown parasha: {parasha_id}",),
            )

        result_fields: dict = {
            "parasha": parasha_id,
            "parasha_name": parasha.canonical_name,
            "book": parasha.book,
            "start_perek": parasha.start_perek,
            "start_pasuk": parasha.start_pasuk,
            "end_perek": parasha.end_perek,
            "end_pasuk": parasha.end_pasuk,
        }

        if commentary_id:
            commentary = self.tanakh_commentaries.get(commentary_id)
            if commentary is None:
                return ValidationOutcome(
                    status=MatchStatus.NEEDS_FALLBACK,
                    errors=(f"Unknown commentary: {commentary_id}",),
                )
            if "תורה" not in commentary.divisions:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_COMBINATION,
                    errors=(
                        f"{commentary.canonical_name} does not cover תורה",
                    ),
                    normalized_fields={
                        **result_fields,
                        "commentary": commentary_id,
                        "commentary_name": commentary.canonical_name,
                    },
                )
            result_fields["commentary"] = commentary.id
            result_fields["commentary_name"] = commentary.canonical_name

        if perek is not None:
            if not isinstance(perek, int) or perek < parasha.start_perek or perek > parasha.end_perek:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(
                        f"Perek {perek} outside parasha range "
                        f"{parasha.start_perek}-{parasha.end_perek} for {parasha.canonical_name}",
                    ),
                    normalized_fields=result_fields,
                )
            result_fields["perek"] = perek

        if pasuk is not None:
            if not isinstance(pasuk, int) or pasuk < 1:
                return ValidationOutcome(
                    status=MatchStatus.INVALID_RANGE,
                    errors=(f"Pasuk {pasuk} must be ≥ 1",),
                    normalized_fields=result_fields,
                )
            result_fields["pasuk"] = pasuk

        return ValidationOutcome(status=MatchStatus.OK, normalized_fields=result_fields)

    @staticmethod
    def _int_to_gem_str(n: int) -> str:
        from src.hebrew_utils import int_to_gematria
        try:
            return int_to_gematria(n)
        except ValueError:
            return str(n)


def load_schema_index(data_dir: Path | None = None) -> SchemaIndex:
    """Load all 4 data JSON files and build a precompiled SchemaIndex."""
    data_dir = data_dir or _DEFAULT_DATA_DIR
    idx = SchemaIndex()

    # Load bavli_and_commentaries.json
    with open(data_dir / "bavli_and_commentaries.json", encoding="utf-8") as f:
        bavli_data = json.load(f)
    for item in bavli_data["bavli_tractates"]:
        t = Tractate(
            id=item["id"],
            canonical_name=item["canonical_name"],
            max_daf=item["max_daf"],
            rif_max_daf=item.get("rif_max_daf"),
            max_perek=item["max_perek"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.tractates[t.id] = t
        idx._register(t.id, t.canonical_name, DOMAIN_TRACTATE, t.aliases)

    for item in bavli_data["talmud_commentaries"]:
        c = TalmudCommentary(
            id=item["id"],
            canonical_name=item["canonical_name"],
            category=item["category"],
            ref_type=item["ref_type"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.bavli_commentaries[c.id] = c
        idx._register(c.id, c.canonical_name, DOMAIN_BAVLI_COMMENTARY, c.aliases)

    # Load rambam.json
    with open(data_dir / "rambam.json", encoding="utf-8") as f:
        rambam_data = json.load(f)
    for item in rambam_data["rambam_hilchot"]:
        h = Hilchah(
            id=item["id"],
            canonical_name=item["canonical_name"],
            sefer=item["sefer"],
            max_perek=item["max_perek"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.hilchot[h.id] = h
        idx._register(h.id, h.canonical_name, DOMAIN_HILCHAH, h.aliases)

    # Load halacha_codes.json
    with open(data_dir / "halacha_codes.json", encoding="utf-8") as f:
        halacha_data = json.load(f)
    for item in halacha_data["sections"]:
        s = HalachaSection(
            id=item["id"],
            canonical_name=item["canonical_name"],
            max_siman=item["max_siman"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.halacha_sections[s.id] = s
        idx._register(s.id, s.canonical_name, DOMAIN_HALACHA_SECTION, s.aliases)

    for item in halacha_data["base_works"]:
        b = BaseWork(
            id=item["id"],
            canonical_name=item["canonical_name"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.base_works[b.id] = b
        idx._register(b.id, b.canonical_name, DOMAIN_BASE_WORK, b.aliases)

    for item in halacha_data["nosei_kelim"]:
        nk = NoseKeli(
            id=item["id"],
            canonical_name=item["canonical_name"],
            applicable_sections=tuple(item["applicable_sections"]),
            sub_unit=item["sub_unit"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.nosei_kelim[nk.id] = nk
        idx._register(nk.id, nk.canonical_name, DOMAIN_NOSE_KELI, nk.aliases)

    # Load tanakh_and_parashot.json
    with open(data_dir / "tanakh_and_parashot.json", encoding="utf-8") as f:
        tanakh_data = json.load(f)
    for item in tanakh_data["books"]:
        b = TanakhBook(
            id=item["id"],
            canonical_name=item["canonical_name"],
            division=item["division"],
            max_perek=item["max_perek"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.tanakh_books[b.id] = b
        idx._register(b.id, b.canonical_name, DOMAIN_TANAKH_BOOK, b.aliases)

    for item in tanakh_data["commentaries"]:
        c = TanakhCommentary(
            id=item["id"],
            canonical_name=item["canonical_name"],
            divisions=tuple(item["divisions"]),
            aliases=tuple(item.get("aliases", [])),
        )
        idx.tanakh_commentaries[c.id] = c
        idx._register(c.id, c.canonical_name, DOMAIN_TANAKH_COMMENTARY, c.aliases)

    for item in tanakh_data["parashot"]:
        p = Parasha(
            id=item["id"],
            canonical_name=item["canonical_name"],
            book=item["book"],
            start_perek=item["start_perek"],
            start_pasuk=item["start_pasuk"],
            end_perek=item["end_perek"],
            end_pasuk=item["end_pasuk"],
            aliases=tuple(item.get("aliases", [])),
        )
        idx.parashot[p.id] = p
        idx._register(p.id, p.canonical_name, DOMAIN_PARASHA, p.aliases)

    idx._build_shortlists()
    return idx
