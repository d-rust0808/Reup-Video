# Vietsub Quality-First Pipeline

## Status

- Scope: future reup jobs only; existing rendered videos are not rewritten.
- Mode: design approved through `grill-me`; implementation is intentionally deferred.
- Primary goal: never publish meaningless Vietnamese subtitles or progressively desynchronized dubbing.

## Agreed Decisions

1. Vietnamese text must be rewritten to fit the original speech window. Subtitle and TTS cues never move later cues.
2. STT uses a multi-signal quality gate. A low-quality primary transcript is retried once with a stronger model.
3. If both STT attempts fail quality validation, the visual reup still completes without Vietsub/TTS and is marked `NEEDS_REVIEW`.
4. Word-level STT fragments are regrouped into complete dialogue phrases before DeepSeek receives them.
5. DeepSeek preserves the exact regrouped cue count, indexes, and timestamps. It may only rewrite cue text.
6. TTS uses two synthesis rounds. Round two rewrites only cues whose measured audio cannot fit naturally.
7. TTS speed-up is capped at `+15%`. No spoken audio is cut and no downstream cue is shifted.
8. If any cue still cannot fit after round two, discard the entire Vietnamese TTS track, retain the clean Vietsub, and use original audio.
9. DeepSeek retries at most twice with backoff and caches only outputs that pass deterministic validation.
10. DeepSeek failure does not fall back to lower-quality translation providers. The job keeps its visual output and is marked for review.

## Target Pipeline

```text
source video
  -> extract speech WAV
  -> primary STT (base)
  -> transcript quality gate
       pass -> continue
       fail -> stronger STT retry (small)
                 pass -> continue
                 fail -> visual-only output + NEEDS_REVIEW
  -> sentence-aware cue regrouping
  -> DeepSeek screenplay localization
  -> deterministic subtitle validation
       fail -> retry with backoff/cache rules
       fail again -> visual-only output + NEEDS_REVIEW
  -> fixed-timeline Vietnamese SRT
  -> TTS round 1 + measured durations
       all fit within +15% -> assemble fixed-timeline track
       overflow -> DeepSeek shortens only overflowing cues
                   -> TTS round 2 + measured durations
                   -> all fit: assemble fixed-timeline track
                   -> any fail: discard all TTS, keep Vietsub + original audio
  -> burn the same fixed-timeline SRT used by TTS
  -> final reup output
```

## Architecture And Data Contracts

### Single Source Of Truth

The validated, fixed-timeline Vietnamese cue list is the only source for:

- burned subtitles;
- TTS input text;
- speech activity intervals used for BGM ducking;
- quality reporting and debugging artifacts.

TTS must not generate or rewrite a second subtitle timeline. The current behavior that moves each following cue after a long generated clip must be removed from the synchronized TTS path.

### Cue Contract

Each cue passed to and returned by DeepSeek contains:

```json
{
  "index": 1,
  "start_time": 12.34,
  "end_time": 14.12,
  "duration_sec": 1.78,
  "source_text": "...",
  "max_words": 5,
  "max_chars": 25,
  "translated_text": "...",
  "speaker_id": "spk_1",
  "gender": "male",
  "emotion": "neutral"
}
```

DeepSeek output invariants:

- exact cue count and exact indexes;
- timestamps are not accepted from the model and cannot be changed;
- `translated_text` contains at least one alphanumeric character;
- no CJK remains when `target_lang=vi`;
- no HTML, markdown, explanations, API errors, or punctuation-only output;
- word and character budgets are respected;
- output is a single JSON object using the declared schema.

### Job Quality State

Keep the existing processing `status` state machine for backward compatibility. Add an orthogonal quality state:

- `PENDING`: quality pipeline not evaluated yet;
- `PASS`: clean Vietsub and requested TTS outcome succeeded;
- `WARNING`: Vietsub passed but TTS fell back to original audio;
- `NEEDS_REVIEW`: STT or localization could not produce trustworthy Vietsub.

Persist:

- `quality_status TEXT NOT NULL DEFAULT 'PENDING'`;
- `quality_report TEXT NOT NULL DEFAULT '{}'`.

The report contains model attempts, scores, fallback reason, cue counts, overflow counts, and artifact paths. It must not contain API keys or full request headers.

## STT Quality Gate

### Model Strategy

