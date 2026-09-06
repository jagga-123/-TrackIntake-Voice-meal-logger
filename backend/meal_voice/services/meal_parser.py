"""LLM-backed extraction of structured meal data from free-form transcripts.

The parser is provider-agnostic: anything satisfying :class:`LLMClient` can be
injected. Production uses Anthropic Claude, falling back to OpenAI when only
that key is configured; tests inject a scripted client so they stay hermetic.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Protocol, TypedDict

from django.conf import settings
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from meal_voice.exceptions import LLMUnavailableError, MealParseError
from meal_voice.models import MealType, Unit
from meal_voice.services.macros import Macros

logger = logging.getLogger(__name__)

MEAL_EXTRACTION_PROMPT = (
    "You are a nutrition data extractor. Convert the meal description into strict JSON.\n"
    'Rules: handle Hindi, English and Hinglish ("do roti", "ek katori dal", "half plate rice"); '
    "convert number words (do=2, teen=3, aadha=0.5, dedh=1.5); "
    "normalize units to piece/bowl/cup/plate/g/ml/tbsp/tsp; "
    "estimate macros per item using standard Indian/global portion sizes; "
    "if quantity missing assume 1 serving and set assumed_quantity=true; "
    "infer meal_type from context/time words if present else null. "
    "Output ONLY JSON matching: {meal_type, items:[{name, original_text, quantity, unit, "
    "assumed_quantity, macros:{calories,protein_g,carbs_g,fats_g}}], confidence}"
)

REPAIR_PROMPT = (
    "Your previous reply did not satisfy the required schema: {error}\n"
    "Reply again with ONLY the corrected JSON object and nothing else."
)

MACROS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fats_g": {"type": "number"},
    },
    "required": ["calories", "protein_g", "carbs_g", "fats_g"],
    "additionalProperties": False,
}

MEAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "meal_type": {
            "anyOf": [{"type": "string", "enum": list(MealType.values)}, {"type": "null"}]
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "original_text": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string", "enum": list(Unit.values)},
                    "assumed_quantity": {"type": "boolean"},
                    "macros": MACROS_JSON_SCHEMA,
                },
                "required": [
                    "name",
                    "original_text",
                    "quantity",
                    "unit",
                    "assumed_quantity",
                    "macros",
                ],
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "number"},
    },
    "required": ["meal_type", "items", "confidence"],
    "additionalProperties": False,
}

# A meal description is a deliberately short output; this ceiling leaves ample
# headroom for a dozen items without risking a truncated JSON document.
MAX_OUTPUT_TOKENS = 4096

# Safety net for models that echo the spoken word instead of a numeral.
NUMBER_WORDS: dict[str, float] = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "aadha": 0.5, "adha": 0.5, "sawa": 1.25, "dedh": 1.5, "dhai": 2.5, "adhai": 2.5,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "half": 0.5, "quarter": 0.25, "a": 1, "an": 1,
}

# Colloquial units mapped onto the canonical set; unknown units become "piece".
UNIT_ALIASES: dict[str, Unit] = {
    "katori": Unit.BOWL, "vati": Unit.BOWL, "bowl": Unit.BOWL, "bowls": Unit.BOWL,
    "glass": Unit.CUP, "glasses": Unit.CUP, "cup": Unit.CUP, "cups": Unit.CUP, "mug": Unit.CUP,
    "plate": Unit.PLATE, "plates": Unit.PLATE, "thali": Unit.PLATE,
    "piece": Unit.PIECE, "pieces": Unit.PIECE, "pc": Unit.PIECE, "pcs": Unit.PIECE,
    "nos": Unit.PIECE, "serving": Unit.PIECE, "servings": Unit.PIECE, "unit": Unit.PIECE,
    "g": Unit.GRAM, "gm": Unit.GRAM, "gms": Unit.GRAM, "gram": Unit.GRAM, "grams": Unit.GRAM,
    "ml": Unit.MILLILITRE, "millilitre": Unit.MILLILITRE, "milliliter": Unit.MILLILITRE,
    "tbsp": Unit.TABLESPOON, "tablespoon": Unit.TABLESPOON, "tablespoons": Unit.TABLESPOON,
    "tsp": Unit.TEASPOON, "teaspoon": Unit.TEASPOON, "teaspoons": Unit.TEASPOON,
}

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class ChatMessage(TypedDict):
    """One turn of a chat-style LLM conversation."""

    role: Literal["user", "assistant"]
    content: str


class LLMClient(Protocol):
    """Minimal chat-completion interface the parser depends on."""

    provider: str

    def complete(self, system: str, messages: list[ChatMessage]) -> str:
        """Return the model's raw text reply for ``messages`` under ``system``."""


