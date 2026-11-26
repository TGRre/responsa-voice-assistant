"""UI formatting: canonical commands and TTS feedback in Hebrew."""

from __future__ import annotations

import re

from src.hebrew_utils import int_to_gematria
from src.models import MatchStatus, SchemaType

_QUOTE_CHARS_RE = re.compile(r"[\"״'׳\u2019]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def _sanitize_payload(text: str) -> str:
    """Guarantee zero gershayim/geresh/apostrophes in Responsa search payloads."""
    cleaned = _QUOTE_CHARS_RE.sub("", text)
    return _MULTI_SPACE_RE.sub(" ", cleaned).strip()


class ResponsaFormatter:
    """Pure UI formatting — no NLP. Generates canonical commands and TTS lines."""

    def __init__(self, preset: str = "default") -> None:
        if preset not in ("default", "bar_ilan_short"):
            raise ValueError(f"Unknown preset: {preset}")
        self.preset = preset

    def format_command(self, schema_type: SchemaType | None, fields: dict) -> str:
        """Generate a clean, gershayim-free canonical Hebrew command string."""
        if schema_type is None:
            return ""

        raw = ""
        if schema_type == SchemaType.BAVLI_DAF:
            raw = self._fmt_bavli_daf(fields)
        elif schema_type == SchemaType.ROSH_PEREK_SIMAN:
            raw = self._fmt_rosh_perek_siman(fields)
        elif schema_type == SchemaType.RIF_DAF:
            raw = self._fmt_rif_daf(fields)
        elif schema_type == SchemaType.RAMBAM_HALACHA:
            raw = self._fmt_rambam_halacha(fields)
        elif schema_type == SchemaType.HALACHA_SIMAN:
            raw = self._fmt_halacha_siman(fields)
        elif schema_type == SchemaType.TANAKH_PEREK_PASUK:
            raw = self._fmt_tanakh_perek_pasuk(fields)
        elif schema_type == SchemaType.PARASHA:
            raw = self._fmt_parasha(fields)

        return _sanitize_payload(raw)

    def format_tts(
        self, status: MatchStatus, schema_type: SchemaType | None,
        fields: dict, detail: str = "",
    ) -> str:
        """Generate natural Hebrew TTS feedback from status + validated bounds."""
        if status == MatchStatus.OK:
            cmd = self.format_command(schema_type, fields)
            return f"פותח {cmd}" if cmd else ""

        if status == MatchStatus.INVALID_RANGE:
            return self._tts_invalid_range(schema_type, fields, detail)

        if status == MatchStatus.INVALID_COMBINATION:
            if fields.get("rif_available") is False:
                name = fields.get("tractate_name", "")
                return f"אין ריף על מסכת {name}" if name else "אין ריף על מסכת זו"
            work = fields.get("work_name") or fields.get("nosei_keli_name")
            sec = fields.get("section_name")
            if work and sec:
                return f"{work} לא חל על {sec}"
            comm = fields.get("commentary_name")
            book = fields.get("book_name") or fields.get("parasha_name")
            if comm and book:
                return f"{comm} לא מפרש על {book}"
            return detail or "השילוב אינו תקין"

        if status == MatchStatus.AMBIGUOUS_SECTION:
            candidates = fields.get("candidates", [])
            if candidates:
                opts = " או ".join(f"ב{c}" for c in candidates)
                return f"האם התכוונת ל{opts}?"
            return "לאיזה חלק התכוונת?"

        # NEEDS_FALLBACK
        return detail or "לא הצלחתי להבין, נסה שוב"

    # ------------------------------------------------------------------
    # Canonical command formatting
    # ------------------------------------------------------------------

    def _fmt_bavli_daf(self, f: dict) -> str:
        name = f.get("tractate_name", f.get("tractate", ""))
        daf = f.get("daf", "")
        amud = f.get("amud", "א")
        commentary = f.get("commentary_name", "")
        daf_str = self._gem(daf) if isinstance(daf, int) else str(daf)
        base = f"{name} דף {daf_str} עמוד {amud}"
        if commentary:
            return f"{commentary} {base}"
        return base

    def _fmt_rosh_perek_siman(self, f: dict) -> str:
        name = f.get("tractate_name", f.get("tractate", ""))
        perek = self._gem(f.get("perek", 0))
        siman = f.get("siman")
        if siman is not None:
            return f"ראש {name} פרק {perek} סימן {self._gem(siman)}"
        return f"ראש {name} פרק {perek}"

    def _fmt_rif_daf(self, f: dict) -> str:
        name = f.get("tractate_name", f.get("tractate", ""))
        daf = self._gem(f.get("daf", 0))
        amud = f.get("amud", "א")
        return f"ריף {name} דף {daf} עמוד {amud}"

    def _fmt_rambam_halacha(self, f: dict) -> str:
        name = f.get("hilchah_name", f.get("hilchah", ""))
        perek = self._gem(f.get("perek", 0))
        halacha = f.get("halacha")
        if halacha is not None:
            return f"רמבם {name} פרק {perek} הלכה {self._gem(halacha)}"
        return f"רמבם {name} פרק {perek}"

    def _fmt_halacha_siman(self, f: dict) -> str:
        name = f.get(
            "nosei_keli_name",
            f.get("base_work_name", f.get("nosei_keli", f.get("base_work", ""))),
        )
        section = f.get("section_name", "")
        siman = self._gem(f.get("siman", 0))

        if self.preset == "bar_ilan_short":
            sec_display = {
                "אורח חיים": "אוח", "יורה דעה": "יוד",
                "אבן העזר": "אהע", "חושן משפט": "חומ",
            }.get(section, section)
        else:
            sec_display = section

        result = f"{name} {sec_display} סימן {siman}"

        seif = f.get("seif")
        seif_katan = f.get("seif_katan")
        if seif is not None:
            result += f" סעיף {self._gem(seif)}"
        if seif_katan is not None:
            result += f" סק {self._gem(seif_katan)}"
        return result

    def _fmt_tanakh_perek_pasuk(self, f: dict) -> str:
        name = f.get("book_name", f.get("book", ""))
        perek = self._gem(f.get("perek", 0))
        pasuk = f.get("pasuk")
        commentary = f.get("commentary_name", "")

        if pasuk is not None:
            base = f"{name} פרק {perek} פסוק {self._gem(pasuk)}"
        else:
            base = f"{name} פרק {perek}"

        if commentary:
            return f"{commentary} {base}"
        return base

    def _fmt_parasha(self, f: dict) -> str:
        name = f.get("parasha_name", f.get("parasha", ""))
        commentary = f.get("commentary_name", "")
        perek = f.get("perek")
        pasuk = f.get("pasuk")

        if perek is not None:
            book = f.get("book", "")
            base = f"{book} פרק {self._gem(perek)}"
            if pasuk is not None:
                base += f" פסוק {self._gem(pasuk)}"
            return f"{commentary} {base}" if commentary else base

        if commentary:
            return f"{commentary} פרשת {name}"
        return f"פרשת {name}"

    # ------------------------------------------------------------------
    # TTS feedback
    # ------------------------------------------------------------------

    def _tts_invalid_range(
        self, schema_type: SchemaType | None, f: dict, detail: str,
    ) -> str:
        if f.get("invalid_amud"):
            return "עמוד חייב להיות א או ב"
        if schema_type == SchemaType.BAVLI_DAF:
            name = f.get("tractate_name", "")
            max_daf = f.get("max_daf")
            if max_daf:
                return f"במסכת {name} יש רק עד דף {self._gem(max_daf)}"
        if schema_type == SchemaType.ROSH_PEREK_SIMAN:
            name = f.get("tractate_name", "")
            max_perek = f.get("max_perek")
            if max_perek:
                return f"בראש {name} יש רק עד פרק {self._gem(max_perek)}"
        if schema_type == SchemaType.RIF_DAF:
            name = f.get("tractate_name", "")
            max_daf = f.get("rif_max_daf")
            if max_daf:
                return f"בריף {name} יש רק עד דף {self._gem(max_daf)}"
        if schema_type == SchemaType.RAMBAM_HALACHA:
            name = f.get("hilchah_name", "")
            max_perek = f.get("max_perek")
            if max_perek:
                return f"ב{name} יש רק עד פרק {self._gem(max_perek)}"
        if schema_type == SchemaType.HALACHA_SIMAN:
            name = f.get("section_name", "")
            max_siman = f.get("max_siman")
            if max_siman:
                return f"ב{name} יש רק עד סימן {self._gem(max_siman)}"
        if schema_type == SchemaType.TANAKH_PEREK_PASUK:
            name = f.get("book_name", "")
            max_perek = f.get("max_perek")
            if max_perek:
                return f"ב{name} יש רק עד פרק {self._gem(max_perek)}"
        if schema_type == SchemaType.PARASHA:
            name = f.get("parasha_name", "")
            start = f.get("start_perek")
            end = f.get("end_perek")
            if start is not None and end is not None:
                return f"בפרשת {name} יש מפרק {self._gem(start)} עד פרק {self._gem(end)}"
        return detail or "המספר מחוץ לטווח"

    @staticmethod
    def _gem(n: int) -> str:
        try:
            return int_to_gematria(n)
        except (ValueError, TypeError):
            return str(n)
