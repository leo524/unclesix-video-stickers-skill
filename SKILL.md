---
name: unclesix-video-stickers
description: Design and animate transparent information stickers for subtitle-bearing talking-head videos. Use when the user wants emphasis text, sequential parallel points, or animated numeric callouts placed over an already-subtitled local video; includes subtitle-driven planning, real-frame previews, Remotion animation, and a legacy Pillow/ffmpeg fallback. Reject videos without embedded, burned-in, or attached timed subtitles.
---

# Unclesix Video Stickers

## Outcome

Create compact, high-resolution overlay stickers for talking-head videos. The sticker must add hierarchy or evidence to the spoken content rather than behave like a subtitle.

The approved system has exactly three semantic categories and three visual variants per category:

| Category | Variant 01 | Variant 02 | Variant 03 |
| --- | --- | --- | --- |
| Focus / `focus` | vertical rail + hierarchy | underline emphasis | size/color contrast quote |
| Parallel / `parallel` | short rail rows | numbered rows | check rows |
| Numeric / `numeric` | percentage | multiplier | before/after count |

Read [references/style-guide.md](references/style-guide.md) before generating or rebuilding visual assets. Read [references/remotion-motion.md](references/remotion-motion.md) before animating. Read [references/pipeline.md](references/pipeline.md) for transcript planning, JSON fields, and legacy CLI compatibility.

## Subtitle Gate

Run this gate before transcription, design, or rendering:

1. Inspect media streams for an embedded subtitle track.
2. If none exists, inspect representative frames for clearly visible burned-in subtitles.
3. Accept a separate SRT/VTT only when it corresponds to subtitles already present in the uploaded video or is intended to be burned into the delivered video.
4. If no subtitle source is confirmed, stop and ask for a subtitled version. Do not download Whisper, upload audio, infer captions from speech, or continue with sticker planning.

When subtitles exist, use them as the primary semantic and timing source:

- Extract embedded SRT/ASS/VTT directly when possible.
- For burned-in subtitles, use OCR/visual reading across subtitle changes or request the matching SRT/VTT/transcript.
- Use audio transcription only as an explicitly authorized fallback for recovering timing or correcting OCR. It never bypasses the subtitle requirement.
- Record `subtitleSource` as `embedded`, `burned-in`, or `external-timed-text` in job metadata.

## Non-Negotiable Rules

- Use a genuine transparent alpha canvas. Never add a full-card background, glass panel, blur panel, dark wash, or semi-transparent rectangle.
- Crop every sticker to visible ink plus a small safety pad. Never deliver a mostly empty 1080-wide sticker image.
- Use genuinely heavy Chinese and numeric fonts. Do not fake weight with a large outline.
- Separate the sticker from changing video backgrounds with a controlled dark outline and a tight contact shadow. Do not use a large halo or glow.
- Align text by measured visible ink bounds and baselines, not only by the font em box.
- Keep the composition compact. Large unused blank regions, loose vertical rhythm, and arbitrary centering are failures.
- Do not cover the speaker's face, microphone, active hands, subtitles, or important product UI.
- Keep at least 48 px from the active picture edge in a 1080-wide or 1920-high vertical composition; scale the margin proportionally for other resolutions.
- Preserve the source video's framing and voice audio unless the user explicitly asks to change them.
- Production copy must be supported by the transcript or user-provided facts. Concept-sheet copy may be simulated, but label it as a style demonstration.
- Never cover, visually merge with, or duplicate the video's existing subtitles.

## Mandatory Review Order

Do not jump directly to an animated composite when designing a new look.

1. Show three category design sheets first. Each sheet contains the three variants of one category.
2. After the static direction is accepted, place the selected stickers on representative frames from the actual source video.
3. Check type weight, sharpness, optical alignment, empty space, safe areas, and readability over both bright and dark frame regions.
4. Only then build the Remotion animation and render a video fragment.

If the user has already approved the static design and asks only for animation changes, continue from the approved assets without restarting concept design.

## Capability Routing

- For visual exploration, use ImageGen or the image-generation app named by the user. Preserve every prompt and generated concept in the job folder.
- Do not trust generated raster text as the only production source. Rebuild copy deterministically when exact wording, typography, or localization matters.
- For animated preview or final delivery, use Remotion when requested or available. Use frame-driven animation through `useCurrentFrame`, `interpolate`, `spring`, `Sequence`, and `@remotion/media`.
- Use HyperFrames only when the user asks for an HTML composition or its GSAP/HTML workflow is materially useful. It is not required for the default Remotion path.
- The bundled Python CLI validates subtitle presence, reads embedded or supplied timed subtitles, plans overlays, extracts frames, and provides a legacy Pillow/ffmpeg render. Audio transcription is explicit fallback behavior. Its historical `displayMode` values are compatibility fields, not the visual taxonomy.

## Category Selection

Choose the semantic category first, then choose the visual variant based on the content and available safe area.

### Focus

Use for one conclusion, instruction, warning, distinction, or key phrase. Keep the copy to one or two short lines.

- `rail`: a short vertical accent rail anchors a two-level statement.
- `underline`: the main phrase is reinforced by a restrained underline or marker stroke.
- `contrast`: size and color separate the setup from the decisive phrase.