class ParsedItem(BaseModel):
    """One food item as extracted by the LLM, after normalisation."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str
    original_text: str = ""
    quantity: float | None = None
    unit: Unit = Unit.PIECE
    assumed_quantity: bool = False
    macros: Macros

    @field_validator("quantity", mode="before")
    @classmethod
    def coerce_quantity(cls, value: Any) -> float | None:
        """Accept numerals, numeric strings, fractions and Hindi/English number words."""
        if value is None or isinstance(value, (int, float)):
            return value
        token = str(value).strip().lower()
        if token in NUMBER_WORDS:
            return NUMBER_WORDS[token]
        if "/" in token:
            numerator, _, denominator = token.partition("/")
            try:
                return float(numerator) / float(denominator)
            except (ValueError, ZeroDivisionError):
                return None
        try:
            return float(token)
        except ValueError:
            return None

    @field_validator("unit", mode="before")
    @classmethod
    def normalise_unit(cls, value: Any) -> Unit:
        """Map colloquial units (katori, glass, gm) onto the canonical enum."""
        token = str(value or "").strip().lower().rstrip(".")
        if token in Unit.values:
            return Unit(token)
        if token in UNIT_ALIASES:
            return UNIT_ALIASES[token]
        logger.warning("Unknown unit %r from LLM; defaulting to 'piece'", value)
        return Unit.PIECE

    @model_validator(mode="after")
    def apply_defaults(self) -> ParsedItem:
        """Assume one serving when the quantity is missing and echo the name as original text."""
        if not self.name:
            raise ValueError("item name must not be empty")
        if self.quantity is None or self.quantity <= 0:
            self.quantity = 1.0
            self.assumed_quantity = True
        if not self.original_text:
            self.original_text = self.name
        return self


class ParsedMeal(BaseModel):
    """Full structured result of parsing one transcript."""

    model_config = ConfigDict(extra="ignore")

    meal_type: MealType | None = None
    items: list[ParsedItem]
    confidence: float = 0.5

    @field_validator("meal_type", mode="before")
    @classmethod
    def normalise_meal_type(cls, value: Any) -> str | None:
        """Lower-case the value and treat anything outside the enum as unknown."""
        token = str(value or "").strip().lower()
        return token if token in MealType.values else None

    @field_validator("confidence", mode="after")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        """Keep confidence inside ``[0, 1]`` regardless of what the model emitted."""
        return max(0.0, min(1.0, value))


class AnthropicLLMClient:
    """Chat completion through the official ``anthropic`` SDK with JSON-schema output."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        import anthropic  # deferred so the OpenAI-only deployment needs no Anthropic SDK

        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.model = model

    def complete(self, system: str, messages: list[ChatMessage]) -> str:
        """Call Claude with the meal schema enforced via structured outputs."""
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": MEAL_JSON_SCHEMA}},
            )
        except self._sdk.APIError as exc:
            raise LLMUnavailableError(f"Anthropic request failed ({type(exc).__name__}).") from exc

        if response.stop_reason == "refusal":
            raise MealParseError("The model declined to process this transcript.")
        return next((block.text for block in response.content if block.type == "text"), "")


