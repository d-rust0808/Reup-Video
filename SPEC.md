# Vietsub Quality Pipeline (AGY-only)

## Status

- Scope: future reup jobs only; existing rendered videos are not rewritten.
- Mode: design approved through `grill-me` on 2026-08-27. This document **supersedes** the previous DeepSeek-first draft.
- Primary goal: Vietnamese subtitles that sound spoken and native, follow the three UI styles exactly, and never publish garbage.

## Agreed Decisions

1. A low-quality Whisper transcript never reaches translation. The visual reup still completes without Vietsub/TTS and is marked `NEEDS_REVIEW`.
2. There is no stronger-model STT retry (`small` is not used). Whisper stays `base`, with `beam_size=5` and `condition_on_previous_text=True`.
3. STT quality uses hard-fail rules only. No 0–100 score.
4. Word-level timestamps are regrouped into sentence-sized cues **before** the quality gate and AGY. Pause `>= 450 ms` or terminal punctuation flushes a cue. Target phrase duration `0.65–4.5 s`. The same regrouped timeline is the only SRT for AGY, hardsub, and TTS.
5. The only subtitle translator is local AGY (Gemini). `translate_subtitles` must not call Google, pyVideoTrans CLI translation, DeepSeek, or Grok.
6. AGY receives the three style rules that already live in `xai_media_service.translate_cues` (Gốc / Kể chuyện / Vui nhộn). Hidden `auto` / `recap` duration switching is removed. Default style is `dub`.
7. **Gốc**: spoken Vietnamese, meaning-faithful, repair ASR from neighboring cues, keep original person (`tôi`/`cậu`), no narrator rewrite, no jokes.
8. **Kể chuyện**: third-person, restate the same beat, ~12 words, timed to that cue, no invented plot.
9. **Vui nhộn**: light Vietnamese-internet humor on the **current** beat only, not vulgar, no new plot.
10. If AGY is missing, times out, or fails validation, skip Vietsub/TTS. Do not silently fall back. Job stays a visual reup + `NEEDS_REVIEW`.
11. Post-AGY validation is all-or-nothing. Any of: leftover CJK; punctuation-only cue; `>15%` cues repeating the same phrase; `>8%` cues of 1–2 words; returned cue count ≠ input count → discard the whole Vietnamese SRT.
12. Lip-sync remains optional and **Gốc-only**. It must not re-slice cues back into 2.4 s / 8-word fragments.

## Why The Current Pipeline Fails

Evidence from 2026-08-27 outputs:

| Job | What happened |
| --- | --- |
| `job_291caf96` | Whisper `base` + `beam_size=1` + `condition_on_previous_text=False` looped `我把我的腳踏回頭我`. AGY translated fragments 1:1 → “Bước chân quay lại” ×4, single-word cues “Tôi / Đem”. Language guard still passed (no CJK). |
| `job_5f71d69e`, `job_64d77c17` | Same AGY engine produced natural Vietnamese and repaired ASR (`壁堪`→hốc tường, `椰子`→chủ nhà) when source sentences were coherent. |

Root causes, ranked:

1. STT garbage is translated instead of blocked.
2. Cue grouping is lip-sync oriented (`regroup_words_to_cues`: 2.4 s, 8 words, 0.28 s gap), so AGY never sees a full utterance on hard clips.
3. Default AGY prompt ignores `vietsub_style`. The three UI modes do not change the text.
4. Grok already has the correct style prompts but is not on the `translate_subtitles` path. DeepSeek `localize_script` has a cinematic prompt but is not default.
5. Google fallback (and tests that assert it) can publish machine-y Vietnamese. Forbidden.

## Target Pipeline

```text
source video
  -> extract speech WAV
  -> Whisper base (beam 5, condition_on_previous_text)
  -> sentence-aware cue regrouping (word timestamps)
  -> STT hard-fail gate
       fail -> visual-only + NEEDS_REVIEW (no AGY)
       pass -> AGY localization with selected style
  -> Vietnamese validation (all-or-nothing)
       fail -> visual-only + NEEDS_REVIEW
       pass -> fixed-timeline Vietnamese SRT
  -> optional TTS from that same SRT
       lipsync only if style == dub and user enabled it
  -> burn / mux the same SRT
  -> final reup output + quality_status=PASS
```

