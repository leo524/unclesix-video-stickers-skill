# Subtitle-First Production Pipeline

## Hard Input Gate

This skill accepts only videos that already have subtitles.

Check in this order:

1. Inspect media streams for an embedded subtitle track.
2. If no track exists, inspect several frames for visibly burned-in subtitles.
3. If neither is confirmed, stop and request a subtitled video.

Do not start Whisper, upload audio, generate design sheets, or plan stickers before this gate passes. A transcript by itself does not prove that the uploaded video contains subtitles.

## Source Inspection

Before planning stickers:

1. Probe resolution, duration, frame rate, pixel format, rotation, and audio streams.
2. Extract representative frames across the requested segment.
3. Mark active-picture bounds, face/body side, microphone, subtitle band, active gestures, and quiet regions.
4. Do not treat letterbox or pillarbox bars as usable sticker space.

## Subtitle Ingestion

Use subtitles as the primary content and timing source:

- Embedded subtitle track: extract the first appropriate track to SRT and parse its cue timing.
- Matching SRT/VTT: parse it directly.
- Burned-in subtitles: detect subtitle changes and read them with OCR/vision; request matching timed text if reliable OCR is unavailable.
- Normalized transcript: accept it only after subtitle presence has independently passed the gate.

Caption-level timings are sufficient for sticker planning. When exact word timing is absent, distribute timing only within the cue for local anchoring; keep the original cue boundaries as evidence.

## Audio Transcription Fallback

Audio transcription is opt-in and never substitutes for subtitles. Use it only after subtitle presence is confirmed and when OCR or cue timing needs recovery. Do not initiate downloads or uploads without explicit authorization.

The bundled CLI can call JieKou.AI ElevenLabs Scribe v2 with an HTTPS `cloud_storage_url` only when `--allow-audio-transcription` or an explicit cloud URL is supplied.

Keep credentials outside the skill directory in:

```text
~/.config/unclesix-video-stickers/config.json
```

The script accepts common word-timestamp shapes such as `words`, `transcription.words`, `data.words`, and `segments[].words`. Preserve the original subtitle cue timing even if local token timings are interpolated.

## Sticker Plan Schema

Preserve legacy fields and add the approved semantic fields:

```json
{
  "category": "focus",
  "variant": "underline",
  "displayMode": "text-shadow",
  "label": "KEY POINT",
  "headline": "先完成",
  "caption": "再优化",
  "start": 3.42,
  "duration": 3.8,
  "preferredRegion": "right-middle",
  "motionPreset": "focus-underline",
  "subtitleSource": "embedded",
  "evidence": {
    "kind": "subtitle-cue",
    "text": "先完成再优化",
    "start": 3.30,
    "end": 4.10
  }
}
```

For parallel content, use `points` with two or three supported rows. For numeric comparison, keep explicit `from`, `to`, `unit`, and `caption` fields when available.

## Semantic Routing

- One conclusion, instruction, warning, distinction, or key phrase → `focus`.
- Two or three equally ranked ideas → `parallel`.
- Compact percentage, multiplier, quantity, time, money, or before/after metric → `numeric`.

Choose variants by meaning:

- Focus: `rail`, `underline`, `contrast`.
- Parallel: `rails`, `numbered`, `checks`.
- Numeric: `percentage`, `multiplier`, `compare`.

Do not invent facts, list rows, or numeric targets to increase variety. The sticker should add structure rather than repeat subtitle text verbatim.

## Legacy `displayMode` Mapping

The Python CLI still accepts historical display modes. Keep them for backward compatibility and map them to the new taxonomy during planning:

| Legacy mode | New category | Typical variant |
| --- | --- | --- |
| `rectangle` | `focus` | `rail` |
| `square` | `focus` | `contrast` |
| `text-shadow` | `focus` | `underline` |
| `opinion` | `focus` | `contrast` |
| `source-quote` | `focus` | `contrast` |
| `parallel-points` | `parallel` | `numbered` or `rails` |
| `process-flow` | `parallel` | `numbered` |
| `numeric` | `numeric` | based on value |
| `comparison` | `numeric` | `compare` |

This mapping keeps old JSON and native fallback rendering valid. It does not mean the old renderer is the visual source of truth.

## Timing

- Anchor entrance to the actual semantic trigger word, usually 0.08–0.18 seconds after it begins.
- Keep the sticker visible long enough to read the full hierarchy.
- Use semantic events, not fixed intervals.
- Limit simultaneous information load: usually no more than two primary overlays in a vertical talking-head frame.
- Parallel rows share one group start but have independent row sequences.
- Numeric counter time must fit before the spoken segment moves on; shorten the count rather than leaving the final value unreadable.

## Design And Render Flow

1. Pass the subtitle gate and record `subtitleSource`.
2. Parse subtitle cues and map the exact subtitle band.
3. Generate three category sheets.
4. Review and select a variant.
5. Export tightly cropped transparent assets.
6. Composite still previews on real source frames without covering subtitles.
7. Adjust placement and legibility before animation.
8. Build the Remotion proof.
9. Inspect keyframes and audio synchronization.
10. Render the final requested segment or full video.

Use the native Pillow/ffmpeg route only as a fallback or for transcript/layout prototyping. The approved 3×3 design system and numeric lock behavior should be implemented in Remotion for final motion work.

## Validation Artifacts

Keep:

- source metadata and sampled frames;
- extracted or supplied subtitle file plus normalized timed text;
- `stickers.json` with evidence;
- generated prompts and untouched design sheets;
- selected crops with alpha;
- real-frame previews;
- Remotion source;
- versioned proof and final renders;
- frame captures used for QA.
