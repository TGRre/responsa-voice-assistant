"""Command matcher: routes, parses, and validates Hebrew voice commands."""

from __future__ import annotations

import time

from src.formatter import ResponsaFormatter
from src.hebrew_utils import (
    clean_spoken_command,
    fix_structural_errors,
    is_valid_gematria_order,
    normalize_key,
    normalize_phonetic,
    parse_hebrew_number,
    parse_spoken_number,
    strip_prefix_variants,
)
from src.models import (
    Candidate,
    CleanedCommand,
    EntityHit,
    MatchResult,
    MatchStatus,
    SchemaType,
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
from src.schema_loader import SchemaIndex

# Structural marker words
_STRUCTURAL = {
    "דף": "daf",
    "עמוד": "amud",
    "פרק": "perek",
    "הלכה": "halacha",
    "סימן": "siman",
    "סעיף": "seif",
    "סעיף_קטן": "seif_katan",
    "פסוק": "pasuk",
    "פרשת": "parasha",
}

_NUMBER_EXPECTING_ROLES = {
    "daf", "amud", "perek", "halacha", "siman", "seif", "seif_katan", "pasuk",
}

# Domains that represent a target work/section able to take a shorthand numeric tail
_TARGET_WORK_DOMAINS = {
    DOMAIN_TRACTATE,
    DOMAIN_HILCHAH,
    DOMAIN_TANAKH_BOOK,
    DOMAIN_HALACHA_SECTION,
    DOMAIN_NOSE_KELI,
}

# Target domains that are immediately followed by a number/marker (never another section/work)
_STRICT_NUMBER_TAIL_DOMAINS = {
    DOMAIN_TRACTATE,
    DOMAIN_HILCHAH,
    DOMAIN_TANAKH_BOOK,
    DOMAIN_HALACHA_SECTION,
}

# Schema trigger words
_TRIGGER_RAMBAM = {"רמבם", "משנה", "תורה", "הלכות", "הלכה"}
_TRIGGER_ROSH = {"ראש"}
_TRIGGER_RIF = {"ריף"}

# Triggers that should NOT be swallowed into a multi-word fuzzy phrase
_HARD_TRIGGERS = {"רמבם", "ריף", "ראש"}

# Length-aware fuzzy thresholds
_THRESH_SHORT = 88.0  # ≤3 chars
_THRESH_MED = 76.0    # 4-6 chars
_THRESH_LONG = 70.0   # ≥7 chars
_THRESH_MULTI = 82.0  # 2-word / 3-word phrases (plain Levenshtein ratio)


def _fuzzy_threshold(token_len: int) -> float:
    if token_len <= 3:
        return _THRESH_SHORT
    if token_len <= 6:
        return _THRESH_MED
    return _THRESH_LONG


def _hits_include_target_work(hits: list[EntityHit]) -> bool:
    return any(h.domain in _TARGET_WORK_DOMAINS for h in hits)


class CommandMatcher:
    """Matches noisy Hebrew voice commands to validated reference schemas."""

    def __init__(self, index: SchemaIndex) -> None:
        self.index = index
        self.formatter = ResponsaFormatter()

    def match(self, text: str) -> MatchResult:
        """Match a raw Whisper transcript to a validated MatchResult."""
        t0 = time.perf_counter_ns()

        cleaned = clean_spoken_command(text)
        fixed = fix_structural_errors(cleaned.text)
        tokens = fixed.split()

        # Classify tokens: entities, structural markers, triggers, numbers
        classified = self._classify_tokens(tokens)

        # Route to schema (deterministic anchor-first order)
        schema = self._route_schema(classified, cleaned)

        if schema is None:
            latency = (time.perf_counter_ns() - t0) / 1e6
            return MatchResult(
                status=MatchStatus.NEEDS_FALLBACK,
                schema_type=None,
                confidence=0.0,
                parsed_fields={},
                canonical_command="",
                top_candidates=[],
                tts_feedback="לא הצלחתי להבין, נסה שוב",
                latency_ms=latency,
                action=cleaned.action,
            )

        # Fill slots
        fields, confidence, inferred = self._fill_slots(schema, classified)

        # Validate (single source of truth)
        outcome = self.index.validate_parsed_fields(schema, fields)

        # Honor section_inferred returned by validator
        if outcome.normalized_fields.get("section_inferred"):
            inferred = True

        fmt_fields = dict(outcome.normalized_fields) if outcome.normalized_fields else dict(fields)

        # Only format canonical_command when validation succeeded
        if outcome.status == MatchStatus.OK:
            canonical = self.formatter.format_command(schema, fmt_fields)
        else:
            canonical = ""

        tts = self.formatter.format_tts(outcome.status, schema, fmt_fields)

        # Adjust confidence
        if inferred:
            confidence = min(confidence, 75.0)

        # Top candidates (only for genuinely ambiguous results)
        top_candidates: list[Candidate] = []
        if outcome.status == MatchStatus.AMBIGUOUS_SECTION:
            top_candidates = self._build_top_candidates(schema, outcome, fields, confidence)

        latency = (time.perf_counter_ns() - t0) / 1e6

        return MatchResult(
            status=outcome.status,
            schema_type=schema,
            confidence=confidence,
            parsed_fields=fmt_fields,
            canonical_command=canonical,
            top_candidates=top_candidates,
            tts_feedback=tts,
            latency_ms=latency,
            action=cleaned.action,
        )

    # ------------------------------------------------------------------
    # Token classification
    # ------------------------------------------------------------------

    def _classify_tokens(self, tokens: list[str]) -> list[dict]:
        """Classify each token as structural marker, number, entity, trigger, or unknown."""
        result: list[dict] = []
        i = 0
        n_tokens = len(tokens)
        last_role: str | None = None
        last_was_target_work = False
        last_entity_domains: set[str] = set()
        max_wc = self.index.max_word_count

        while i < n_tokens:
            tok = tokens[i]

            # 1. Structural marker?
            if tok in _STRUCTURAL:
                role = _STRUCTURAL[tok]
                result.append({"type": "structural", "role": role, "token": tok, "idx": i})
                last_role = role
                last_was_target_work = False
                last_entity_domains = set()
                i += 1
                continue

            # 2. After number-expecting structural markers, try numbers BEFORE entities
            if last_role in _NUMBER_EXPECTING_ROLES:
                # Allow spaced single-letter gematria (e.g. "דף ק ט עמוד ב"),
                # but do NOT merge a trailing "א"/"ב" into daf when no "עמוד" follows
                allow_spaced = True
                if last_role == "daf" and i + 1 < n_tokens:
                    nxt = normalize_key(tokens[i + 1])
                    has_amud_ahead = "עמוד" in tokens[i + 2 :]
                    if nxt in ("א", "ב") and not has_amud_ahead and i + 1 == n_tokens - 1:
                        allow_spaced = False

                spk = parse_spoken_number(tokens, i, allow_spaced_gematria=allow_spaced)
                if spk is not None and spk[1] > i:
                    val, next_i = spk
                    result.append({
                        "type": "number", "value": val, "token": tok,
                        "idx": i, "span": next_i - i,
                    })
                    last_role = "number"
                    last_was_target_work = True
                    last_entity_domains = set()
                    i = next_i
                    continue
                num = parse_hebrew_number(tok)
                if num is not None:
                    result.append({"type": "number", "value": num, "token": tok, "idx": i})
                    last_role = "number"
                    last_was_target_work = True
                    last_entity_domains = set()
                    i += 1
                    continue

            # 3. Exact multi-word entity match (longest first, up to catalog max_word_count)
            entity_hits: list[EntityHit] = []
            consumed = 1
            upper_span = min(max_wc, n_tokens - i)
            for span in range(upper_span, 1, -1):
                phrase = " ".join(tokens[i : i + span])
                hits = self._lookup_all_exact(phrase)
                if hits:
                    entity_hits = hits
                    consumed = span
                    break

            if entity_hits:
                result.append({
                    "type": "entity",
                    "hit": entity_hits[0],
                    "alternatives": entity_hits[1:],
                    "token": " ".join(tokens[i : i + consumed]),
                    "idx": i,
                    "span": consumed,
                })
                last_role = "entity"
                last_entity_domains = {h.domain for h in entity_hits}
                last_was_target_work = _hits_include_target_work(entity_hits)
                i += consumed
                continue

            # 4. Multi-word fuzzy match BEFORE triggers and single-token lookup
            #    (catches "משנה ברור", "פני יהושוע", "שולחן ארוך", "קצוות החושן", "אורח חים")
            multi_fuzzy = self._fuzzy_match_multi(tokens, i)
            if multi_fuzzy is not None:
                fuzzy_hits, consumed = multi_fuzzy
                result.append({
                    "type": "entity",
                    "hit": fuzzy_hits[0],
                    "alternatives": fuzzy_hits[1:],
                    "token": " ".join(tokens[i : i + consumed]),
                    "idx": i,
                    "span": consumed,
                })
                last_role = "entity"
                last_entity_domains = {h.domain for h in fuzzy_hits}
                last_was_target_work = _hits_include_target_work(fuzzy_hits)
                i += consumed
                continue

            # 5. Shorthand numeric tail BEFORE single-word entity lookup when preceded by a
            #    target work or number (prevents 2-letter acronyms/parashot like טז/כח/מב/עז/יד/נח/צו
            #    from hijacking shorthand daf/siman numbers, while preserving e.g. "שך יוד סימן י")
            tok_normalized = normalize_key(tok)
            if last_was_target_work:
                # If a primary number-introducing structural word immediately follows an exact entity
                # (e.g. "ברכות רן דף ב"), let Step 6 match it as an entity instead of a number.
                next_introduces_num = (
                    i + 1 < n_tokens and tokens[i + 1] in ("דף", "סימן", "פרק")
                )
                exact_tok_hits = self._lookup_all_exact(tok)
                is_section_after_nk = (
                    DOMAIN_NOSE_KELI in last_entity_domains
                    and not (last_entity_domains & _STRICT_NUMBER_TAIL_DOMAINS)
                    and any(h.domain == DOMAIN_HALACHA_SECTION for h in exact_tok_hits)
                )
                if not is_section_after_nk and not (next_introduces_num and exact_tok_hits):
                    has_struct_ahead = any(t in _STRUCTURAL for t in tokens[i + 1 :])
                    spk = parse_spoken_number(tokens, i, allow_spaced_gematria=has_struct_ahead)
                    if spk is not None and spk[1] > i:
                        val, next_i = spk
                        result.append({
                            "type": "number", "value": val, "token": tok,
                            "idx": i, "span": next_i - i,
                        })
                        last_role = "number"
                        last_was_target_work = True
                        last_entity_domains = set()
                        i = next_i
                        continue
                    if len(tok_normalized) <= 3 and is_valid_gematria_order(tok):
                        num = parse_hebrew_number(tok)
                        if num is not None:
                            result.append({"type": "number", "value": num, "token": tok, "idx": i})
                            last_role = "number"
                            last_was_target_work = True
                            last_entity_domains = set()
                            i += 1
                            continue

            # 6. Exact single-word entity match (span 1)
            single_hits = self._lookup_all_exact(tok)
            if single_hits:
                result.append({
                    "type": "entity",
                    "hit": single_hits[0],
                    "alternatives": single_hits[1:],
                    "token": tok,
                    "idx": i,
                    "span": 1,
                })
                last_role = "entity"
                last_entity_domains = {h.domain for h in single_hits}
                last_was_target_work = _hits_include_target_work(single_hits)
                i += 1
                continue

            # 7. Trigger words (only if not matched as entity)
            found_trigger = False
            for trigger_set, trigger_name in (
                (_TRIGGER_RAMBAM, "rambam"),
                (_TRIGGER_RIF, "rif"),
                (_TRIGGER_ROSH, "rosh"),
            ):
                if tok in trigger_set or tok_normalized in trigger_set:
                    result.append({"type": "trigger", "trigger": trigger_name, "token": tok, "idx": i})
                    last_role = "trigger"
                    last_was_target_work = False
                    last_entity_domains = set()
                    i += 1
                    found_trigger = True
                    break
            if found_trigger:
                continue

            # 8. Single-token fuzzy match
            fuzzy_single = self._fuzzy_match_token(tok)
            if fuzzy_single is not None:
                result.append({
                    "type": "entity",
                    "hit": fuzzy_single[0],
                    "alternatives": fuzzy_single[1:],
                    "token": tok,
                    "idx": i,
                    "span": 1,
                })
                last_role = "entity"
                last_entity_domains = {h.domain for h in fuzzy_single}
                last_was_target_work = _hits_include_target_work(fuzzy_single)
                i += 1
                continue

            # 9. Unknown token
            result.append({"type": "unknown", "token": tok, "idx": i})
            last_role = "unknown"
            last_was_target_work = False
            last_entity_domains = set()
            i += 1

        return result

    # ------------------------------------------------------------------
    # Entity matching (two-tier, C++-accelerated)
    # ------------------------------------------------------------------

    def _lookup_exact(self, phrase: str) -> EntityHit | None:
        """Tier-1: O(1) exact lookup on normalized key. Returns first match."""
        hits = self._lookup_all_exact(phrase)
        return hits[0] if hits else None

    def _lookup_all_exact(self, phrase: str) -> list[EntityHit]:
        """Tier-1: get ALL exact matches for a phrase (multi-domain), including prefix variants."""
        key = normalize_key(phrase)
        if not key:
            return []
        results = self.index.lookup_exact_key(key)
        if results:
            return results

        # Try prefix-stripped variants — collect BOTH exact_map AND _alt_map!
        for variant in strip_prefix_variants(phrase)[1:]:
            vk = normalize_key(variant)
            if vk:
                v_results = self.index.lookup_exact_key(vk)
                if v_results:
                    return v_results
        return []

    def _fuzzy_match_multi(
        self, tokens: list[str], i: int,
    ) -> tuple[list[EntityHit], int] | None:
        """Tier-2 multi-word fuzzy match for multi-word phrases (up to catalog max_word_count)."""
        n_tokens = len(tokens)
        upper_span = min(self.index.max_word_count, n_tokens - i)
        for span in range(upper_span, 1, -1):
            if span not in self.index._flat_raw_keys:
                continue
            window = tokens[i : i + span]

            # Never span across structural markers, Arabic numerals, or hard triggers
            if any(
                w in _STRUCTURAL
                or w.isdigit()
                or normalize_key(w) in _HARD_TRIGGERS
                for w in window
            ):
                continue

            # Don't merge when EVERY token is already an exact entity on its own (e.g. "רשי ברכות")
            exact_per_tok = [bool(self._lookup_all_exact(w)) for w in window]
            if all(exact_per_tok):
                continue

            # Don't merge a target work followed by a valid number/gematria token (e.g. "שבת קנח")
            if exact_per_tok[0]:
                first_hits = self._lookup_all_exact(window[0])
                if _hits_include_target_work(first_hits):
                    second = window[1]
                    if (
                        parse_spoken_number(window, 1, allow_spaced_gematria=False) is not None
                        or (len(normalize_key(second)) <= 3 and parse_hebrew_number(second) is not None)
                    ):
                        continue

            phrase = " ".join(window)
            key = normalize_key(phrase)
            if not key:
                continue
            phon_key = normalize_phonetic(phrase)
            matched = self.index.fuzzy_match(
                key, phon_key, span, _THRESH_MULTI, use_wratio=False,
            )
            if matched is not None:
                _mkey, best_score, hits = matched
                scored = [
                    EntityHit(
                        entity_id=h.entity_id,
                        canonical_name=h.canonical_name,
                        domain=h.domain,
                        score=best_score,
                        matched_surface=phrase,
                    )
                    for h in hits
                ]
                return scored, span
        return None

    def _fuzzy_match_token(self, token: str) -> list[EntityHit] | None:
        """Tier-2 single-token fuzzy match via C++-accelerated rapidfuzz."""
        key = normalize_key(token)
        if not key:
            return None

        exact = self.index.lookup_exact_key(key)
        if exact:
            return exact

        phon_key = normalize_phonetic(token)
        threshold = _fuzzy_threshold(len(key))
        # Use pure Levenshtein ratio (not WRatio/partial_ratio) so 2-letter aliases
        # like "פת"/"בי"/"אח" never falsely match unrelated 4-6 letter words ("נפתח" vs "פת").
        matched = self.index.fuzzy_match(key, phon_key, 1, threshold, use_wratio=False)
        if matched is None:
            return None

        _mkey, best_score, hits = matched
        return [
            EntityHit(
                entity_id=h.entity_id,
                canonical_name=h.canonical_name,
                domain=h.domain,
                score=best_score,
                matched_surface=token,
            )
            for h in hits
        ]

    # ------------------------------------------------------------------
    # Routing (strict anchor-first order)
    # ------------------------------------------------------------------

    def _route_schema(
        self, classified: list[dict], cleaned: CleanedCommand,
    ) -> SchemaType | None:
        """Deterministic anchor-first decision table for schema routing."""
        entities = [c for c in classified if c["type"] == "entity"]
        structurals = {c["role"] for c in classified if c["type"] == "structural"}
        triggers = {c["trigger"] for c in classified if c["type"] == "trigger"}

        all_domains: set[str] = set()
        all_entity_ids: set[str] = set()
        for e in entities:
            all_domains.add(e["hit"].domain)
            all_entity_ids.add(e["hit"].entity_id)
            for alt in e.get("alternatives", []):
                all_domains.add(alt.domain)
                all_entity_ids.add(alt.entity_id)

        has_tractate = DOMAIN_TRACTATE in all_domains
        has_hilchah = DOMAIN_HILCHAH in all_domains
        has_tanakh_book = DOMAIN_TANAKH_BOOK in all_domains
        has_nk = DOMAIN_NOSE_KELI in all_domains
        has_base_work = DOMAIN_BASE_WORK in all_domains
        has_halacha_section = DOMAIN_HALACHA_SECTION in all_domains
        has_rosh = "rosh" in triggers or "rosh" in all_entity_ids
        has_rif = "rif" in triggers or "rif" in all_entity_ids
        has_numeric_tail = self._has_numeric_tail(classified)

        # 1. פרשת → PARASHA
        if "parasha" in structurals:
            return SchemaType.PARASHA

        # 2. רא"ש + (סימן | פרק | tractate) → ROSH_PEREK_SIMAN
        if has_rosh and ("siman" in structurals or "perek" in structurals or has_tractate):
            return SchemaType.ROSH_PEREK_SIMAN

        # 3. רי"ף + (דף | עמוד | numeric tail) → RIF_DAF
        if has_rif and ("daf" in structurals or "amud" in structurals or has_numeric_tail):
            return SchemaType.RIF_DAF

        # 4. דף / עמוד → BAVLI_DAF (NEVER Tanakh or Rambam!)
        if "daf" in structurals or "amud" in structurals:
            return SchemaType.BAVLI_DAF

        # 5. רמב"ם trigger or הלכה structural → RAMBAM_HALACHA
        if "rambam" in triggers or "halacha" in structurals:
            return SchemaType.RAMBAM_HALACHA

        # 6. סימן / סעיף / סעיף קטן → HALACHA_SIMAN
        if "siman" in structurals or "seif" in structurals or "seif_katan" in structurals:
            return SchemaType.HALACHA_SIMAN

        # 7. פרק / פסוק (without Rambam trigger)
        if "perek" in structurals or "pasuk" in structurals:
            if has_tanakh_book:
                return SchemaType.TANAKH_PEREK_PASUK
            if has_hilchah:
                return SchemaType.RAMBAM_HALACHA
            return None

        # 8. Shorthand numeric tail (no structural markers)
        if has_numeric_tail:
            if has_tractate:
                return SchemaType.BAVLI_DAF
            if has_tanakh_book:
                return SchemaType.TANAKH_PEREK_PASUK
            if has_nk or has_base_work or has_halacha_section:
                return SchemaType.HALACHA_SIMAN
            if has_hilchah:
                return SchemaType.RAMBAM_HALACHA

        # Never route solely on a shared commentary without a target book or structural anchor
        return None

    def _has_numeric_tail(self, classified: list[dict]) -> bool:
        """Check if there are number tokens after the last entity."""
        last_entity_idx = -1
        for i, c in enumerate(classified):
            if c["type"] == "entity":
                last_entity_idx = i
        return any(c["type"] == "number" for c in classified[last_entity_idx + 1 :])

    # ------------------------------------------------------------------
    # Slot filling
    # ------------------------------------------------------------------

    def _fill_slots(
        self, schema: SchemaType, classified: list[dict],
    ) -> tuple[dict, float, bool]:
        """Fill parsed_fields for the routed schema. Returns (fields, confidence, inferred)."""
        entities = [c for c in classified if c["type"] == "entity"]
        numbers = [c for c in classified if c["type"] == "number"]
        structurals = [c for c in classified if c["type"] == "structural"]

        fields: dict = {}
        confidences: list[float] = []
        inferred = False

        if schema == SchemaType.BAVLI_DAF:
            fields, confidences, inferred = self._fill_bavli_daf(entities, numbers, structurals)
        elif schema == SchemaType.RIF_DAF:
            fields, confidences, inferred = self._fill_rif_daf(entities, numbers, structurals)
        elif schema == SchemaType.RAMBAM_HALACHA:
            fields, confidences, inferred = self._fill_rambam_halacha(entities, numbers, structurals)
        elif schema == SchemaType.HALACHA_SIMAN:
            fields, confidences, inferred = self._fill_halacha_siman(entities, numbers, structurals)
        elif schema == SchemaType.TANAKH_PEREK_PASUK:
            fields, confidences, inferred = self._fill_tanakh(entities, numbers, structurals)
        elif schema == SchemaType.PARASHA:
            fields, confidences, inferred = self._fill_parasha(entities, numbers, structurals)
        elif schema == SchemaType.ROSH_PEREK_SIMAN:
            fields, confidences, inferred = self._fill_rosh_perek_siman(entities, numbers, structurals)

        confidence = min(confidences) if confidences else 50.0
        return fields, confidence, inferred

    def _fill_bavli_daf(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        tractate = self._pick_entity(entities, DOMAIN_TRACTATE)
        commentary = self._pick_entity(entities, DOMAIN_BAVLI_COMMENTARY)

        if tractate:
            fields["tractate"] = tractate.entity_id
            confidences.append(tractate.score)
        if commentary:
            fields["commentary"] = commentary.entity_id
            fields["commentary_name"] = commentary.canonical_name
            confidences.append(commentary.score)

        daf, amud = self._extract_daf_amud(numbers, structurals)
        if daf is not None:
            fields["daf"] = daf
        if amud is not None:
            fields["amud"] = amud

        return fields, confidences, False

    def _fill_rif_daf(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        tractate = self._pick_entity(entities, DOMAIN_TRACTATE)
        if tractate:
            fields["tractate"] = tractate.entity_id
            confidences.append(tractate.score)

        daf, amud = self._extract_daf_amud(numbers, structurals)
        if daf is not None:
            fields["daf"] = daf
        if amud is not None:
            fields["amud"] = amud

        return fields, confidences, False

    def _fill_rambam_halacha(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        hilchah = self._pick_entity(entities, DOMAIN_HILCHAH)
        if hilchah:
            fields["hilchah"] = hilchah.entity_id
            confidences.append(hilchah.score)

        struct_roles = {s["role"] for s in structurals}
        nums = [n["value"] for n in numbers]

        if "perek" in struct_roles:
            perek = self._number_after_structural(numbers, structurals, "perek")
            if perek is not None:
                fields["perek"] = perek
        if "halacha" in struct_roles:
            halacha = self._number_after_structural(numbers, structurals, "halacha")
            if halacha is not None:
                fields["halacha"] = halacha

        # Shorthand when structural markers were omitted
        if "perek" not in struct_roles and "halacha" not in struct_roles:
            if len(nums) >= 2:
                fields["perek"] = nums[0]
                fields["halacha"] = nums[1]
            elif len(nums) == 1:
                fields["perek"] = nums[0]
        elif "perek" in fields and "halacha" not in struct_roles and len(nums) >= 2:
            fields["halacha"] = nums[1]

        return fields, confidences, False

    def _fill_halacha_siman(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        nk = self._pick_entity(entities, DOMAIN_NOSE_KELI)
        bw = self._pick_entity(entities, DOMAIN_BASE_WORK)
        section = self._pick_entity(entities, DOMAIN_HALACHA_SECTION)

        if nk:
            fields["nosei_keli"] = nk.entity_id
            confidences.append(nk.score)
        if bw:
            fields["base_work"] = bw.entity_id
            confidences.append(bw.score)
        if section:
            fields["section"] = section.entity_id
            confidences.append(section.score)

        struct_roles = {s["role"] for s in structurals}
        unclaimed = self._unclaimed_numbers(numbers, structurals)

        # Siman
        siman = self._number_after_structural(numbers, structurals, "siman")
        if siman is None and "siman" not in struct_roles and unclaimed:
            siman = unclaimed[0]["value"]
        if siman is not None:
            fields["siman"] = siman

        # Distinct seif and seif_katan extraction
        seif = self._number_after_structural(numbers, structurals, "seif")
        if seif is not None:
            fields["seif"] = seif

        seif_katan = self._number_after_structural(numbers, structurals, "seif_katan")
        if seif_katan is not None:
            fields["seif_katan"] = seif_katan

        # Shorthand second number (only if neither seif nor seif_katan was explicitly given)
        if (
            "seif" not in fields
            and "seif_katan" not in fields
            and "seif" not in struct_roles
            and "seif_katan" not in struct_roles
            and len(unclaimed) >= 2
        ):
            val = unclaimed[1]["value"]
            if nk and nk.entity_id in self.index.nosei_kelim:
                nk_obj = self.index.nosei_kelim[nk.entity_id]
                fields[nk_obj.sub_unit] = val
            else:
                fields["seif"] = val

        return fields, confidences, False

    def _fill_tanakh(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        book = self._pick_entity(entities, DOMAIN_TANAKH_BOOK)
        commentary = self._pick_entity(entities, DOMAIN_TANAKH_COMMENTARY)

        if book:
            fields["book"] = book.entity_id
            if book.entity_id == "esther" and book.score >= 99.9 and book.matched_surface == "מגילה":
                fields["book_name"] = "מגילה"
            confidences.append(book.score)
        if commentary:
            fields["commentary"] = commentary.entity_id
            fields["commentary_name"] = commentary.canonical_name
            confidences.append(commentary.score)

        struct_roles = {s["role"] for s in structurals}
        perek = self._number_after_structural(numbers, structurals, "perek")
        pasuk = self._number_after_structural(numbers, structurals, "pasuk")

        if perek is not None:
            fields["perek"] = perek
        if pasuk is not None:
            fields["pasuk"] = pasuk

        # Shorthand when structural markers were omitted (never steal a pasuk number for perek)
        unclaimed = self._unclaimed_numbers(numbers, structurals)
        if "perek" not in struct_roles and "perek" not in fields and unclaimed:
            fields["perek"] = unclaimed[0]["value"]
            if len(unclaimed) >= 2 and "pasuk" not in fields and "pasuk" not in struct_roles:
                fields["pasuk"] = unclaimed[1]["value"]

        return fields, confidences, False

    def _fill_parasha(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        parasha = self._pick_entity(entities, DOMAIN_PARASHA)
        commentary = self._pick_entity(entities, DOMAIN_TANAKH_COMMENTARY)

        if parasha:
            fields["parasha"] = parasha.entity_id
            confidences.append(parasha.score)
        if commentary:
            fields["commentary"] = commentary.entity_id
            fields["commentary_name"] = commentary.canonical_name
            confidences.append(commentary.score)

        perek = self._number_after_structural(numbers, structurals, "perek")
        if perek is not None:
            fields["perek"] = perek

        pasuk = self._number_after_structural(numbers, structurals, "pasuk")
        if pasuk is not None:
            fields["pasuk"] = pasuk

        return fields, confidences, False

    def _fill_rosh_perek_siman(self, entities, numbers, structurals):
        fields: dict = {}
        confidences: list[float] = []

        tractate = self._pick_entity(entities, DOMAIN_TRACTATE)
        if tractate:
            fields["tractate"] = tractate.entity_id
            confidences.append(tractate.score)

        struct_roles = {s["role"] for s in structurals}
        perek = self._number_after_structural(numbers, structurals, "perek")
        siman = self._number_after_structural(numbers, structurals, "siman")

        if perek is not None:
            fields["perek"] = perek
        if siman is not None:
            fields["siman"] = siman

        # Shorthand: only when markers were omitted (never copy perek into siman!)
        if "perek" not in struct_roles and "siman" not in struct_roles:
            nums = [n["value"] for n in numbers]
            if len(nums) >= 2:
                fields["perek"] = nums[0]
                fields["siman"] = nums[1]
            elif len(nums) == 1:
                fields["perek"] = nums[0]

        return fields, confidences, False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_entity(self, entities: list[dict], domain: str) -> EntityHit | None:
        """Pick highest-scoring entity matching `domain` (exact matches beat fuzzy guesses)."""
        best: EntityHit | None = None
        for e in entities:
            if e["hit"].domain == domain:
                if best is None or e["hit"].score > best.score:
                    best = e["hit"]
            for alt in e.get("alternatives", []):
                if alt.domain == domain:
                    if best is None or alt.score > best.score:
                        best = alt
        return best

    def _number_after_structural(
        self, numbers: list[dict], structurals: list[dict], role: str,
    ) -> int | None:
        """Find the first number after a structural marker of `role`, before the next marker."""
        struct_idx: int | None = None
        next_struct_idx: int | None = None
        for s in structurals:
            if struct_idx is None:
                if s["role"] == role:
                    struct_idx = s["idx"]
            elif s["idx"] > struct_idx:
                next_struct_idx = s["idx"]
                break
        if struct_idx is None:
            return None

        for n in numbers:
            if n["idx"] > struct_idx:
                if next_struct_idx is not None and n["idx"] >= next_struct_idx:
                    return None
                return n["value"]
        return None

    def _unclaimed_numbers(
        self, numbers: list[dict], structurals: list[dict],
    ) -> list[dict]:
        """Return number tokens not directly claimed by any structural marker."""
        claimed_indices: set[int] = set()
        for idx_pos, s in enumerate(structurals):
            s_idx = s["idx"]
            next_s_idx = (
                structurals[idx_pos + 1]["idx"]
                if idx_pos + 1 < len(structurals)
                else None
            )
            for n in numbers:
                if n["idx"] > s_idx:
                    if next_s_idx is not None and n["idx"] >= next_s_idx:
                        break
                    claimed_indices.add(n["idx"])
                    break
        return [n for n in numbers if n["idx"] not in claimed_indices]

    def _extract_daf_amud(
        self, numbers: list[dict], structurals: list[dict],
    ) -> tuple[int | None, str | None]:
        """Extract daf number and amud letter from classified tokens."""
        struct_roles = {s["role"] for s in structurals}
        unclaimed = self._unclaimed_numbers(numbers, structurals)

        daf = self._number_after_structural(numbers, structurals, "daf")
        if daf is None and "daf" not in struct_roles and unclaimed:
            daf = unclaimed[0]["value"]

        amud = None
        amud_num = self._number_after_structural(numbers, structurals, "amud")
        if amud_num is not None:
            amud = "א" if amud_num == 1 else "ב" if amud_num == 2 else str(amud_num)
        elif "amud" not in struct_roles and len(unclaimed) >= 2:
            amud_val = unclaimed[1]["value"]
            amud = "א" if amud_val == 1 else "ב" if amud_val == 2 else str(amud_val)

        return daf, amud

    def _build_top_candidates(
        self,
        schema: SchemaType,
        outcome: ValidationOutcome,
        fields: dict,
        confidence: float,
    ) -> list[Candidate]:
        """Build deduplicated top_candidates list for ambiguous/low-confidence results."""
        candidates: list[Candidate] = []
        seen_ids: set[str] = set()

        if outcome.status == MatchStatus.AMBIGUOUS_SECTION:
            cand_labels = outcome.normalized_fields.get("candidates", [])
            for label in cand_labels:
                for sec in self.index.halacha_sections.values():
                    if sec.canonical_name == label and sec.id not in seen_ids:
                        seen_ids.add(sec.id)
                        candidates.append(Candidate(
                            label=sec.canonical_name,
                            entity_id=sec.id,
                            score=confidence,
                            schema_type=schema,
                            parsed_fields={**fields, "section": sec.id},
                        ))
                        break
            return candidates

        if schema == SchemaType.HALACHA_SIMAN:
            for sec in self.index.halacha_sections.values():
                if sec.id not in seen_ids:
                    seen_ids.add(sec.id)
                    candidates.append(Candidate(
                        label=sec.canonical_name,
                        entity_id=sec.id,
                        score=confidence,
                        schema_type=schema,
                        parsed_fields=fields,
                    ))
                    if len(candidates) >= 3:
                        break
        return candidates[:3]