## Architecture And Data Contracts

### Single Source Of Truth

The validated, fixed-timeline Vietnamese cue list is the only source for:

- burned subtitles;
- toggleable/soft SRT;
- TTS input text;
- speech activity intervals used for BGM ducking;
- quality reporting.

Do not generate a second subtitle timeline after TTS. Do not run `recap_to_srt`.

Style prompt strings, validation thresholds, and regrouping constants live in one module (new `app/services/vietsub_rules.py`) so AGY, the gate, and tests cannot drift.

### Cue Contract

Cues entering AGY:

```json
{
  "index": 1,
  "start_time": 12.34,
  "end_time": 14.12,
  "duration_sec": 1.78,
  "source_text": "你看墙上写这么大的壁龛两个字"
}
```

AGY returns JSON `lines: [{index, text}]` with the **exact** same indexes. Timestamps are never accepted from the model.

### Style Contract

`vietsub_style` allowed values after resolve: `dub` | `narrator` | `funny`.

Aliases kept: `goc`/`gốc`/`original`/`faithful` → `dub`; `kechuyen`/`kể chuyện`/`story` → `narrator`; `vuinhon`/`vui` → `funny`.

`auto` and `recap` map to `dub` (no duration switching). UI stays three buttons. `VideoWorkbench` must not default to `'auto'`.

### Job Quality State

Keep the existing processing `status` state machine. Add orthogonal quality state:

- `PENDING`: not evaluated yet;
- `PASS`: clean Vietsub (and TTS if requested);
- `NEEDS_REVIEW`: STT gate failed, AGY missing/failed, or Vietnamese validation failed.

Persist:

- `quality_status TEXT NOT NULL DEFAULT 'PENDING'`;
- `quality_report TEXT NOT NULL DEFAULT '{}'`.

Report fields: `stt_model`, `stt_fail_reason`, `cue_count_in`, `cue_count_out`, `style`, `agy_model`, `translate_fail_reason`, artifact paths. No API keys.

A visual-only job is still `COMPLETED` with `quality_status=NEEDS_REVIEW`. It is not `FAILED`.

## STT

### Decode

- Model: `base` only (cached under `data/models`).
- `beam_size=5`, `best_of=5`, `condition_on_previous_text=True`, `vad_filter=True`, `word_timestamps=True`.
- Keep language lock via `_normalize_whisper_lang`.
- STT cache key must include decode params so old greedy caches are not reused.

### Sentence-Aware Regrouping

Replace `lipsync_service.regroup_words_to_cues` as the STT writer.

Rules:

- flush on pause `>= 450 ms`;
- flush after terminal punctuation `。！？!?…`;
- merge detached CJK particles (`吗呢吧嘛么啊呀啦呗了的地得`) into the previous cue;
- merge sub-350 ms fragments when the adjacent gap is small and there is no speaker-sized silence;
- target duration `0.65–4.5 s`;
- cap source phrase length (~42 CJK chars) so a cue stays readable;
- keep first-word start and last-word end as the immutable window;
- never merge across a long silence.

`_merge_fragmented_cues` at translate time becomes a no-op safety net (particles only), not a second grouping policy.

### Hard-Fail Gate (after regrouping)

Block Vietsub when any of:

- zero usable cues;
- punctuation-only transcript;
- repeated phrase or character loops covering `>15%` of cues (e.g. the same ≥4-character span in consecutive cues);
- `>12%` of cues shorter than 350 ms;
- `>8%` of cues containing only one CJK character;
- voiced coverage / VAD indicating mostly music or silence (existing empty/fallback STT statuses already count).

No 0–100 composite score. No retry with `small`.

## AGY Localization

### Prompt Behavior

AGY is translator + ASR repairer, not a screenwriter. One request per chunk.

