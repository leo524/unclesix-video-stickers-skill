# Approved Overlay Style Guide

## Design Principle

These are graphic annotations placed directly over a talking-head video. They are not cards and they are not subtitles. Readability must come from type weight, outline, contact shadow, hierarchy, and placement—not from a translucent panel.

## Canvas And Crop

- Work on transparent RGBA.
- Crop to the union of visible text, rails, marks, underline, outline, and shadow.
- At final video scale, keep about 8 px of safety padding beyond the visible effect bounds.
- Never export a full-frame sticker asset unless the animation itself genuinely requires full-frame coordinates.
- Preserve alpha through the render chain. Check corner pixels and the decoded overlay, not only the source PNG.

For deterministic raster assets, render at 4× the target size and perform one final Lanczos downsample. Avoid repeated scaling.

## Typography

- Use a real heavy Chinese face. On macOS, `Hiragino Sans GB W6` is a proven choice when available; `PingFang SC Semibold/Heavy` or `STHeiti` are acceptable measured fallbacks.
- Use a heavy condensed numeric face when the design calls for it. `Avenir Next Condensed Heavy` is a proven option on macOS.
- Do not simulate bold with an oversized stroke. The fill glyph must already have the intended weight.
- Measure actual rendered ink bounds for horizontal centering, line placement, and mixed-size alignment.
- Align number, unit, and caption to a coherent optical grid. Mixed font sizes must share intentional baselines or edge anchors.
- Use compact Chinese line-height. Do not rely on browser defaults.
- Keep wording short enough to preserve the design. Recompose long copy instead of shrinking it until it looks weak.

## Separation From Video

The default legibility stack is:

1. heavy foreground fill;
2. controlled dark outline sized for the final viewing scale;
3. tight contact shadow with small offset and modest blur;
4. deliberate placement over quieter image regions.

Do not add:

- full-card fills;
- semi-transparent background rectangles;
- glass blur;
- broad black glow;
- bright outer neon halos;
- a shadow so large that it reads as a panel.

Test on actual frames. A sticker that works only on a neutral mock background is not approved.

## Color

- Use white or warm-white for primary copy.
- Use one high-contrast accent family per sticker group; acid green/yellow is the established direction, but the exact hue may follow the video palette.
- Use black/dark ink on small opaque accent marks when needed.
- Accent fills are allowed only for semantic marks such as a rail, underline, number token, or check—not as a surrounding card.
- Maintain a clear primary/secondary hierarchy. Do not color every word.

## Category 1: Focus

Use for one decisive thought. The visual footprint should feel like a typographic annotation, not a title card.

### `focus / rail`

- Add one short vertical accent rail.
- Set the supporting phrase smaller and the decisive phrase larger/heavier.
- Align the rail to the visible text block, not to the asset canvas.
- Example structure: `先把问题` / `说清楚`.

### `focus / underline`

- Use one restrained underline or marker sweep below the decisive phrase.
- Keep the underline shorter than or optically matched to the highlighted text; do not create a long decorative bar.
- Example structure: `先完成` / `再优化`.

### `focus / contrast`

- Use size and color contrast to separate setup from conclusion.
- A quote cue is optional, but must remain secondary.
- Example structure: `真正重要的` / `是判断力`.

## Category 2: Parallel

Use for two or three equally ranked, transcript-backed points. All variants are one visual group with compact, consistent row geometry.

### `parallel / rails`

- Give each row a short independent accent rail.
- Keep rail length and text inset identical across rows.
- Example rows: `目标清楚`, `边界明确`, `结果可验`.

### `parallel / numbered`

- Use `01`, `02`, `03` as compact navigation anchors.
- Keep number width, baseline, and row gap consistent.
- Example rows: `少堆功能`, `多讲价值`, `快做验证`.

### `parallel / checks`

- Use compact check marks for criteria, validation, or completion.
- Keep check marks optically centered to the first text line.
- Example rows: `能解决`, `值得做`, `可复用`.

For every parallel variant:

- each row is a separate layer;
- row height and visual weight are consistent;
- the group is tightly cropped after all rows are composed;
- no row may appear clipped or drift horizontally after animation;
- do not invent a third point when only two are supported.

## Category 3: Numeric

The number is the first visual anchor. The unit and caption support it without turning the sticker into a dashboard card.

### `numeric / percentage`

- Use a compact target such as `72%`.
- Keep `%` intentionally sized and aligned; do not let it float or shrink excessively.
- Put the explanation on a separate compact line, such as `重复工作减少`.

### `numeric / multiplier`

- Use a compact target such as `3×`.
- Treat `×` as part of the designed value, with an optical baseline that matches the numeral.
- Use a short caption such as `验证速度`.

### `numeric / compare`

- Show a meaningful before/after pair such as `30 → 10`.
- Keep both values balanced and the arrow subordinate.
- Use small supporting labels only when required for comprehension, such as `耗时` and `分钟`.

For every numeric variant:

- fit the final approved digits first;
- align the unit, underline, and caption from measured ink bounds;
- keep the counter-phase type visually the same size as the final design;
- never count a range such as `35–45`;
- preserve signs and units such as `%`, `×`, `¥`, `+`, or `分钟` exactly at lock.

## Design Sheet Specification

Create three sheets in this order:

1. `01-focus`: all three focus variants.
2. `02-parallel`: rails, numbered, and checks.
3. `03-numeric`: percentage, multiplier, and compare.

Each sheet should:

- show the three variants at comparable scale;
- use transparent or neutral checkerboard presentation without implying a production background panel;
- include only enough annotation to distinguish variants;
- leave no huge unused canvas region;
- be reviewed for text accuracy before video compositing.

After approval, export each selected sticker as an individual tightly cropped transparent asset.

## Real-Frame Preview Specification

- Sample frames that represent bright, dark, busy, and face-adjacent conditions.
- Place the sticker at intended final size; do not enlarge only for review.
- Keep at least 48 px from active-picture edges at 1080×1920.
- Avoid face, microphone, subtitles, hands, and important UI.
- Compare at least two candidate positions when the source background is unstable.
- Review the preview in full frame and at 100% crop.

Reject and revise when any of the following appears:

- thin or distorted Chinese glyphs;
- fuzzy text caused by upscaling;
- inconsistent left edges or baselines;
- oversized outline or glow;
- large empty margins inside the sticker crop;
- a composition that needs a background panel to remain readable;
- text that looks centered mathematically but not optically.
