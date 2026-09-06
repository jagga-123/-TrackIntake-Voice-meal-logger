"""Persistence models for meal logs captured manually or by voice.

The schema mirrors a typical nutrition-tracker meal log: one ``MealLog`` per
eating occasion with denormalised ``total_macros``, and one ``MealItem`` per
food with its own quantity, unit and macro breakdown.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from meal_voice.services.macros import Macros


class MealType(models.TextChoices):
    """Eating occasion a meal log belongs to."""

    BREAKFAST = "breakfast", "Breakfast"
    LUNCH = "lunch", "Lunch"
    DINNER = "dinner", "Dinner"
    SNACK = "snack", "Snack"


class MealSource(models.TextChoices):
    """How a meal log entered the system."""

    MANUAL = "manual", "Manual"
    VOICE = "voice", "Voice"


class Unit(models.TextChoices):
    """Normalised serving units accepted for a meal item."""

    PIECE = "piece", "Piece"
    BOWL = "bowl", "Bowl"
    CUP = "cup", "Cup"
    PLATE = "plate", "Plate"
    GRAM = "g", "Gram"
    MILLILITRE = "ml", "Millilitre"
    TABLESPOON = "tbsp", "Tablespoon"
    TEASPOON = "tsp", "Teaspoon"


class MacroSource(models.TextChoices):
    """Provenance of an item's macros: curated table or LLM estimate."""

    TABLE = "table", "Nutrition table"
    LLM_ESTIMATE = "llm_estimate", "LLM estimate"


def empty_macros() -> dict[str, float]:
    """Default value for macro JSON fields (callable so each row gets its own dict)."""
    return Macros.zero().model_dump()


class MealLog(models.Model):
    """One eating occasion for a user, with denormalised macro totals."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meal_logs"
    )
    meal_type = models.CharField(max_length=16, choices=MealType.choices)
    logged_at = models.DateTimeField(default=timezone.now, db_index=True)
    source = models.CharField(
        max_length=16, choices=MealSource.choices, default=MealSource.MANUAL
    )
    transcript = models.TextField(null=True, blank=True)
    total_macros = models.JSONField(default=empty_macros)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-logged_at", "-id")

    def __str__(self) -> str:
        return f"{self.get_meal_type_display()} for {self.user} at {self.logged_at:%Y-%m-%d %H:%M}"

    def recalculate_totals(self, *, save: bool = True) -> dict[str, float]:
        """Recompute ``total_macros`` from the related items.

        Totals are always derived server-side so a client can never submit
        totals that disagree with the items they accompany.
        """
        total = Macros.total(Macros(**item.macros) for item in self.items.all()).rounded()
        self.total_macros = total.model_dump()
        if save:
            self.save(update_fields=["total_macros"])
        return self.total_macros


class MealItem(models.Model):
    """A single food within a meal log, with the quantity the user reported."""

    meal_log = models.ForeignKey(MealLog, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=120)
    original_text = models.CharField(
        max_length=255, blank=True, help_text="Verbatim phrase from the transcript."
    )
    quantity = models.DecimalField(max_digits=7, decimal_places=2)
    unit = models.CharField(max_length=8, choices=Unit.choices)
    assumed_quantity = models.BooleanField(
        default=False, help_text="True when no quantity was spoken and 1 serving was assumed."
    )
    macros = models.JSONField(default=empty_macros)
    macro_source = models.CharField(
        max_length=16, choices=MacroSource.choices, default=MacroSource.LLM_ESTIMATE
    )
    confidence = models.FloatField(
        default=1.0, validators=[MinValueValidator(0.0), MaxValueValidator(1.0)]
    )

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.quantity:g} {self.unit} {self.name}"