Shared rules for every style:

- spoken Vietnamese, not literary;
- repair obvious ASR errors using neighboring cues (homophones, split words, 壁堪→壁龛);
- do not invent plot, medicine, religion, or off-screen facts;
- keep cue count and indexes;
- each `text` is speakable; never punctuation-only;
- no leftover CJK/Japanese/Korean when `target_lang=vi`;
- no markdown, no commentary.

Style-specific (verbatim intent from current Grok prompts):

**dub / Gốc**

- Follow the original line. Keep person (`tôi`/`mày`/`cậu` if dialogue).
- Meaning-faithful, not word-by-word.
- Do not narrate, do not add jokes.
- Prefer a complete spoken clause; length close to the source window.

**narrator / Kể chuyện**

- Third person. Restate that beat (`Lúc này…`, `Rồi nó…`).
- Must match the original cue’s moment; no scene jumping.
- Max ~12 words.

**funny / Vui nhộn**

- Witty Vietnamese-internet tone, mildly salty, never vulgar.
- Humor must sit on the current beat.
- One short joke beat allowed; no new plot.

### Chunking

- Chunk size 16–24 cues.
- Overlap: include previous 2 + next 2 cues as read-only context; model still returns only the inner indexes.
- Pass `video_title` as context, treated as untrusted data (never as instructions).
- Existing JSON schema `lines[{index, text}]` stays.
- Two attempts per chunk (current agy retry), then fail the job’s Vietsub.

### What Is Removed From The Translate Path

Delete or stop calling:

- `PyVideoTransService._translate_with_google`
- `PyVideoTransService._translate_with_cli` (translation task)
- `PyVideoTransService._translate_with_deepseek`
- Google/`deep_translator` inside `translate_subtitles`
- `xai_media_service.translate_cues` and `build_recap_lines` / `recap_to_srt` from `reup_service`
- `ai_scriptwriter_service.localize_script` from `translate_subtitles`

`SUBTITLE_TRANSLATOR` config: `agy` only. Other values behave as `agy` (no silent engine switch).

`apply_vietnamese_dubbing` still uses DeepSeek then Google for a leftover whole-file dub. Neutralize it: do not Google-translate published speech. If that path still runs, it must use AGY or skip.

Grok helpers that are **not** translation (`compact_vi_cue`, `LANG_DEFAULT_VOICE`) may stay.

## Vietnamese Validation

After AGY, before writing SRT:

- every cue `_translation_matches_target` (no CJK, not punctuation-only, not HTML/error blob);
- exact index coverage;
- repeated normalized phrase in `>15%` of cues → fail;
- cues with 1–2 tokens (after stripping punctuation) `>8%` → fail;
- empty/whitespace cue → fail.

On fail: delete any partial `*_vi.srt`, skip TTS, `quality_status=NEEDS_REVIEW`.

## TTS And Lip-Sync (unchanged except coupling)

- TTS reads the validated SRT only.
- If Vietsub is omitted, TTS is omitted. Original audio stays.
- Lip-sync fitting stays Gốc-only and must not regroup cues.
- Two-round DeepSeek shortening from the old SPEC is **out of scope**.

## Security And Boundary Defense

- Transcript and title are untrusted; wrap them as data, not instructions.
- Cap cue count, chars per cue, and prompt size.
- Validate JSON before file writes.
- Hash-only cache filenames; atomic SRT/cache writes.
- Do not log AGY/DeepSeek authorization material.

## Performance

- One Whisper `base` pass.
- AGY in bounded chunks with overlap.
- Cache valid AGY results at `data/cache/vietsub/<hash>.json`. Cache key: SHA-256 of regrouped source cues + timestamps + target lang + style + model + prompt version. Write only after validation.
- If AGY is missing, fail fast (no 90 s timeouts per chunk).

## User Experience

Queue badge:

- green: `Vietsub đạt`;
- review: `Cần kiểm tra Vietsub` with reason (`STT rác`, `Chưa có agy`, `AGY không đạt`).

