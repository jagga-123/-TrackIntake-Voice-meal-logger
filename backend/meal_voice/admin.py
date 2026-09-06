"""Django admin registration for meal logs and their items."""

from __future__ import annotations

from django.contrib import admin

from meal_voice.models import MealItem, MealLog


class MealItemInline(admin.TabularInline):
    """Edit a meal's items inline on the meal log page."""

    model = MealItem
    extra = 0
    fields = (
        "name",
        "quantity",
        "unit",
        "assumed_quantity",
        "macro_source",
        "confidence",
        "macros",
    )


@admin.register(MealLog)
class MealLogAdmin(admin.ModelAdmin):
    """Admin list and detail views for meal logs."""

    list_display = ("id", "user", "meal_type", "source", "logged_at", "calories")
    list_filter = ("meal_type", "source", "logged_at")
    search_fields = ("user__username", "transcript", "items__name")
    date_hierarchy = "logged_at"
    inlines = (MealItemInline,)
    readonly_fields = ("total_macros", "created_at")

    @admin.display(description="kcal")
    def calories(self, obj: MealLog) -> float:
        """Show the denormalised calorie total in the list view."""
        return obj.total_macros.get("calories", 0.0)
