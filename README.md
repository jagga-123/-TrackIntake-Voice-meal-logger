# TrackIntake · Voice Meal Ingestion Pipeline

**Audio → Text → Structured Data.** Speak a meal in Hindi, English or Hinglish
("do roti aur ek katori dal"), review an editable preview with per-item macros,
and save it as a meal log. Built as an internship assignment for
[TrackIntake](https://trackintake.co.in).

| Layer    | Stack                                                                  |
| -------- | ---------------------------------------------------------------------- |
| Backend  | Django 5 · Django REST Framework · SimpleJWT · faster-whisper · Google Gemini (OpenAI fallback) · pydantic |
| Frontend | React 18 · Vite · plain CSS Modules · axios · MediaRecorder API        |
| Tests    | pytest + pytest-django (95 tests, no network or model downloads needed) |

---

## Architecture

```mermaid
flowchart LR
    A[🎙 Browser audio<br/>MediaRecorder] -->|multipart webm/wav/mp3| B[POST /voice/preview/]
    B --> C[Whisper<br/>faster-whisper base]
    C -->|transcript + language| D[LLM meal parser<br/>Gemini, JSON schema]
    D -->|items, quantities, units| E[Nutrition lookup<br/>indian_foods.json]
    E -->|table macros or LLM estimate| F[Preview JSON<br/>not saved]
    F --> G[Editable preview card]
    G -->|user edits + confirms| H[POST /voice/confirm/]
    H --> I[(MealLog + MealItems)]
```

The preview is deliberately **not persisted**. Speech recognition and LLM
extraction both make mistakes; the user sees every item, quantity and macro
value before anything is written, and the confirm endpoint recomputes totals
server-side from whatever they approved. See [docs/architecture.md](docs/architecture.md)
for the sequence diagram and error-handling strategy.

---

## Quick start

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # then set GEMINI_API_KEY (or OPENAI_API_KEY)
python manage.py migrate
python manage.py createsuperuser  # any user can log meals; superuser also gets /admin/
python manage.py runserver
```

The first voice request downloads the Whisper `base` checkpoint (~145 MB) from
Hugging Face and caches it locally. Audio decoding uses PyAV, which ships its own
FFmpeg, so no system FFmpeg install is required.

> **Tip:** `base` is fast but mis-hears some Hinglish food words. Setting
> `WHISPER_MODEL=small` in `.env` (~460 MB, a few seconds slower per clip) is
> noticeably more accurate on Hindi and Hinglish. Restart the server after
> changing it.

Run the tests:

```bash
python -m pytest
```

### Frontend

```bash
cd frontend
npm install
copy .env.example .env            # VITE_API_BASE_URL defaults to http://localhost:8000/api/v1
npm run dev                       # http://localhost:5173
```

Sign in with the user you created, tap the microphone, speak, review, confirm.

> Microphone access requires a secure context: `http://localhost` is fine; a LAN
> IP needs HTTPS.

---

## Deployment

The backend needs a persistent server (Whisper loads a model into memory and
transcription takes seconds), so it runs as a normal web service rather than on
serverless functions. The frontend is a static bundle and can go on any CDN.

**Backend — Render web service**

| Setting | Value |
| --- | --- |
| Root directory | `backend` |
| Build command | `pip install -r requirements.txt && python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"` |
| Start command | `python manage.py migrate --no-input && python manage.py warm_whisper --ignore-errors && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300` |

Downloading the Whisper checkpoint during the build bakes it into the image, and
`warm_whisper` loads it into memory before traffic arrives, so no user request
pays either cost. The long gunicorn timeout matters because transcription on a
shared CPU is far slower than on a laptop.

Required environment variables: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`,
`CORS_ALLOWED_ORIGINS` (the frontend URL), and `GEMINI_API_KEY`.
`DJANGO_ALLOWED_HOSTS` is optional on Render because `RENDER_EXTERNAL_HOSTNAME`
is trusted automatically.

**Sizing the instance.** Transcription is CPU-bound, and the wall-clock cost is
roughly the CPU cost divided by the fraction of a core the plan grants. Measured
on a 3.5-second clip:

| Settings | CPU seconds |
| --- | --- |
| `base`, beam 5, VAD on | 10.4 |
| `tiny`, beam 1, VAD off | 6.4 |

On a 0.1-CPU instance that is roughly 104 s and 64 s of waiting respectively,
which is slow enough that a proxy or client may give up first. Either run the
low-cost settings (`WHISPER_MODEL=tiny`, `WHISPER_BEAM_SIZE=1`,
`WHISPER_CPU_THREADS=1`, `WHISPER_VAD_FILTER=false`) or use an instance with at
least half a core, where the default settings answer in about twenty seconds.
`WHISPER_CPU_THREADS=1` matters on any throttled container: left on auto,
CTranslate2 spawns one thread per host core and they contend for a sliver of CPU.

**Frontend — Vercel**

Set the project root directory to `frontend`; Vercel then detects Vite and needs
no `vercel.json`. Add `VITE_API_BASE_URL` pointing at the backend, including the
`/api/v1` suffix. The variable is read at build time, so changing it requires a
redeploy.

**Free-tier caveats.** A free Render instance sleeps after inactivity and takes
roughly a minute to wake, and its SQLite file is recreated on every deploy, so
accounts and logged meals do not survive. Both are fine for a demo; a paid
instance with a managed Postgres database fixes them.

---

## API reference

All endpoints live under `/api/v1/`. Authentication is a JWT bearer token.

### Obtain a token

```http
POST /api/v1/auth/token/
Content-Type: application/json

{"username": "asha", "password": "••••••••"}
```

```json
{"access": "<jwt>", "refresh": "<jwt>"}
```

`POST /api/v1/auth/token/refresh/` with `{"refresh": "<jwt>"}` returns a new access token.

### `POST /api/v1/meal-log/voice/preview/`

Multipart upload, field `audio` (webm, wav, mp3, ogg or m4a; 10 MB max).
Returns the transcript, parsed items and totals. **Nothing is saved.**

```bash
curl -X POST http://localhost:8000/api/v1/meal-log/voice/preview/ \
  -H "Authorization: Bearer $ACCESS" \
  -F "audio=@clip.webm"
```

```json
{
  "transcript": "do roti aur ek katori dal",
  "language": "hi",
  "duration": 2.4,
  "meal_type": "lunch",
  "meal_type_source": "time_of_day",
  "source": "voice",
  "confidence": 0.92,
  "items": [
    {
      "name": "roti",
      "original_text": "do roti",
      "quantity": 2.0,
      "unit": "piece",
      "assumed_quantity": false,
      "macros": {"calories": 208.0, "protein_g": 6.2, "carbs_g": 36.0, "fats_g": 5.2},
      "macro_source": "table",
      "matched_food": "Roti",
      "confidence": 0.92
    },
    {
      "name": "dal",
      "original_text": "ek katori dal",
      "quantity": 1.0,
      "unit": "bowl",
      "assumed_quantity": false,
      "macros": {"calories": 150.0, "protein_g": 9.0, "carbs_g": 20.0, "fats_g": 3.5},
      "macro_source": "table",
      "matched_food": "Dal",
      "confidence": 0.92
    }
  ],
  "total_macros": {"calories": 358.0, "protein_g": 15.2, "carbs_g": 56.0, "fats_g": 8.7}
}
```

| Field              | Meaning                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `meal_type_source` | `transcript` when the LLM inferred it from words like "nashta"; `time_of_day` otherwise. |
| `assumed_quantity` | No quantity was spoken, so one serving was assumed. The UI shows a badge.               |
| `macro_source`     | `table` = scaled from `indian_foods.json`; `llm_estimate` = the model's own estimate.   |
| `matched_food`     | Which table row was used, so a wrong match is visible and editable.                     |

### `POST /api/v1/meal-log/voice/confirm/`

JSON body: the preview, after the user has edited it. `total_macros` is ignored
if sent; the server recomputes it from the items.

```json
{
  "meal_type": "lunch",
  "transcript": "do roti aur ek katori dal",
  "logged_at": "2026-09-06T13:05:00+05:30",
  "items": [
    {
      "name": "roti", "original_text": "do roti", "quantity": 3, "unit": "piece",
      "assumed_quantity": false, "macro_source": "table", "confidence": 0.92,
      "macros": {"calories": 312, "protein_g": 9.3, "carbs_g": 54, "fats_g": 7.8}
    }
  ]
}
```

Response `201 Created`:

```json
{
  "id": 17,
  "meal_type": "lunch",
  "logged_at": "2026-09-06T13:05:00+05:30",
  "source": "voice",
  "transcript": "do roti aur ek katori dal",
  "total_macros": {"calories": 312.0, "protein_g": 9.3, "carbs_g": 54.0, "fats_g": 7.8},
  "items": [
    {
      "id": 41, "name": "roti", "original_text": "do roti", "quantity": 3.0, "unit": "piece",
      "assumed_quantity": false, "macro_source": "table", "confidence": 0.92,
      "macros": {"calories": 312, "protein_g": 9.3, "carbs_g": 54, "fats_g": 7.8}
    }
  ],
  "created_at": "2026-09-06T07:35:12.412Z"
}
```

### `GET /api/v1/meal-log/?page=1&page_size=20`

The requesting user's logs, newest first, in DRF's page-number envelope
(`count`, `next`, `previous`, `results`). Each result has the shape above.

### Errors

Every error uses one envelope:

```json
{"error": {"code": "meal_parse_failed", "message": "No food items were detected in the transcript."}}
```

| Status | `code`                                    | When                                                        |
| ------ | ----------------------------------------- | ----------------------------------------------------------- |
| 400    | `validation_error` (+ `details`)          | Missing/empty/oversized/unrecognised audio; bad confirm body |
| 401    | `not_authenticated`                       | Missing or expired token                                     |
| 422    | `meal_parse_failed`                       | Silence, no food mentioned, or the LLM could not produce valid JSON after one repair attempt |
| 502    | `transcription_failed`, `llm_unavailable` | Whisper cannot load/decode; LLM provider down, rate-limited or unconfigured |

---

## Project structure

```
trackintake-voice-meal-ingestion/
├── README.md
├── docs/
│   ├── schema-analysis.md        # how the meal-log schema was inferred
│   └── architecture.md           # sequence diagram, error strategy, preview-then-confirm
├── backend/
│   ├── manage.py · requirements.txt · .env.example · pytest.ini
│   ├── config/                   # settings.py, urls.py, wsgi.py
│   └── meal_voice/
│       ├── models.py             # MealLog, MealItem + enums
│       ├── serializers.py        # upload validation, confirm payload, read models
│       ├── views.py              # preview / confirm / list / health
│       ├── exceptions.py         # domain errors + DRF handler (uniform envelope)
│       ├── admin.py · urls.py
│       ├── services/
│       │   ├── transcriber.py    # faster-whisper wrapper
│       │   ├── meal_parser.py    # prompt, JSON schema, pydantic models, LLM clients
│       │   ├── nutrition_lookup.py
│       │   ├── pipeline.py       # VoiceMealPipeline.run()
│       │   └── macros.py         # Macros value object
│       ├── data/indian_foods.json  # 60 foods with per-serving macros
│       └── tests/                # parser, nutrition, pipeline, API
└── frontend/
    └── src/
        ├── components/VoiceMealLogger/   # VoiceMealLogger, VoiceRecorder, MealPreviewCard
        ├── components/LoginForm/         # JWT sign-in
        ├── components/MealHistory/       # GET /meal-log/ listing
        ├── hooks/useVoiceRecorder.js     # MediaRecorder state machine
        ├── services/mealApi.js           # axios + JWT interceptor + refresh
        └── App.jsx · main.jsx
```

---

## Design decisions

**Preview, then confirm.** Two probabilistic stages (ASR, LLM) sit between the
user's voice and the database. Showing an editable preview keeps the user in
control, makes hallucinated items or mis-heard quantities harmless, and produces
a corrected dataset that can later be used to evaluate and tune the pipeline.

**Table first, LLM second.** The LLM estimates macros for every item, but when
the food is in `indian_foods.json` the curated value wins and is scaled to the
spoken portion (`2 roti`, `300 g rice`, `1 plate biryani`). Matching favours
precision: exact names and aliases, filler-word stripping ("ek katori dal" →
"dal"), and a strict fuzzy match for spelling variants. A name that merely
*contains* a known food ("quinoa salad") stays an LLM estimate rather than
being silently mapped to "salad". Every row carries `macro_source` and
`matched_food` so provenance is visible in the UI.

**Strict JSON from the LLM.** Every provider is asked for schema-constrained
JSON (Gemini via `response_json_schema`, OpenAI via JSON mode), then the reply
is validated with pydantic. Validation also
normalises what smaller models get wrong: Hindi number words, "katori"/"glass",
missing quantities (→ 1 serving, `assumed_quantity=true`), out-of-range
confidence. Invalid output triggers exactly one repair round-trip that shows the
model its previous reply and the validation error; a second failure is a 422.

**Provider abstraction.** `MealParser` depends on a two-method `LLMClient`
protocol with Google Gemini and OpenAI implementations. With
`LLM_PROVIDER=auto` the first provider that has an API key is used, Gemini
first; set `LLM_PROVIDER=openai` (or `gemini`) to force one. Adding another
provider is one class plus one registry entry. Tests inject a scripted client,
so the whole suite runs offline in under a minute.

**Whisper on CPU with `int8`.** The `base` checkpoint balances latency and
accuracy for short clips; an `initial_prompt` in the meal domain nudges Whisper
toward romanised Hinglish. The model is loaded lazily once per process.

**Uniform error envelope.** A DRF exception handler maps domain exceptions to
400/422/502 with stable `code` strings so the frontend can branch on them.

**Environment-driven configuration, no secrets in code.** `DJANGO_SECRET_KEY`
is required outside DEBUG; in DEBUG a random per-process key is generated.

---

## Limitations & future work

- **Nutrition table is small (60 foods) and approximate.** Values are typical
  per-serving figures, not lab-verified. Production should back this with a
  proper database (IFCT 2017, USDA FoodData Central) and per-user portion
  preferences.
- **Whisper `base` mis-hears some Hinglish food names** (e.g. "chole" → "chole"/"choley"/"cholay").
  Options: a fine-tuned or larger model, a post-ASR spelling normaliser fed
  from the food table, or letting the LLM see the top-N ASR hypotheses.
- **Latency.** CPU Whisper + an LLM round-trip takes a few seconds. Streaming
  partial transcripts and a warmed model pool would help; so would running the
  pipeline as a background task with polling for long clips.
- **Meal type from time of day** uses the server's `Asia/Kolkata` zone, not the
  user's; the UI lets them override it. A per-user timezone belongs on the user profile.
- **Single-language prompt.** Confidence is meal-level from the model; a
  per-item confidence (and highlighting of low-confidence rows) would improve
  the review step.
- **Auth is minimal**: username/password JWT with a refresh interceptor. There
  is no sign-up UI; accounts are created with `createsuperuser`.
- **No rate limiting or audio virus scanning** on the upload endpoint beyond
  size and container-signature checks.
- **Evaluation loop.** Confirmed logs store both the transcript and the
  corrected items, which is exactly the data needed to measure parser accuracy
  and hill-climb the prompt; that harness is not built yet.
