"""Tests for the food-table lookup and portion scaling."""

from __future__ import annotations

import pytest

from meal_voice.models import Unit
from meal_voice.services.macros import Macros
from meal_voice.services.nutrition_lookup import (
    NutritionLookup,
    normalise_food_name,
    singularise,
    strip_filler_words,
)

LLM_GUESS = Macros(calories=999, protein_g=9, carbs_g=9, fats_g=9)


@pytest.fixture(scope="module")
def lookup() -> NutritionLookup:
    """Single table load shared by the module."""
    return NutritionLookup()


def test_table_loads_at_least_forty_foods(lookup: NutritionLookup) -> None:
    assert len(lookup.entries) >= 40


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("roti", "Roti"),
        ("Chapati", "Roti"),
        ("rotis", "Roti"),
        ("chapatti", "Roti"),  # spelling variant via strict fuzzy match
        ("ek katori dal (tadka)", "Dal"),  # quantity + unit words stripped
        ("2 roti", "Roti"),
        ("doodh", "Milk"),
        ("wheat roti", "Roti"),
        ("chicken curry with gravy", "Chicken curry"),
        ("masoor ki dal", "Dal"),
        ("masala dosa", "Masala dosa"),
        ("idly", "Idli"),
        ("Paneer Butter Masala", "Paneer butter masala"),
    ],
)
def test_find_matches_names_and_aliases(lookup: NutritionLookup, spoken: str, expected: str) -> None:
    entry = lookup.find(spoken)
    assert entry is not None and entry.name == expected


@pytest.mark.parametrize(
    "spoken",
    ["quinoa salad", "chicken salad", "egg bhurji", "dal makhani", "sushi", "", "   "],
)
def test_find_returns_none_rather_than_a_wrong_dish(lookup: NutritionLookup, spoken: str) -> None:
    assert lookup.find(spoken) is None


def test_table_macros_are_scaled_by_quantity(lookup: NutritionLookup) -> None:
    result = lookup.resolve("roti", 2, Unit.PIECE, LLM_GUESS)

    assert result.macro_source == "table"
    assert result.matched_food == "Roti"
    assert result.macros.calories == pytest.approx(208)
    assert result.macros.protein_g == pytest.approx(6.2)


def test_grams_are_converted_through_serving_weight(lookup: NutritionLookup) -> None:
    result = lookup.resolve("rice", 300, Unit.GRAM, LLM_GUESS)  # serving is 150 g

    assert result.macro_source == "table"
    assert result.macros.calories == pytest.approx(390)


def test_alternate_units_use_per_food_conversions(lookup: NutritionLookup) -> None:
    plate = lookup.resolve("rice", 1, Unit.PLATE, LLM_GUESS)  # 250 g vs 150 g bowl

    assert plate.macro_source == "table"
    assert plate.macros.calories == pytest.approx(195 * 250 / 150, abs=0.1)


def test_unconvertible_unit_falls_back_to_llm_estimate(lookup: NutritionLookup) -> None:
    result = lookup.resolve("roti", 1, Unit.CUP, LLM_GUESS)

    assert result.macro_source == "llm_estimate"
    assert result.matched_food is None
    assert result.macros == LLM_GUESS.rounded()


def test_unknown_food_falls_back_to_llm_estimate(lookup: NutritionLookup) -> None:
    result = lookup.resolve("quinoa salad", 1, Unit.BOWL, LLM_GUESS)

    assert result.macro_source == "llm_estimate"
    assert result.macros == LLM_GUESS.rounded()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Dal-Tadka (yellow) ", "dal tadka"), ("Chai!!", "chai"), ("egg_curry", "egg curry")],
)
def test_normalise_food_name(raw: str, expected: str) -> None:
    assert normalise_food_name(raw) == expected


@pytest.mark.parametrize(
    ("plural", "singular"),
    [("rotis", "roti"), ("eggs", "egg"), ("dosas", "dosa"), ("glass", "glass"), ("berries", "berry")],
)
def test_singularise(plural: str, singular: str) -> None:
    assert singularise(plural) == singular


@pytest.mark.parametrize(
    ("query", "expected"),
    [("ek katori dal", "dal"), ("2 large glass milk", "milk"), ("paneer", "paneer"), ("do", "")],
)
def test_strip_filler_words(query: str, expected: str) -> None:
    assert strip_filler_words(query) == expected
