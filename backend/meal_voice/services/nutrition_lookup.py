"""Resolve item macros from a curated food table, falling back to LLM estimates.

Table values are preferred because they are deterministic and auditable; the
LLM's own estimate is kept only when no table entry matches the item or the
spoken unit cannot be converted for that food. Matching deliberately favours
precision: a wrong table hit is worse than an honestly labelled estimate.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meal_voice.models import MacroSource, Unit
from meal_voice.services.macros import Macros
from meal_voice.services.meal_parser import NUMBER_WORDS, UNIT_ALIASES

logger = logging.getLogger(__name__)

FOOD_TABLE_PATH = Path(__file__).resolve().parent.parent / "data" / "indian_foods.json"

MacroSourceLiteral = Literal["table", "llm_estimate"]

# Tokens that carry no food identity: quantities, units, connectors and
# generic descriptors. They are stripped only after an exact match fails.
FILLER_WORDS: frozenset[str] = (
    frozenset(NUMBER_WORDS)
    | frozenset(UNIT_ALIASES)
    | frozenset(Unit.values)
    | frozenset(
        {
            "with", "and", "aur", "ke", "saath", "ka", "ki", "ko", "of", "the", "some", "plus",
            "wala", "wali", "vala", "plain", "hot", "cold", "fresh", "homemade", "home", "made",
            "small", "big", "large", "medium", "regular", "normal", "gravy", "cooked",
        }
    )
)

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")
_NUMERIC = re.compile(r"^\d+(?:\.\d+)?$")


class ServingSpec(BaseModel):
    """The reference serving that ``macros_per_serving`` describes."""

    model_config = ConfigDict(frozen=True)

    unit: Unit
    grams: float = Field(gt=0)


class FoodEntry(BaseModel):
    """One row of the nutrition table."""

    model_config = ConfigDict(frozen=True)

    name: str
    aliases: list[str] = Field(default_factory=list)
    serving: ServingSpec
    unit_grams: dict[Unit, float] = Field(
        default_factory=dict,
        description="Grams per unit for units other than the serving unit.",
    )
    macros_per_serving: Macros

    def search_terms(self) -> list[str]:
        """Every normalised string that should resolve to this entry."""
        return [normalise_food_name(term) for term in (self.name, *self.aliases)]

    def grams_for(self, unit: Unit) -> float | None:
        """Grams contained in one ``unit`` of this food, or ``None`` if unknown."""
        if unit == Unit.GRAM:
            return 1.0
        if unit == self.serving.unit:
            return self.serving.grams
        return self.unit_grams.get(unit)


class NutritionResult(BaseModel):
    """Macros chosen for an item together with where they came from."""

    model_config = ConfigDict(frozen=True)

    macros: Macros
    macro_source: MacroSourceLiteral
    matched_food: str | None = None


def normalise_food_name(name: str) -> str:
    """Lower-case, strip punctuation and collapse whitespace for matching."""
    lowered = name.lower().replace("-", " ").replace("_", " ")
    without_parens = re.sub(r"\([^)]*\)", " ", lowered)
    cleaned = _NON_ALNUM.sub(" ", without_parens)
    return _WHITESPACE.sub(" ", cleaned).strip()


def singularise(term: str) -> str:
    """Very small English plural stripper (``rotis`` → ``roti``, ``eggs`` → ``egg``)."""
    if term.endswith("ies") and len(term) > 4:
        return term[:-3] + "y"
    if term.endswith("es") and term[:-2].endswith(("sh", "ch", "x", "s")):
        return term[:-2]
    if term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def strip_filler_words(query: str) -> str:
    """Drop quantities, units and descriptors (``ek katori dal`` → ``dal``)."""
    kept = [w for w in query.split() if w not in FILLER_WORDS and not _NUMERIC.match(w)]
    return " ".join(kept)


class NutritionLookup:
    """Match free-text food names to the table and scale macros to the spoken portion.

    Args:
        table_path: JSON file containing a list of :class:`FoodEntry` records.
        fuzzy_cutoff: Minimum ``difflib`` similarity for a fuzzy whole-name match;
            kept high so spelling variants match but different dishes do not.
    """

    def __init__(self, table_path: Path = FOOD_TABLE_PATH, fuzzy_cutoff: float = 0.9) -> None:
        self._table_path = table_path
        self._fuzzy_cutoff = fuzzy_cutoff

    @cached_property
    def entries(self) -> list[FoodEntry]:
        """Load and validate the table once per instance."""
        with self._table_path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        entries = [FoodEntry.model_validate(record) for record in raw]
        logger.info("Loaded %d foods from %s", len(entries), self._table_path.name)
        return entries

    @cached_property
    def _index(self) -> dict[str, FoodEntry]:
        """Normalised search term → entry. Earlier entries win on alias clashes."""
        index: dict[str, FoodEntry] = {}
        for entry in self.entries:
            for term in entry.search_terms():
                index.setdefault(term, entry)
        return index

    def find(self, name: str) -> FoodEntry | None:
        """Return the table entry for ``name`` or ``None`` when nothing fits.

        Strategy, strictest first: exact term; singularised term; the same two
        after filler words are stripped; finally a strict fuzzy match on the
        stripped name to absorb spelling variants (``chapatti`` → ``chapati``).
        Names that merely *contain* a known food (``quinoa salad``) are left
        to the LLM estimate on purpose.
        """
        query = normalise_food_name(name)
        if not query:
            return None
        reduced = strip_filler_words(query)
        candidates = dict.fromkeys(
            (query, singularise(query), reduced, singularise(reduced))
        )
        for candidate in candidates:
            if candidate and candidate in self._index:
                return self._index[candidate]
        close = difflib.get_close_matches(
            reduced or query, self._index, n=1, cutoff=self._fuzzy_cutoff
        )
        return self._index[close[0]] if close else None

    def resolve(
        self, name: str, quantity: float, unit: Unit, llm_estimate: Macros
    ) -> NutritionResult:
        """Choose macros for one item: scaled table values if possible, else the LLM estimate."""
        entry = self.find(name)
        grams_per_unit = entry.grams_for(unit) if entry else None
        if entry is None or grams_per_unit is None:
            if entry is not None:
                logger.info("No %s conversion for '%s'; using LLM estimate", unit, entry.name)
            return NutritionResult(
                macros=llm_estimate.rounded(),
                macro_source=MacroSource.LLM_ESTIMATE.value,
            )
        servings = quantity * grams_per_unit / entry.serving.grams
        return NutritionResult(
            macros=entry.macros_per_serving.scaled(servings).rounded(),
            macro_source=MacroSource.TABLE.value,
            matched_food=entry.name,
        )