### Parallel

Use only for two or three transcript-backed ideas with the same grammatical level. Do not invent rows merely to use the style.

- `rails`: each row has a short independent accent rail.
- `numbered`: rows use `01`, `02`, `03` as navigation anchors.
- `checks`: rows use check marks for validated criteria or completed conditions.

Every row must be a separate animation layer and enter in sequence.

### Numeric

Use for compact explicit values where the number is the visual anchor.

- `percentage`: values such as `72%`.
- `multiplier`: values such as `3×`.
- `compare`: a before/after transition such as `30 → 10`.

Normalize spoken values into compact notation when the meaning is unambiguous. Do not count ranges such as `35–45` as a single animated value.

## Motion Contract

Animation must reveal the design; it must not redesign it.

- Motion may change opacity, transform, mask/reveal progress, line progress, and a numeric value during the counter phase.
- Motion must not change final font, weight, color, spacing, alignment, outline, shadow, or geometry.
- Focus stickers use a restrained 6–12 frame entrance, a small settle, a readable hold, and a short exit.
- Parallel stickers reveal rows one at a time with about a 9-frame stagger at 30 fps. Each row gets a slight translation/settle and a light tick; after all rows are visible, the group holds and exits together.
- Numeric stickers use a two-stage build: a native counter first, then a 2–4 frame lock to the original approved full sticker. Match the counter's visual size to the final digits so the lock does not jump.
- Preserve the approved full numeric sticker exactly after lock, including digits, unit, underline, caption, outline, and shadow.
- Use restrained sound effects: soft whoosh for focus, light tick per parallel row, small pop on numeric entry, and a light tick at numeric lock.
- Never use CSS animations or transitions in a Remotion composition. All state is derived from the current frame.

## End-to-End Workflow

1. Inspect the source video with `ffprobe`, verify the subtitle gate, and extract representative frames with `ffmpeg`.
2. Identify active-picture bounds, face/body position, microphone, exact subtitle band, gestures, and stable negative space.
3. Read embedded subtitles or ingest matching SRT/VTT. For burned-in subtitles, use OCR/visual reading or request matching timed text. Follow [references/pipeline.md](references/pipeline.md).
4. Build a subtitle-backed sticker plan with category, variant, copy, evidence, start time, duration, and preferred region.
5. Generate the three static category sheets and secure visual approval.
6. Produce real-frame composite previews using tightly cropped transparent assets.
7. Build or update the Remotion composition. Preserve the original source video and audio.
8. Render a short proof first, inspect key frames at entry/hold/exit, then render the requested final duration.
9. Keep prompts, source frames, design sheets, crops, plan JSON, previews, composition source, and versioned renders.

## Legacy CLI

The bundled CLI can still produce a transcript plan and a native fallback render:

```bash
python3 scripts/video_stickers.py run /path/to/video.mp4 \
  --out-dir out/video-stickers/job-name \
  --render-native \
  --person-side left \
  --layout-mode balanced \
  --display-mode mixed \
  --density medium \
  --motion smart \
  --output out/video-stickers/job-name/final.mp4
```

For visibly burned-in subtitles without an embedded subtitle stream, confirm the gate and provide matching timed text:

```bash
python3 scripts/video_stickers.py run /path/to/video.mp4 \
  --confirm-burned-in-subtitles \
  --subtitle-file /path/to/captions.srt \
  --density high
```

Treat its old renderer as a fallback. For the approved 3×3 look and numeric lock behavior, prefer deterministic transparent assets plus Remotion. Preserve old fields in `stickers.json`; enrich them with `category` and `variant` instead of breaking existing jobs.

Private transcription config remains outside the skill directory:

```text
~/.config/unclesix-video-stickers/config.json
```

Never write API keys into this skill, generated docs, scripts, or repositories.

## Deliverables

A complete design-and-motion job should retain:

- `design/01-focus.png`, `design/02-parallel.png`, `design/03-numeric.png`
- generated prompts or a prompt manifest
- representative source frames
- tightly cropped transparent sticker assets
- real-frame preview images
- transcript and `stickers.json`
- Remotion composition source
- short proof render and versioned final MP4
- a brief review note listing any compromises or unresolved collisions

## Validation

Before delivery:

- Inspect at least one bright-background frame and one dark-background frame at final scale.
- Confirm the uploaded video contains subtitles and `subtitleSource` is recorded before any sticker work begins.
- Confirm Chinese text is sharp and genuinely bold; no thin strokes, warped glyphs, or raster upscaling artifacts.
- Confirm baselines, optical centers, row spacing, underline length, and visible-ink bounds.
- Confirm every asset is tightly cropped and has real alpha.
- Confirm parallel rows enter sequentially in all three variants.
- Confirm numeric counters reach the intended target and lock to the exact approved static sticker within 2–4 frames.
- Confirm ranges do not count.
- Confirm stickers avoid faces, microphones, gestures, subtitles, and active-picture edges.
- Confirm voice audio is preserved and sound effects do not mask speech.
- Confirm the MP4 decodes and its resolution, frame rate, duration, and audio streams match the request.
- After modifying this skill, run the bundled unit tests, Python syntax checks, and the skill validator.
