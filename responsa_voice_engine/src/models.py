"""Typed data models for the Hebrew Voice Command Validation & Correction Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SchemaType(StrEnum):
    BAVLI_DAF = "bavli_daf"
    ROSH_PEREK_SIMAN = "rosh_perek_siman"
    RIF_DAF = "rif_daf"
    RAMBAM_HALACHA = "rambam_halacha"
    HALACHA_SIMAN = "halacha_siman"
    TANAKH_PEREK_PASUK = "tanakh_perek_pasuk"
    PARASHA = "parasha"


class MatchStatus(StrEnum):
    OK = "OK"
    NEEDS_FALLBACK = "NEEDS_FALLBACK"
    INVALID_RANGE = "INVALID_RANGE"
    INVALID_COMBINATION = "INVALID_COMBINATION"
    AMBIGUOUS_SECTION = "AMBIGUOUS_SECTION"


class Action(StrEnum):
    OPEN = "פתח"
    SEARCH = "חפש"
    NAVIGATE = "עבור אל"


# ---------------------------------------------------------------------------
# Catalog entities (loaded from data/*.json)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tractate:
    id: str
    canonical_name: str
    max_daf: int
    rif_max_daf: int | None
    max_perek: int
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TalmudCommentary:
    id: str
    canonical_name: str
    category: str  # "rishon" | "acharon"
    ref_type: str  # "daf_amud" | "perek_siman" | "daf_rif"
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Hilchah:
    id: str
    canonical_name: str
    sefer: str
    max_perek: int
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HalachaSection:
    id: str
    canonical_name: str
    max_siman: int
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BaseWork:
    id: str
    canonical_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NoseKeli:
    id: str
    canonical_name: str
    applicable_sections: tuple[str, ...]
    sub_unit: str  # "seif" | "seif_katan"
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TanakhBook:
    id: str
    canonical_name: str
    division: str  # "תורה" | "נביאים" | "כתובים"
    max_perek: int
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TanakhCommentary:
    id: str
    canonical_name: str
    divisions: tuple[str, ...]
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Parasha:
    id: str
    canonical_name: str
    book: str
    start_perek: int
    start_pasuk: int
    end_perek: int
    end_pasuk: int
    aliases: tuple[str, ...]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

#: entity domains used for candidate bucketing and disambiguation
DOMAIN_TRACTATE = "tractate"
DOMAIN_BAVLI_COMMENTARY = "bavli_commentary"
DOMAIN_HILCHAH = "hilchah"
DOMAIN_HALACHA_SECTION = "halacha_section"
DOMAIN_BASE_WORK = "base_work"
DOMAIN_NOSE_KELI = "nosei_keli"
DOMAIN_TANAKH_BOOK = "tanakh_book"
DOMAIN_TANAKH_COMMENTARY = "tanakh_commentary"
DOMAIN_PARASHA = "parasha"


@dataclass(frozen=True, slots=True)
class EntityHit:
    """A resolved entity mention (one surface token -> one entity)."""

    entity_id: str
    canonical_name: str
    domain: str
    score: float
    matched_surface: str


@dataclass(slots=True)
class Candidate:
    label: str
    entity_id: str
    score: float
    schema_type: SchemaType | None = None
    parsed_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationOutcome:
    status: MatchStatus
    errors: tuple[str, ...] = ()
    normalized_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    status: MatchStatus
    schema_type: SchemaType | None
    confidence: float
    parsed_fields: dict[str, Any]
    canonical_command: str
    top_candidates: list[Candidate]
    tts_feedback: str
    latency_ms: float
    action: Action = Action.OPEN


@dataclass(slots=True)
class CleanedCommand:
    action: Action
    text: str