- Primary: configured model, default `base`.
- Retry: configured stronger model, default `small`.
- Retry only when the first transcript fails the gate; do not always run both models.
- Lock detected source language for the retry when the first detection is sufficiently confident; otherwise allow detection again.

### Scoring

Compute a score from `0` to `100` using persisted metrics:

- confidence/log probability: 35 points;
- fragmentation: 25 points;
- voiced-duration coverage: 15 points;
- repetition/compression anomalies: 15 points;
- expected-language consistency: 10 points.

Initial thresholds, configurable and covered by tests:

- `PASS`: score `>= 75` and no hard-fail rule;
- `RETRY`: score `< 75` or any retry-level anomaly;
- final `NEEDS_REVIEW`: stronger model also scores `< 75`.

Hard-fail signals include:

- zero usable cues;
- punctuation-only transcript;
- source-language mismatch across most text;
- repeated phrase/character loops above 15%;
- more than 12% of cues shorter than 350 ms after initial word grouping;
- more than 8% of cues containing only one CJK character after sentence regrouping;
- non-speech probability or transcript coverage indicating mostly music/silence.

The quality report must retain raw metric values so thresholds can be tuned without guessing.

## Sentence-Aware Cue Regrouping

Regroup before DeepSeek using word timestamps:

- flush on a meaningful pause, default `>= 450 ms`;
- flush after terminal punctuation;
- merge detached punctuation and Chinese particles into the adjacent phrase;
- merge sub-350 ms fragments when the adjacent gap is small;
- target phrase duration between roughly `0.65` and `4.5` seconds;
- cap source phrase length to prevent unreadable subtitle blocks;
- retain the first word start and last word end as the immutable cue window;
- preserve chronological order and renumber indexes once.

Never merge across a long silence or a detected speaker boundary.

## DeepSeek Localization

### Prompt Behavior

DeepSeek acts as translator, screenplay editor, and dubbing director in one context-aware request per manageable scene/chunk. It receives neighboring dialogue context but must return one result for every input cue.

For Vietnamese dubbing, initial budgets are:

- `max_words = max(1, floor(duration_sec * 3.2))`;
- `max_chars = max(6, floor(duration_sec * 14))`.

Budgets may be lowered during TTS round two based on measured overflow.

### Retry And Cache

- Total DeepSeek attempts: initial call plus two retries.
- Backoff: approximately `1.5s`, then `4s`, with small jitter.
- Retry on timeout, HTTP 429/5xx, malformed JSON, schema mismatch, or validation failure.
- Do not retry permanent authentication/configuration errors.
- Cache key: SHA-256 of normalized source cues, timestamps, target language, style, model, and prompt/schema version.
- Cache location: `data/cache/vietsub/<hash>.json`.
- Write cache atomically only after every cue passes validation.
- Never cache heuristic, partial, mixed-language, or punctuation-only output.

## Fixed-Timeline TTS

### Round One

1. Synthesize every validated Vietnamese cue.
2. Measure actual audio duration, not estimated character duration.
3. A cue fits when `actual_duration <= target_duration * 1.15 + 0.05s`.
4. Apply formant-preserving tempo adjustment capped at `1.15x`.
5. Do not trim audio and do not change cue start/end times.

### Round Two

For overflowing cues only, send DeepSeek:

- current Vietnamese text;
- target duration;
- measured duration;
- required reduction ratio;
- tighter word/character budget;
- neighboring cues for pronoun/context consistency.

Synthesize and measure those cues one final time.

### Final Fallback

If any cue remains invalid, fails synthesis, or exceeds the fit limit:

- discard all generated Vietnamese TTS clips for that job;
- do not produce a partially dubbed track;
- keep the validated fixed-timeline Vietsub;
- keep original audio/BGM behavior;
- set `quality_status=WARNING` and record `tts_fallback_reason`.

## Security And Boundary Defense

- Treat transcript/title text as untrusted data, never as model instructions.
- Cap cue count, characters per cue, and total prompt size.
- Validate all model JSON before file writes or TTS calls.
- Escape subtitle text correctly for SRT and FFmpeg/libass usage.
- Use hash-only cache filenames and validate resolved cache paths.
- Do not log authorization headers, API keys, or full sensitive payloads.
- Use atomic file replacement for SRT/cache artifacts so interrupted jobs cannot expose partial files.

## Performance And Resource Limits