UI copy for the three modes must match the style contract (Gốc is “sát ý, tiếng Việt nói”, not “dịch từng chữ Whisper”).

For `NEEDS_REVIEW`, the visual reup remains downloadable.

## Minimal Safe Diff

- `app/services/vietsub_rules.py` (new)
  - style aliases (no auto/recap);
  - AGY system/user prompt per style;
  - STT hard-fail + Vietnamese loop/short-cue checks;
  - regroup constants.
- `app/services/pyvideotrans_service.py`
  - Whisper decode params;
  - sentence regrouping on word timestamps;
  - STT gate;
  - `translate_subtitles` = AGY only;
  - remove Google/CLI/DeepSeek translate helpers from this path.
- `app/services/agy_cli_service.py`
  - accept `style`;
  - use `vietsub_rules` prompts;
  - chunk overlap.
- `app/services/reup_service.py`
  - drop recap rewrite;
  - resolve style without duration auto-switch;
  - persist quality outcome;
  - skip TTS when Vietsub omitted;
  - neutralize `apply_vietnamese_dubbing` Google/DeepSeek fallback.
- `app/services/xai_media_service.py`
  - `resolve_vietsub_style` no longer picks narrator/recap by duration;
  - stop using `translate_cues` / recap from the reup path.
- `app/services/lipsync_service.py`
  - do not drive STT grouping.
- `app/core/database.py`, `app/models/job.py`, `app/services/queue_manager.py`
  - `quality_status`, `quality_report`.
- `app/config.py`
  - `SUBTITLE_TRANSLATOR` default/docs = `agy` only.
- `frontend/src/components/ReupFxControls.jsx`, `VideoWorkbench.jsx`, `BatchQueue.jsx`
  - default `dub`; badge + review reason.
- tests
  - replace Google-fallback expectations;
  - gate, regroup, style prompt, all-or-nothing validation, no-fallback.

No unrelated visual, watermark, platform export, or preview-stream work.

## Verification Strategy

### STT And Gate

- detached `吗` joins the prior phrase;
- looping identical CJK spans (`>15%`) block Vietsub;
- single-character / sub-350 ms ratios block after regrouping;
- music-only / empty STT → visual-only `NEEDS_REVIEW`;
- cache keyed by beam/condition flags does not reuse greedy results;
- `tiny` still remaps to `base`; `small` is never selected.

### AGY Contract

- style `dub`/`narrator`/`funny` appear in the prompt; `auto`/`recap` never do;
- exact cue count/index enforcement;
- leftover CJK, punctuation-only, looped Vietnamese, 1–2 word flood rejected;
- missing `agy` binary → `NEEDS_REVIEW`, no Google call;
- monkeypatched Google/DeepSeek/Grok translators are not invoked from `translate_subtitles`.

### Timeline

- final cue start/end equal the regrouped STT timeline;
- burned SRT text equals TTS input text;
- no `previous_end` cascade from this change;
- lipsync off when style ≠ `dub`.

### Job State

- visual-only fallback ends `COMPLETED` + `quality_status=NEEDS_REVIEW`;
- successful Vietsub ends `PASS`;
- legacy rows migrate `PENDING`.

### Required Commands

```bash
./venv311/bin/python -m pytest -q tests/test_subtitle_language_guard.py tests/test_reup_quality_fixes.py
./venv311/bin/python -m pytest -q
```

Touched-file lint only if the repo’s critical rules are configured.

## Acceptance Criteria

- No future job burns a punctuation-only, mixed-Chinese, looped, or 1–2 word Vietnamese cue set.
- Gốc / Kể chuyện / Vui nhộn produce distinct AGY prompts; UI selection is the style that is sent.
- A garbage transcript cannot reach AGY.
- AGY failure cannot fall through to Google, DeepSeek, Grok, or pyVideoTrans CLI translation.
- The same validated SRT drives hardsub and TTS.
- Jobs that skip Vietsub remain downloadable and show `Cần kiểm tra Vietsub` with a reason.
