# Schema analysis

## Why the schema is inferred

TrackIntake's dashboard sits behind a paid subscription, so the real meal-log
storage structure could not be inspected directly. The models in this
repository were therefore derived from two sources:

1. **The assignment brief**, which describes a meal log as a list of items, each
   with a quantity, a unit and a macronutrient object (calories, protein, carbs,
   fats), plus a meal-level total.
2. **Standard nutrition-tracker patterns** (MyFitnessPal, Cronometer, HealthifyMe
   and similar): one row per eating occasion, child rows per food, denormalised
   totals on the parent for fast dashboard queries, and a `source` discriminator
   so voice, manual and integration entries can be told apart.

The result is intentionally conservative: it mirrors the shape the brief
implies and adds only what the voice pipeline needs to explain itself.

## Assumed models

```text
MealLog
├── id            bigint PK
├── user          FK → auth user
├── meal_type     enum  breakfast | lunch | dinner | snack
├── logged_at     datetime (when the meal was eaten; defaults to now)
├── source        enum  manual | voice
├── transcript    text, nullable   (voice only)
├── total_macros  JSON  {calories, protein_g, carbs_g, fats_g}
└── created_at    datetime

MealItem
├── id                bigint PK
├── meal_log          FK → MealLog (related_name="items")
├── name              varchar(120)   normalised food name
├── original_text     varchar(255)   verbatim phrase from the transcript
├── quantity          decimal(7,2)
├── unit              enum piece | bowl | cup | plate | g | ml | tbsp | tsp
├── assumed_quantity  bool           true when 1 serving was assumed
├── macros            JSON {calories, protein_g, carbs_g, fats_g}
├── macro_source      enum table | llm_estimate      ← added for provenance
└── confidence        float 0–1
```

`macro_source` is the only field not named in the brief. It records whether an
item's macros came from the curated table or from the LLM, which the UI needs
to show the "AI estimate" badge and which is valuable later for auditing.

## Assumed JSON payloads

Preview (returned by `POST /voice/preview/`, never persisted):

```json
{
  "transcript": "do roti aur ek katori dal",
  "language": "hi",
  "duration": 2.4,
  "meal_type": "lunch",
  "meal_type_source": "transcript",
  "source": "voice",
  "confidence": 0.92,
  "items": [
    {
      "name": "roti",
      "original_text": "do roti",
      "quantity": 2.0,
      "unit": "piece",
      "assumed_quantity": false,
      "macros": {"calories": 208, "protein_g": 6.2, "carbs_g": 36, "fats_g": 5.2},
      "macro_source": "table",
      "matched_food": "Roti",
      "confidence": 0.92
    }
  ],
  "total_macros": {"calories": 358, "protein_g": 15.2, "carbs_g": 56, "fats_g": 8.7}
}
```

Stored log (returned by confirm and list):

```json
{
  "id": 17,
  "meal_type": "lunch",
  "logged_at": "2026-09-06T13:05:00+05:30",
  "source": "voice",
  "transcript": "do roti aur ek katori dal",
  "total_macros": {"calories": 358, "protein_g": 15.2, "carbs_g": 56, "fats_g": 8.7},
  "items": [ { "...": "same fields as the preview item, minus matched_food, plus id" } ],
  "created_at": "2026-09-06T07:35:12Z"
}
```

## Adapting to the real TrackIntake schema (under 30 minutes)

The pipeline is decoupled from persistence: `VoiceMealPipeline.run()` returns
plain dictionaries, and only `ConfirmMealSerializer.create()` and the two read
serializers touch the ORM. Adapting to the real schema is therefore a
serializer-and-model exercise:

| Likely difference in the real schema                     | Change                                                                                                             | Effort |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------ |
| Different table/column names (`meals`, `meal_entries`)   | Rename fields in `models.py`, regenerate the migration (or point `Meta.db_table` / `db_column` at existing tables) | 5 min  |
| Macros stored as columns, not JSON                       | Replace the two `JSONField`s with `DecimalField`s; update `Macros` ↔ model mapping in `recalculate_totals()` and the serializers | 10 min |
| Foods referenced by FK to a `Food` table                 | In `ConfirmMealSerializer.create()`, resolve `matched_food` / name to a `Food` row (or create a pending one) before `bulk_create` | 10 min |
| Extra fields (`fibre_g`, `sugar_g`, micronutrients)      | Add keys to `Macros` and `MACRO_KEYS`; extend the JSON schema in `meal_parser.py` and the table rows                | 5 min  |
| Units expressed differently (`serving`, `katori`)        | Extend the `Unit` enum and `UNIT_ALIASES`; the frontend `UNITS` list mirrors it                                     | 5 min  |
| Meal types beyond four (e.g. `pre_workout`)              | Extend `MealType`; the JSON schema and frontend list are built from the enum                                        | 2 min  |

Nothing in `transcriber.py`, `meal_parser.py`, `nutrition_lookup.py` or the
frontend recorder needs to change; the preview contract is the stable seam.