class OpenAILLMClient:
    """Chat completion through the official ``openai`` SDK in JSON mode."""

    provider = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        import openai  # deferred so the Anthropic-only deployment needs no OpenAI SDK

        self._sdk = openai
        self._client = openai.OpenAI(api_key=api_key, timeout=timeout)
        self.model = model

    def complete(self, system: str, messages: list[ChatMessage]) -> str:
        """Call the chat completions endpoint with ``response_format=json_object``."""
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *messages],
                response_format={"type": "json_object"},
                max_completion_tokens=MAX_OUTPUT_TOKENS,
            )
        except self._sdk.APIError as exc:
            raise LLMUnavailableError(f"OpenAI request failed ({type(exc).__name__}).") from exc
        return response.choices[0].message.content or ""


def build_llm_client() -> LLMClient:
    """Instantiate the configured provider, preferring Anthropic over OpenAI.

    Raises:
        LLMUnavailableError: when neither provider has an API key configured.
    """
    config = settings.VOICE_MEAL
    timeout = config["LLM_TIMEOUT_SECONDS"]
    if config["ANTHROPIC_API_KEY"]:
        return AnthropicLLMClient(config["ANTHROPIC_API_KEY"], config["ANTHROPIC_MODEL"], timeout)
    if config["OPENAI_API_KEY"]:
        return OpenAILLMClient(config["OPENAI_API_KEY"], config["OPENAI_MODEL"], timeout)
    raise LLMUnavailableError(
        "No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )


class MealParser:
    """Turn a transcript into a validated :class:`ParsedMeal` via an LLM.

    Invalid JSON or schema violations trigger one repair round-trip in which the
    model sees its previous reply and the validation error.
    """

    def __init__(self, client: LLMClient, max_attempts: int = 2) -> None:
        self._client = client
        self._max_attempts = max_attempts

    def parse(self, transcript: str, language: str | None = None) -> ParsedMeal:
        """Extract meal items from ``transcript``.

        Args:
            transcript: Free-form meal description in Hindi, English or Hinglish.
            language: Optional ISO code detected by the transcriber, passed as a hint.

        Raises:
            MealParseError: if the transcript is empty, contains no food, or the
                model fails to produce schema-valid JSON after the repair attempt.
            LLMUnavailableError: propagated from the provider client.
        """
        transcript = transcript.strip()
        if not transcript:
            raise MealParseError("The recording contained no recognisable speech.")

        messages: list[ChatMessage] = [
            {"role": "user", "content": self._user_message(transcript, language)}
        ]
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            raw = self._client.complete(MEAL_EXTRACTION_PROMPT, messages)
            try:
                meal = ParsedMeal.model_validate(self._extract_json(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "Attempt %d/%d produced invalid meal JSON via %s: %s",
                    attempt, self._max_attempts, self._client.provider, exc,
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {"role": "user", "content": REPAIR_PROMPT.format(error=_summarise(exc))}
                )
                continue

            if not meal.items:
                raise MealParseError("No food items were detected in the transcript.")
            return meal

        raise MealParseError(
            f"The model did not return valid meal JSON after {self._max_attempts} attempts."
        ) from last_error

    @staticmethod
    def _user_message(transcript: str, language: str | None) -> str:
        """Compose the user turn, including the detected language as a hint."""
        hint = f" (detected language: {language})" if language else ""
        return f"Meal description{hint}: {transcript}"

    @staticmethod
    def _extract_json(raw: str) -> Any:
        """Parse the model reply, tolerating code fences and surrounding prose."""
        text = raw.strip()
        fenced = _CODE_FENCE.match(text)
        if fenced:
            text = fenced.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
        return json.loads(text)


def _summarise(exc: Exception) -> str:
    """Compress a validation error into a single line suitable for a repair prompt."""
    return " ".join(str(exc).split())[:500]
