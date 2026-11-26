"""Tests for engine_bridge, whisper_config, formatter safety, and LLMFallbackResolver."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from src.fallback_llm import GeminiProvider, LLMFallbackResolver
from src.formatter import ResponsaFormatter
from src.models import MatchStatus, SchemaType
from src.schema_loader import load_schema_index
from src.whisper_config import generate_whisper_prompt

# Make responsa_voice importable for engine_bridge tests
_VOICE_DIR = Path(__file__).resolve().parent.parent.parent / "responsa_voice"
if str(_VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_DIR))

from engine_bridge import get_whisper_prompt, resolve_with_engine, warm_up  # noqa: E402
from config import get_whisper_initial_prompt  # noqa: E402


@pytest.fixture(scope="module")
def index():
    return load_schema_index()


# ---------------------------------------------------------------------------
# engine_bridge tests
# ---------------------------------------------------------------------------


class TestEngineBridge:
    def test_warm_up(self):
        warm_up()

    def test_simple_command_with_comma_and_period(self):
        r = resolve_with_engine("הבא, בבקשה.")
        assert r.command == "next"
        assert r.status == "OK"
        assert r.book_ok is True

    def test_valid_reference_open(self):
        r = resolve_with_engine("פתח חולין דף קט עמוד ב.")
        assert r.command == "open"
        assert r.payload == "חולין דף קט עמוד ב"
        assert r.status == "OK"
        assert r.book_ok is True
        assert r.tts_feedback

    def test_free_text_search(self):
        cases = [
            ("חפש דינא דמלכותא", "דינא דמלכותא"),
            ("מצא דינא דמלכותא", "דינא דמלכותא"),
            ("תמצא לי דינא דמלכותא", "דינא דמלכותא"),
            ("נמצא מלמד", "מלמד"),
            ("תמצא לי את המילה מלמד.", "מלמד"),
            ("חפש על מנת", "על מנת"),
            ("חפש ש\"ך סימן י", "שך סימן י"),
        ]
        for q, expected in cases:
            r = resolve_with_engine(q)
            assert r.command == "find", f"Failed for {q!r}: {r}"
            assert r.payload == expected, f"Wrong payload for {q!r}: {r.payload!r}"
            assert r.source == "engine"
            assert r.status == "OK"
            assert r.book_ok is True

    def test_config_whisper_prompt_is_compact(self):
        p_bridge = get_whisper_prompt()
        p_config = get_whisper_initial_prompt()
        assert p_bridge == p_config
        assert 0 < len(p_config) <= 240

    def test_invalid_range_blocked_with_tts(self):
        r = resolve_with_engine("פתח שבת דף קנח עמוד א")
        assert r.command is None
        assert r.payload == ""
        assert r.book_ok is False
        assert r.status == "INVALID_RANGE"
        assert "עד" in r.tts_feedback

    def test_ambiguous_section_blocked_with_tts(self):
        r = resolve_with_engine("פתח שך סימן י")
        assert r.command is None
        assert r.payload == ""
        assert r.book_ok is False
        assert r.status == "AMBIGUOUS_SECTION"
        assert "יורה דעה" in r.tts_feedback and "חושן משפט" in r.tts_feedback

    def test_invalid_combination_blocked(self):
        r = resolve_with_engine("קצות החושן אורח חיים סימן י")
        assert r.command is None
        assert r.payload == ""
        assert r.book_ok is False
        assert r.status == "INVALID_COMBINATION"
        assert r.tts_feedback


# ---------------------------------------------------------------------------
# Formatter quote-stripping invariant
# ---------------------------------------------------------------------------


class TestFormatterInvariants:
    def test_formatter_strips_all_quotes(self):
        fmt = ResponsaFormatter()
        cmd = fmt.format_command(
            SchemaType.BAVLI_DAF,
            {"tractate_name": 'בבא קמ"א', "daf": 30, "amud": "ב׳", "commentary_name": "רש״י"},
        )
        for q in ('"', "״", "'", "׳", "\u2019"):
            assert q not in cmd
        assert cmd == "רשי בבא קמא דף ל עמוד ב"


# ---------------------------------------------------------------------------
# Whisper prompt tests
# ---------------------------------------------------------------------------


class TestWhisperConfig:
    def test_prompt_within_budget_and_clean(self, index):
        prompt = generate_whisper_prompt(index)
        assert prompt
        assert len(prompt) <= 240, f"Whisper prompt too long ({len(prompt)} chars)"
        # Misspelling aliases must NEVER appear in the prompt
        for bad_alias in ("נוח", "בוא", "מצורה", "בבא קמה", "אולין", "פולין"):
            assert bad_alias not in prompt, f"Misspelling alias {bad_alias!r} leaked into prompt"

    def test_prompt_respects_custom_tokenizer_budget(self, index):
        # Fake tokenizer that counts 2 tokens per character -> forces trimming to <= 105 chars
        prompt = generate_whisper_prompt(index, tokenizer=lambda s: [0] * (len(s) * 2))
        assert len(prompt) * 2 <= 210


# ---------------------------------------------------------------------------
# LLMFallbackResolver tests
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, system: str, user: str, timeout: float = 2.5) -> str:
        return self.response


class TestLLMFallbackResolver:
    def test_gemini_default_model_not_deprecated(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        prov = GeminiProvider(api_key="test-key")
        assert prov.model == "gemini-2.5-flash-lite"

    def test_strips_markdown_fences_and_maps_hebrew_names(self, index):
        fenced = '```json\n{"schema_type": "bavli_daf", "parsed_fields": {"tractate": "חולין", "daf": 109, "amud": "ב", "commentary": "רש\\"י"}}\n```'
        resolver = LLMFallbackResolver(_FakeProvider(fenced), index)
        r = resolver.resolve("תביא לי חולין מאה ותשע")
        assert r.status == MatchStatus.OK
        assert r.canonical_command == "רשי חולין דף קט עמוד ב"
        assert r.latency_ms >= 0.0

    def test_fuzzy_maps_minor_llm_spelling_variation(self, index):
        payload = json.dumps({
            "schema_type": "halacha_siman",
            "parsed_fields": {"nosei_keli": "משנה ברור", "siman": 328, "seif_katan": 4},
        })
        resolver = LLMFallbackResolver(_FakeProvider(payload), index)
        r = resolver.resolve("משנה ברור שכח ד")
        assert r.status == MatchStatus.OK
        assert r.canonical_command == "משנה ברורה אורח חיים סימן שכח סק ד"

    def test_validation_failure_returns_hebrew_tts_never_english(self, index):
        payload = json.dumps({
            "schema_type": "bavli_daf",
            "parsed_fields": {"tractate": "שבת", "daf": 200, "amud": "א"},
        })
        resolver = LLMFallbackResolver(_FakeProvider(payload), index)
        r = resolver.resolve("שבת דף מאתיים")
        assert r.status == MatchStatus.INVALID_RANGE
        assert r.canonical_command == ""
        assert "עד" in r.tts_feedback
        # Ensure no raw English validation message leaked
        assert "out of range" not in r.tts_feedback.lower()
