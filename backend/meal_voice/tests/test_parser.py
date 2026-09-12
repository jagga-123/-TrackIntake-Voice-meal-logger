"""Unit tests for ``MealParser`` with a scripted LLM (no network access)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from meal_voice.exceptions import LLMUnavailableError, MealParseError
from meal_voice.models import MealType, Unit
from meal_voice.services.meal_parser import (
    MEAL_EXTRACTION_PROMPT,
    MEAL_JSON_SCHEMA,
    GeminiLLMClient,
    MealParser,
    OpenAILLMClient,
    ParsedItem,
    build_llm_client,
)
from meal_voice.tests.conftest import ScriptedLLMClient, item, macros, meal_json


def parse(reply: str, transcript: str = "meal") -> tuple[Any, ScriptedLLMClient]:
    """Run the parser once against a single scripted reply."""
    client = ScriptedLLMClient(reply)
    return MealParser(client).parse(transcript, language="hi"), client


# --- Hinglish extraction cases -------------------------------------------------
# Each case replays what the LLM returns for the spoken phrase, including the
# messy-but-plausible variants (number words, "katori", missing quantity) that
# the parser must normalise before anything downstream sees them.

HINGLISH_CASES = [
    pytest.param(
        "do roti aur ek katori dal",
        [item("roti", 2, "piece"), item("dal", "ek", "katori")],
        [("roti", 2.0, Unit.PIECE, False), ("dal", 1.0, Unit.BOWL, False)],
        id="do-roti-ek-katori-dal",
    ),
    pytest.param(
        "aadha plate rice khaya",
        [item("rice", "aadha", "plate")],
        [("rice", 0.5, Unit.PLATE, False)],
        id="aadha-plate-rice",
    ),
    pytest.param(
        "dedh glass doodh",
        [item("milk", "dedh", "glass")],
        [("milk", 1.5, Unit.CUP, False)],
        id="dedh-glass-doodh",
    ),
    pytest.param(
        "teen idli sambar ke saath",
        [item("idli", 3, "piece"), item("sambar", None, "bowl")],
        [("idli", 3.0, Unit.PIECE, False), ("sambar", 1.0, Unit.BOWL, True)],
        id="teen-idli-sambar-assumed",
    ),
    pytest.param(
        "paneer bhurji with two paratha",
        [item("paneer bhurji", 0, "bowl"), item("paratha", "two", "pieces")],
        [("paneer bhurji", 1.0, Unit.BOWL, True), ("paratha", 2.0, Unit.PIECE, False)],
        id="zero-quantity-treated-as-assumed",
    ),
    pytest.param(
        "100 gram paneer aur ek chammach ghee",
        [item("paneer", "100", "gm"), item("ghee", 1, "teaspoon")],
        [("paneer", 100.0, Unit.GRAM, False), ("ghee", 1.0, Unit.TEASPOON, False)],
        id="gram-and-teaspoon-aliases",
    ),
    pytest.param(
        "ek cup chai aur do biscuit",
        [item("chai", "1/1", "cup"), item("biscuit", "2", "pcs")],
        [("chai", 1.0, Unit.CUP, False), ("biscuit", 2.0, Unit.PIECE, False)],
        id="fraction-and-pcs",
    ),
    pytest.param(
        "one scoop whey",
        [item("whey protein", 1, "scoop")],
        [("whey protein", 1.0, Unit.PIECE, False)],
        id="unknown-unit-defaults-to-piece",
    ),
]


@pytest.mark.parametrize(("transcript", "llm_items", "expected"), HINGLISH_CASES)
def test_hinglish_items_are_normalised(
    transcript: str,
    llm_items: list[dict[str, Any]],
    expected: list[tuple[str, float, Unit, bool]],
) -> None:
    meal, client = parse(meal_json(llm_items), transcript)

    assert [(i.name, i.quantity, i.unit, i.assumed_quantity) for i in meal.items] == expected
    # The transcript and language hint must reach the model verbatim.
    assert transcript in client.calls[0][0]["content"]
    assert "detected language: hi" in client.calls[0][0]["content"]


@pytest.mark.parametrize(
    ("transcript", "llm_meal_type", "expected"),
    [
        ("subah nashte mein poha khaya", "breakfast", MealType.BREAKFAST),
        ("raat ko dinner mein chicken curry", "Dinner", MealType.DINNER),
        ("shaam ko samosa", "evening snack", None),
        ("do roti", None, None),
    ],
)
def test_meal_type_is_normalised_or_dropped(
    transcript: str, llm_meal_type: str | None, expected: MealType | None
) -> None:
    meal, _ = parse(meal_json([item("poha", 1, "bowl")], meal_type=llm_meal_type), transcript)
    assert meal.meal_type == expected


def test_confidence_is_clamped_into_unit_interval() -> None:
    meal, _ = parse(meal_json([item("roti", 1, "piece")], confidence=1.7))
    assert meal.confidence == 1.0


def test_original_text_falls_back_to_name() -> None:
    meal, _ = parse(meal_json([item("dal", 1, "bowl", original_text="")]))
    assert meal.items[0].original_text == "dal"


def test_code_fenced_reply_is_accepted() -> None:
    fenced = "```json\n" + meal_json([item("roti", 2, "piece")]) + "\n```"
    meal, _ = parse(fenced)
    assert meal.items[0].quantity == 2.0


# --- Retry and failure behaviour -------------------------------------------------


def test_invalid_json_triggers_exactly_one_repair_round_trip() -> None:
    client = ScriptedLLMClient("not json at all", meal_json([item("roti", 2, "piece")]))

    meal = MealParser(client).parse("do roti")

    assert meal.items[0].name == "roti"
    assert len(client.calls) == 2
    repair_turns = client.calls[1]
    assert repair_turns[1] == {"role": "assistant", "content": "not json at all"}
    assert repair_turns[2]["role"] == "user"
    assert "ONLY the corrected JSON" in repair_turns[2]["content"]


def test_schema_violation_is_repaired_on_second_attempt() -> None:
    missing_macros = json.dumps({"meal_type": None, "items": [{"name": "roti"}], "confidence": 0.8})
    client = ScriptedLLMClient(missing_macros, meal_json([item("roti", 1, "piece")]))

    meal = MealParser(client).parse("roti")

    assert meal.items[0].macros.calories == 100
    assert "macros" in client.calls[1][2]["content"]


def test_two_invalid_replies_raise_parse_error() -> None:
    client = ScriptedLLMClient("{", "still broken")

    with pytest.raises(MealParseError, match="after 2 attempts"):
        MealParser(client).parse("kuch bhi")
    assert len(client.calls) == 2


def test_empty_item_list_fails_fast_without_retry() -> None:
    client = ScriptedLLMClient(meal_json([]))

    with pytest.raises(MealParseError, match="No food items"):
        MealParser(client).parse("hello, testing one two three")
    assert len(client.calls) == 1


def test_blank_transcript_never_calls_the_model() -> None:
    client = ScriptedLLMClient()

    with pytest.raises(MealParseError, match="no recognisable speech"):
        MealParser(client).parse("   ")
    assert client.calls == []


def test_missing_name_is_a_schema_error() -> None:
    with pytest.raises(ValueError, match="name"):
        ParsedItem.model_validate({"name": "  ", "quantity": 1, "unit": "piece", "macros": macros(1, 1, 1, 1)})


# --- Prompt and schema contracts --------------------------------------------------


def test_prompt_and_schema_cover_required_fields() -> None:
    assert "do=2, teen=3, aadha=0.5, dedh=1.5" in MEAL_EXTRACTION_PROMPT
    assert "assumed_quantity=true" in MEAL_EXTRACTION_PROMPT

    item_schema = MEAL_JSON_SCHEMA["properties"]["items"]["items"]
    assert set(item_schema["required"]) == {
        "name", "original_text", "quantity", "unit", "assumed_quantity", "macros"
    }
    assert item_schema["properties"]["unit"]["enum"] == list(Unit.values)
    assert item_schema["additionalProperties"] is False


# --- Provider selection -----------------------------------------------------------

NO_KEYS = {"GEMINI_API_KEY": "", "OPENAI_API_KEY": ""}


def configure(settings: Any, provider: str = "auto", **keys: str) -> None:
    """Override the LLM section of ``VOICE_MEAL`` for one test."""
    settings.VOICE_MEAL = {**settings.VOICE_MEAL, **NO_KEYS, "LLM_PROVIDER": provider, **keys}


@pytest.mark.parametrize(
    ("provider", "keys", "expected"),
    [
        ("auto", {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}, GeminiLLMClient),
        ("auto", {"OPENAI_API_KEY": "o"}, OpenAILLMClient),
        ("auto", {"GEMINI_API_KEY": "g"}, GeminiLLMClient),
        ("openai", {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}, OpenAILLMClient),
        ("Gemini", {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"}, GeminiLLMClient),
    ],
)
def test_build_llm_client_selects_provider(
    settings: Any, provider: str, keys: dict[str, str], expected: type
) -> None:
    configure(settings, provider, **keys)

    client = build_llm_client()

    assert isinstance(client, expected)
    assert client.provider == expected.provider
    assert client.model == settings.VOICE_MEAL[f"{expected.provider.upper()}_MODEL"]


def test_build_llm_client_without_any_key_raises(settings: Any) -> None:
    configure(settings)

    with pytest.raises(LLMUnavailableError, match="GEMINI_API_KEY"):
        build_llm_client()


def test_build_llm_client_explicit_provider_needs_its_own_key(settings: Any) -> None:
    configure(settings, "gemini", OPENAI_API_KEY="o")

    with pytest.raises(LLMUnavailableError, match="No LLM provider configured"):
        build_llm_client()


def test_build_llm_client_rejects_unknown_provider(settings: Any) -> None:
    configure(settings, "cohere", GEMINI_API_KEY="g")

    with pytest.raises(LLMUnavailableError, match="Unknown LLM_PROVIDER"):
        build_llm_client()