- Stronger STT runs only after a failed primary quality gate.
- DeepSeek operates in bounded context-aware chunks with overlap/context metadata, then validates global index coverage.
- TTS round two processes only overflowing cues.
- Cache valid localization results to avoid repeat API cost on job retries.
- Limit concurrent external localization requests per worker to avoid rate-limit storms.
- Clean temporary TTS clips on success, fallback, cancellation, and exception.

## User Experience

Queue and job details expose a quality badge:

- green: `Vietsub đạt`;
- amber: `Vietsub đạt · Giữ audio gốc`;
- red/amber review: `Cần kiểm tra Vietsub`.

For `NEEDS_REVIEW`, the visual reup remains downloadable, but the UI states that Vietsub/TTS were intentionally omitted because transcript quality was insufficient.

## Minimal Safe Diff

Expected files and responsibilities:

- `app/services/pyvideotrans_service.py`
  - collect STT metrics;
  - run primary/stronger model selection;
  - sentence-aware regrouping;
  - deterministic transcript/localization validation;
  - valid localization cache.
- `app/services/ai_scriptwriter_service.py`
  - strict JSON contract;
  - bounded retry/backoff;
  - initial localization and overflow-only shortening APIs.
- `app/services/tts_service.py`
  - measured two-round synthesis;
  - fixed timestamps;
  - all-or-nothing TTS fallback;
  - remove progressive cue shifting.
- `app/services/lipsync_service.py`
  - `1.15x` maximum fitting policy;
  - reusable duration/budget helpers.
- `app/services/reup_service.py`
  - orchestrate quality outcomes;
  - burn the validated fixed SRT;
  - select original audio when TTS fallback occurs.
- `app/services/queue_manager.py`
  - persist quality outcome and logs without turning visual-only output into `FAILED`.
- `app/core/database.py`
  - additive migration for `quality_status` and `quality_report`.
- `app/models/job.py`
  - quality enums/DTO fields while preserving existing job statuses.
- `frontend/src/components/BatchQueue.jsx`
  - quality badge, fallback reason, and review messaging.
- tests
  - focused unit, integration, API contract, and regression coverage described below.

No unrelated visual, watermark, platform export, or preview-stream refactor belongs in this change.

## Verification Strategy

### STT And Regrouping

- detached `吗` joins the prior phrase;
- single-character fragments join correctly without crossing silence/speaker boundaries;
- clean transcripts do not trigger the stronger model;
- low score triggers exactly one stronger-model pass;
- two failed scores produce visual-only `NEEDS_REVIEW` output;
- music-only clips do not generate fake subtitles.

### DeepSeek Contract

- exact cue count/index enforcement;
- punctuation-only, CJK leakage, HTML, malformed JSON, and over-budget text are rejected;
- retry count and backoff are bounded;
- authentication failure is not retried;
- only validated results enter cache;
- cached results are invalidated by model, prompt version, style, or transcript changes.

### TTS And Timeline

- all final cue start/end times equal the validated source timeline;
- no `previous_end` cascade moves later cues;
- clips fitting within `1.15x` assemble successfully;
- overflow cues alone receive round-two rewrite;
- one remaining failure discards the whole TTS track;
- clean Vietsub remains burned when TTS falls back;
- original audio is retained without Vietnamese/audio language switching.

### Job State And Recovery

- visual-only fallback ends processing successfully with `quality_status=NEEDS_REVIEW`;
- TTS fallback ends with `quality_status=WARNING`;
- cancellation removes temporary SRT/audio/cache staging files;
- restart does not treat partial cache data as valid;
- legacy database rows migrate with `quality_status=PENDING`.

### Required Commands

Before completion:

```bash
./venv/bin/python -m pytest -q
pyright app tests
ruff check app tests
cd frontend && npm run lint && npm run build
```

If the repository-wide lint suite has pre-existing debt, the implementation must still pass the configured critical lint rules and all touched-file checks, with unrelated failures reported separately.

## Acceptance Criteria

- No future job emits a punctuation-only or mixed-Chinese Vietnamese cue.
- No TTS operation shifts later subtitle timestamps.
- A low-quality transcript cannot reach DeepSeek localization without retry/gating.
- A failed subtitle pipeline cannot silently publish nonsense; it produces a clear review state.
- A failed TTS fit cannot produce partial dubbing; the output uses clean Vietsub plus original audio.
- The same validated SRT text and timestamps drive both hardsub and TTS.
- All quality decisions and fallback reasons are persisted and visible to the user.
