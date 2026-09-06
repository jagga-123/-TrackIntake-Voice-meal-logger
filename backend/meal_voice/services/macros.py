"""Macronutrient value object shared by the parser, lookup, pipeline and models."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

MACRO_KEYS: tuple[str, ...] = ("calories", "protein_g", "carbs_g", "fats_g")


class Macros(BaseModel):
    """Calories and macronutrients for one item or one whole meal.

    Instances are immutable; arithmetic returns new objects so partially
    computed totals can never leak back into their inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    calories: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    fats_g: float = Field(ge=0)

    @classmethod
    def zero(cls) -> Macros:
        """Return an all-zero macro set, the identity element for :meth:`total`."""
        return cls(calories=0.0, protein_g=0.0, carbs_g=0.0, fats_g=0.0)

    @classmethod
    def total(cls, parts: Iterable[Macros]) -> Macros:
        """Sum an iterable of macro sets."""
        result = cls.zero()
        for part in parts:
            result = result + part
        return result

    def __add__(self, other: Macros) -> Macros:
        return Macros(
            calories=self.calories + other.calories,
            protein_g=self.protein_g + other.protein_g,
            carbs_g=self.carbs_g + other.carbs_g,
            fats_g=self.fats_g + other.fats_g,
        )

    def scaled(self, factor: float) -> Macros:
        """Multiply every macro by ``factor`` (e.g. a serving multiplier)."""
        return Macros(
            calories=self.calories * factor,
            protein_g=self.protein_g * factor,
            carbs_g=self.carbs_g * factor,
            fats_g=self.fats_g * factor,
        )

    def rounded(self, ndigits: int = 1) -> Macros:
        """Round every macro to ``ndigits`` decimal places for presentation."""
        return Macros(
            calories=round(self.calories, ndigits),
            protein_g=round(self.protein_g, ndigits),
            carbs_g=round(self.carbs_g, ndigits),
            fats_g=round(self.fats_g, ndigits),
        )
