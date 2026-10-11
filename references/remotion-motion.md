# Remotion Motion Specification

## Core Rule

The approved static sticker is the source of truth. Animation reveals it and may temporarily animate a numeric value, but the final held state must match the approved design.

Use frame-derived state only:

- `useCurrentFrame()` for current time;
- `interpolate()` for bounded opacity, translation, masks, and line progress;
- `spring()` for small settle motion;
- `Sequence` for independent rows and timed phases;
- `@remotion/media` for source video and audio.

Do not use CSS `animation`, CSS `transition`, timers, or nondeterministic runtime state.

## Timing Baseline

These values assume 30 fps. Scale by time when using another frame rate.

| Element | Entrance | Stagger | Settle | Exit |
| --- | ---: | ---: | ---: | ---: |
| Focus | 6–12 frames | — | 4–6 frames | 6–8 frames |
| Parallel row | 7–10 frames | 9 frames | 3–5 frames | group exits together |
| Numeric counter | 18–36 frames | — | lock in 2–4 frames | 6–8 frames |

Keep the readable hold longer than the entrance. Avoid repeated pulsing, floating, large bounce, and decorative rotation.

## Focus Motion

Choose motion that follows the variant:

- `rail`: reveal the rail with a vertical mask while the text slides 8–16 px and fades in.
- `underline`: reveal the text first, then sweep the underline left-to-right; add only a small settle.
- `contrast`: reveal the setup phrase, then bring in the decisive phrase with a restrained scale or slide.

Use a soft whoosh only when it does not compete with speech.

## Parallel Motion

All three visual variants must reveal rows sequentially.

Implementation contract:

1. Make every row an independent component or layer.
2. Start row `n` about `n × 9` frames after the group begins.
3. Animate each row from 8–14 px offset and zero opacity to its exact final coordinates.
4. Allow a small rebound or settle without changing the final row spacing.
5. Play one restrained tick per row.
6. After the last row is visible, hold the complete group.
7. Exit the group together; do not remove rows one by one unless explicitly requested.

The final row geometry must be identical to the approved static group.

## Numeric Two-Stage Motion

Numeric animation has two layers/phases:

### Phase A: Native counter

- Render only the changing value natively in Remotion.
- Use the same heavy condensed numeric family, apparent size, fill, outline, and contact shadow as the final asset.
- A temporary underline/progress line may reveal during the count.
- Preserve fixed width or optical centering so digit changes do not shake the layout.

Supported examples:

- `0 → 72%`
- `0 → 3×`
- `30 → 10`

Do not count a range such as `35–45`; reveal the intact range instead.

### Phase B: Design lock

- When the target is reached, transition to the original approved full sticker over 2–4 frames.
- Use a very short crossfade or snap with a subtle settle rebound.
- Play a small tick at lock; a restrained pop may be used at counter entry.
- After lock, show only the approved final sticker. Its digits, unit, underline, caption, outline, shadow, and crop are authoritative.

The counter and final asset must overlap closely enough that no size jump, baseline jump, or style swap is visible.

## Audio

- Preserve the original source voice track.
- Keep effects short and quiet relative to speech.
- Suggested mapping: focus `whoosh`, each parallel row `tick`, numeric entry `pop`, numeric lock `tick`.
- If a sound masks a consonant or keyword, lower it or remove it.
- Do not add music unless the user asks.

## Composition And Asset Rules

- Prefer individual transparent sticker assets over a full-frame raster overlay.
- If using a generated ImageGen concept as the final held state, preserve the original file and prompt. Rebuild exact production copy deterministically when generated text is inaccurate.
- Place assets with explicit pixel coordinates derived from source-frame review.
- Keep entry transforms local to the sticker. Do not move or zoom the source video.
- Use preloading and deterministic font loading before render.
- Version renders; do not overwrite the last accepted proof.

## Keyframe QA

Export or inspect frames at:

- one frame before entrance;
- mid-entrance;
- first fully settled frame;
- each parallel row's first settled frame;
- numeric target frame;
- first full design-lock frame;
- final hold frame;
- mid-exit.

Check that:

- no asset is clipped during transforms;
- alpha edges remain clean;
- final geometry matches the static sticker;
- counter and lock have no visible size jump;
- all parallel variants are sequential;
- sound cues line up with visible events;
- source audio remains synchronized.
