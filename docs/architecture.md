# Architecture

## Components

| Component                      | Responsibility                                                                  |
| ------------------------------ | ------------------------------------------------------------------------------- |
| `useVoiceRecorder` (React)     | MediaRecorder state machine: idle → recording → processing; permission errors   |
| `VoiceMealPreviewView`         | Validates the upload (size, container signature) and runs the pipeline          |
| `Transcriber`                  | faster-whisper `base`, auto language detection, lazy model load, domain prompt  |
| `MealParser`                   | LLM call (Claude, OpenAI or Gemini) with JSON-schema output, pydantic validation, one repair retry |
| `NutritionLookup`              | Curated table match + portion scaling; falls back to the LLM's estimate         |
| `VoiceMealPipeline`            | Chains the three services and shapes the preview dict                           |
| `MealPreviewCard` (React)      | Editable table, live totals, provenance badges, confirm / re-record             |
| `VoiceMealConfirmView`         | Persists the edited preview atomically and recomputes totals                    |

## Sequence

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant FE as React app
    participant API as Django / DRF
    participant W as Whisper
    participant LLM as LLM (Claude / OpenAI / Gemini)
    participant NT as Nutrition table
    participant DB as Database

    U->>FE: Tap mic, speak "do roti aur ek katori dal"
    FE->>FE: MediaRecorder → Blob (webm/opus)
    FE->>API: POST /voice/preview/ (multipart, JWT)
    API->>API: Validate size + container signature (400 on failure)
    API->>W: transcribe(bytes)
    W-->>API: text, language, duration (502 on failure)
    API->>LLM: system prompt + transcript, JSON schema enforced
    LLM-->>API: JSON
    API->>API: pydantic validation; on failure one repair call (422 after 2nd failure)
    loop each item
        API->>NT: resolve(name, quantity, unit, llm_estimate)
        NT-->>API: table macros (scaled) or llm_estimate
    end
    API-->>FE: preview JSON (not saved)
    FE->>U: Editable table with badges + totals
    U->>FE: Edit rows, choose meal type, Confirm
    FE->>API: POST /voice/confirm/ (JSON, JWT)
    API->>DB: MealLog + MealItems in one transaction; totals recomputed
    DB-->>API: saved rows
    API-->>FE: 201 MealLog
    FE->>API: GET /meal-log/ (refresh history)
```

## Why preview-then-confirm

1. **User trust.** The person eating the meal is the only reliable oracle for
   what was on the plate. Showing the interpretation before saving turns a
   "black box that sometimes gets it wrong" into a fast assistant whose work is
   glanced at and approved.
2. **Hallucination guard.** LLMs occasionally invent an item ("with a side of
   salad"), double-count ("roti" and "chapati" as two rows) or guess wildly at a
   portion. None of that reaches the database unless the user lets it.
3. **ASR guard.** Whisper mis-hears Hinglish food names. Because the transcript
   is displayed verbatim, the user can immediately see *why* an item is wrong
   and re-record or fix the row.
4. **Explicit assumptions.** Missing quantities are filled with one serving and
   flagged; LLM-estimated macros are labelled; the table match is named. The
   user corrects assumptions rather than discovering them in a weekly report.
5. **Data flywheel.** Each confirmed log pairs a raw transcript with corrected
   structured data. That is a labelled dataset for measuring parser accuracy and
   improving the prompt or the food table over time.

The preview endpoint is idempotent and side-effect free, which also makes it
cheap to retry from the client.

## Error handling strategy

Every failure is converted into a single envelope,
`{"error": {"code", "message", "details?"}}`, by `meal_voice.exceptions.api_exception_handler`.

| Stage                    | Failure                                                     | Exception              | HTTP | Client behaviour                          |
| ------------------------ | ----------------------------------------------------------- | ---------------------- | ---- | ----------------------------------------- |
| Upload validation        | No file, empty file, > 10 MB, unrecognised container bytes  | DRF `ValidationError`  | 400  | Show the field message, stay on recorder  |
| Speech-to-text           | Model cannot be loaded/downloaded; PyAV cannot decode       | `TranscriptionError`   | 502  | "Speech-to-text unavailable", retry later |
| Speech-to-text           | Decoded fine but no speech (silence)                        | `MealParseError`       | 422  | Ask the user to re-record                 |
| LLM transport            | Auth/rate-limit/5xx/network, or no provider configured      | `LLMUnavailableError`  | 502  | "Parsing service unavailable"             |
| LLM content              | Invalid JSON or schema twice in a row; refusal; zero items  | `MealParseError`       | 422  | Ask the user to rephrase / re-record      |
| Confirm validation       | Bad unit, negative macros, empty items, unknown meal type   | DRF `ValidationError`  | 400  | Inline message on the preview card        |
| Auth                     | Missing/expired token                                       | DRF `NotAuthenticated` | 401  | Silent refresh, then sign-out event       |
| Anything else            | Programming error                                           | —                      | 500  | Django's default handler; never masked    |

Principles:

- **Fail fast before spending compute.** Container signatures are checked from
  the first 16 bytes so a text file never reaches Whisper.
- **Distinguish "our side is down" from "we could not understand you".** 502
  tells the client to retry later; 422 tells it the input needs to change.
- **One repair attempt, then stop.** The LLM sees its own invalid reply and the
  validation error once. Retrying more than that rarely helps and doubles cost.
- **Never trust client totals.** `total_macros` is recomputed from the items in
  `MealLog.recalculate_totals()` inside the confirm transaction.
- **Log with context, not with payloads.** Transcripts are logged at INFO for
  debugging; audio bytes and tokens never are.

## Runtime characteristics

- The Whisper model and the LLM client are created once per process
  (`get_pipeline()` is `lru_cache`d). The first request pays the model-load cost.
- Preview latency on a laptop CPU: roughly 1–3 s for Whisper `base` on a
  5-second clip plus the LLM round-trip.
- The frontend's axios client has a 120 s timeout for the preview call and a
  single-flight JWT refresh so concurrent 401s trigger one refresh request.
