"""LLM fallback resolver with mandatory local re-validation."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any, Protocol

from src.formatter import ResponsaFormatter
from src.hebrew_utils import (
    normalize_key,
    normalize_phonetic,
    parse_hebrew_number,
    strip_prefix_variants,
)
from src.models import (
    DOMAIN_BAVLI_COMMENTARY,
    DOMAIN_BASE_WORK,
    DOMAIN_HALACHA_SECTION,
    DOMAIN_HILCHAH,
    DOMAIN_NOSE_KELI,
    DOMAIN_PARASHA,
    DOMAIN_TANAKH_BOOK,
    DOMAIN_TANAKH_COMMENTARY,
    DOMAIN_TRACTATE,
    MatchResult,
    MatchStatus,
    SchemaType,
)
from src.schema_loader import SchemaIndex

_SYSTEM_PROMPT = (
    "You are a Hebrew text assistant for the Bar-Ilan Responsa Project. "
    "Given a noisy Hebrew voice command about a Jewish text reference, "
    "extract the structured fields using standard canonical Hebrew names (no gershayim/geresh). "
    "Respond ONLY with valid JSON matching one of these schemas:\n"
    '{"schema_type": "bavli_daf", "parsed_fields": {"tractate": "חולין", "daf": 109, "amud": "א", "commentary": "רשי"}}\n'
    '{"schema_type": "rambam_halacha", "parsed_fields": {"hilchah": "הלכות שבת", "perek": 1, "halacha": 1}}\n'
    '{"schema_type": "halacha_siman", "parsed_fields": {"nosei_keli": "משנה ברורה", "section": "אורח חיים", "siman": 328, "seif": 1, "seif_katan": 4}}\n'
    '{"schema_type": "tanakh_perek_pasuk", "parsed_fields": {"book": "בראשית", "perek": 1, "pasuk": 1, "commentary": "רשי"}}\n'
    '{"schema_type": "parasha", "parsed_fields": {"parasha": "בא", "commentary": "רשי", "perek": 10}}\n'
    '{"schema_type": "rif_daf", "parsed_fields": {"tractate": "שבת", "daf": 20, "amud": "ב"}}\n'
    '{"schema_type": "rosh_perek_siman", "parsed_fields": {"tractate": "ברכות", "perek": 1, "siman": 2}}\n'
    "Omit optional fields when not spoken. "
    'If you cannot parse the command, respond with {"schema_type": null, "parsed_fields": {}}.'
)

_TTS_FAILURE = "לא הצלחתי להבין, נסה שוב"
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_json_fences(raw: str) -> str:
    """Strip optional ```json ... ``` markdown fences from LLM output."""
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text


class LLMProvider(Protocol):
    def complete(self, system: str, user: str, timeout: float) -> str:
        ...


class OpenAICompatProvider:
    """Provider for OpenAI-compatible endpoints (Groq, OpenRouter, etc.)."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str, timeout: float = 2.5) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]


class GeminiProvider:
    """Provider for Google Gemini REST API."""

    def __init__(self, api_key: str, model: str | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def complete(self, system: str, user: str, timeout: float = 2.5) -> str:
        payload = json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 256,
                "responseMimeType": "application/json",
            },
        }).encode("utf-8")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body["candidates"][0]["content"]["parts"][0]["text"]


class LLMFallbackResolver:
    """Resolves NEEDS_FALLBACK queries via LLM with mandatory local re-validation."""

    def __init__(
        self, provider: LLMProvider, index: SchemaIndex, timeout_s: float = 2.5,
    ) -> None:
        self.provider = provider
        self.index = index
        self.timeout_s = timeout_s
        self.formatter = ResponsaFormatter()
        self._domain_maps: dict[str, dict[str, Any]] = {
            DOMAIN_TRACTATE: self.index.tractates,
            DOMAIN_BAVLI_COMMENTARY: self.index.bavli_commentaries,
            DOMAIN_HILCHAH: self.index.hilchot,
            DOMAIN_HALACHA_SECTION: self.index.halacha_sections,
            DOMAIN_BASE_WORK: self.index.base_works,
            DOMAIN_NOSE_KELI: self.index.nosei_kelim,
            DOMAIN_TANAKH_BOOK: self.index.tanakh_books,
            DOMAIN_TANAKH_COMMENTARY: self.index.tanakh_commentaries,
            DOMAIN_PARASHA: self.index.parashot,
        }

    def resolve(self, text: str) -> MatchResult:
        """Attempt LLM resolution. Never returns OK without local re-validation."""
        t0 = time.perf_counter_ns()

        try:
            raw = self.provider.complete(_SYSTEM_PROMPT, text, timeout=self.timeout_s)
        except Exception:
            return self._failure_result(t0)

        try:
            parsed = json.loads(_strip_json_fences(raw))
        except (json.JSONDecodeError, ValueError, TypeError):
            return self._failure_result(t0)

        if not isinstance(parsed, dict):
            return self._failure_result(t0)

        schema_str = parsed.get("schema_type")
        fields = parsed.get("parsed_fields", {})

        if not schema_str or not isinstance(fields, dict) or not fields:
            return self._failure_result(t0)

        try:
            schema_type = SchemaType(schema_str)
        except ValueError:
            return self._failure_result(t0)

        # Map canonical Hebrew names (or IDs) returned by the LLM to internal SchemaIndex IDs
        mapped_fields = self._map_fields_to_ids(schema_type, fields)

        # MANDATORY local re-validation
        outcome = self.index.validate_parsed_fields(schema_type, mapped_fields)
        latency = (time.perf_counter_ns() - t0) / 1e6

        if outcome.status == MatchStatus.OK:
            canonical = self.formatter.format_command(schema_type, outcome.normalized_fields)
            tts = self.formatter.format_tts(outcome.status, schema_type, outcome.normalized_fields)
            return MatchResult(
                status=MatchStatus.OK,
                schema_type=schema_type,
                confidence=60.0,
                parsed_fields=outcome.normalized_fields,
                canonical_command=canonical,
                top_candidates=[],
                tts_feedback=tts,
                latency_ms=latency,
            )

        # LLM produced invalid data — always return natural Hebrew TTS feedback (never raw English)
        tts = self.formatter.format_tts(outcome.status, schema_type, outcome.normalized_fields)
        return MatchResult(
            status=outcome.status,
            schema_type=schema_type,
            confidence=0.0,
            parsed_fields=outcome.normalized_fields,
            canonical_command="",
            top_candidates=[],
            tts_feedback=tts or _TTS_FAILURE,
            latency_ms=latency,
        )

    def _lookup_entity_id(self, value: Any, domains: tuple[str, ...]) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        val = value.strip()

        # Check direct IDs first for backward compatibility
        for dom in domains:
            if val in self._domain_maps[dom]:
                return val

        # Exact lookup on canonical Hebrew name / alias via SchemaIndex
        for variant in strip_prefix_variants(val):
            key = normalize_key(variant)
            if not key:
                continue
            for hit in self.index.lookup_exact_key(key):
                if hit.domain in domains:
                    return hit.entity_id

        # Fuzzy fallback for minor LLM spelling variations (e.g. "באבא קמא", "משנה ברור")
        key = normalize_key(val)
        if key:
            wc = key.count(" ") + 1
            phon_key = normalize_phonetic(val)
            threshold = 82.0 if wc >= 2 else (88.0 if len(key) <= 3 else 78.0)
            matched = self.index.fuzzy_match(
                key, phon_key, wc, threshold, use_wratio=False,
            )
            if matched is not None:
                _mkey, _score, hits = matched
                for hit in hits:
                    if hit.domain in domains:
                        return hit.entity_id
        return val

    @staticmethod
    def _coerce_int(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            parsed = parse_hebrew_number(value)
            if parsed is not None:
                return parsed
        return value

    def _map_fields_to_ids(self, schema_type: SchemaType, fields: dict[str, Any]) -> dict[str, Any]:
        mapped = dict(fields)

        if "tractate" in mapped:
            mapped["tractate"] = self._lookup_entity_id(mapped["tractate"], (DOMAIN_TRACTATE,))
        if "hilchah" in mapped:
            mapped["hilchah"] = self._lookup_entity_id(mapped["hilchah"], (DOMAIN_HILCHAH,))
        if "nosei_keli" in mapped:
            nk_val = mapped["nosei_keli"]
            nk_id = self._lookup_entity_id(nk_val, (DOMAIN_NOSE_KELI, DOMAIN_BASE_WORK))
            mapped["nosei_keli"] = nk_id
        if "base_work" in mapped:
            mapped["base_work"] = self._lookup_entity_id(mapped["base_work"], (DOMAIN_BASE_WORK,))
        if "section" in mapped:
            mapped["section"] = self._lookup_entity_id(mapped["section"], (DOMAIN_HALACHA_SECTION,))
        if "book" in mapped:
            raw_book = mapped["book"]
            book_id = self._lookup_entity_id(raw_book, (DOMAIN_TANAKH_BOOK,))
            mapped["book"] = book_id
            if book_id == "esther" and isinstance(raw_book, str) and normalize_key(raw_book) == "מגילה":
                mapped["book_name"] = "מגילה"
        if "parasha" in mapped:
            mapped["parasha"] = self._lookup_entity_id(mapped["parasha"], (DOMAIN_PARASHA,))
        if "commentary" in mapped:
            if schema_type in (SchemaType.TANAKH_PEREK_PASUK, SchemaType.PARASHA):
                mapped["commentary"] = self._lookup_entity_id(
                    mapped["commentary"], (DOMAIN_TANAKH_COMMENTARY,),
                )
            else:
                mapped["commentary"] = self._lookup_entity_id(
                    mapped["commentary"], (DOMAIN_BAVLI_COMMENTARY,),
                )

        for num_field in ("daf", "perek", "halacha", "siman", "seif", "seif_katan", "pasuk"):
            if num_field in mapped and mapped[num_field] is not None:
                mapped[num_field] = self._coerce_int(mapped[num_field])

        if "amud" in mapped and mapped["amud"] is not None:
            amud_val = mapped["amud"]
            if amud_val in (1, "1", "a", "A", "אלף"):
                mapped["amud"] = "א"
            elif amud_val in (2, "2", "b", "B", "בית"):
                mapped["amud"] = "ב"
            elif isinstance(amud_val, str):
                mapped["amud"] = normalize_key(amud_val)

        return mapped

    def _failure_result(self, t0: int) -> MatchResult:
        latency = (time.perf_counter_ns() - t0) / 1e6
        return MatchResult(
            status=MatchStatus.NEEDS_FALLBACK,
            schema_type=None,
            confidence=0.0,
            parsed_fields={},
            canonical_command="",
            top_candidates=[],
            tts_feedback=_TTS_FAILURE,
            latency_ms=latency,
        )
