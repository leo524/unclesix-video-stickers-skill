#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("video_stickers.py")
SPEC = importlib.util.spec_from_file_location("video_stickers", MODULE_PATH)
assert SPEC and SPEC.loader
video_stickers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(video_stickers)


class VideoStickerTests(unittest.TestCase):
    def test_subtitle_gate_requires_embedded_or_confirmed_burned_in_subtitles(self) -> None:
        self.assertEqual(video_stickers.require_subtitle_source({"has_embedded_subtitles": True}), "embedded")
        self.assertEqual(video_stickers.require_subtitle_source({}, confirmed_burned_in=True), "burned-in")
        with self.assertRaises(SystemExit):
            video_stickers.require_subtitle_source({})

    def test_srt_is_normalized_into_timed_words(self) -> None:
        srt = """1
00:00:01,000 --> 00:00:02,000
先完成

2
00:00:02,200 --> 00:00:03,400
再优化
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            subtitle_path = Path(temp_dir) / "captions.srt"
            subtitle_path.write_text(srt, encoding="utf-8")
            transcript = video_stickers.parse_subtitle_file(subtitle_path)
        self.assertEqual(transcript["source"], "subtitles")
        self.assertEqual("".join(item["text"] for item in transcript["words"]), "先完成再优化")
        self.assertEqual(transcript["duration"], 3.4)

    def test_new_semantic_modes(self) -> None:
        cases = [
            ("以前需要两个工具现在只要一个Skill", "comparison"),
            ("第一步输入概念然后生成节点最后动态演示", "process-flow"),
            ("原书真正的知识要能解决问题", "source-quote"),
        ]
        for index, (text, expected_mode) in enumerate(cases):
            window = {"start": index * 8.0, "end": index * 8.0 + 4.0, "text": text}
            stickers = video_stickers.stickers_from_window(window, index, density="high")
            self.assertTrue(stickers)
            video_stickers.apply_sticker_options(stickers, "glass", "mixed", "cyan")
            self.assertEqual(stickers[0]["displayMode"], expected_mode)

    def test_high_density_scales_with_duration(self) -> None:
        self.assertEqual(video_stickers.density_sticker_limit({"duration": 60}, "high"), 22)
        self.assertGreater(video_stickers.density_sticker_limit({"duration": 180}, "high"), 22)

    def test_spoken_percentage_uses_compact_numeric_tile_value(self) -> None:
        self.assertEqual(video_stickers.normalize_metric_headline("百分之二十五"), "25%")
        sticker = {"type": "metric-card", "headline": "25%"}
        video_stickers.apply_sticker_options([sticker], "glass", "mixed", "cyan")
        self.assertEqual(sticker["displayMode"], "numeric")
        self.assertFalse(video_stickers.is_compact_numeric_value("百分之二十五"))

    def test_high_density_coverage_adds_a_second_opening_idea(self) -> None:
        stickers = [{"start": 0.38, "duration": 4.0, "type": "source-quote", "headline": "深度研究"}]
        windows = [
            {"start": 0.16, "end": 3.5, "text": "四个提示词让你深度研究一个领域", "words": [{"text": "四个提示词", "start": 0.16, "end": 0.9}]},
            {"start": 3.7, "end": 7.2, "text": "而不是看几篇文章刷几个视频问AI几个问题", "words": [{"text": "而不是", "start": 3.7, "end": 4.1}]},
            {"start": 7.3, "end": 10.0, "text": "不要只获得表面观点", "words": [{"text": "不要", "start": 7.3, "end": 7.6}]},
        ]
        video_stickers.ensure_high_density_coverage(stickers, windows, max_stickers=5)
        self.assertGreaterEqual(len([item for item in stickers if item["start"] <= 10]), 2)

    def test_opening_sticker_enters_within_first_second(self) -> None:
        stickers = [{"start": 3.2, "duration": 4.0, "headline": "开场观点"}]
        words = [{"text": "开场", "start": 0.16, "end": 0.52}]
        video_stickers.ensure_opening_sticker(stickers, words)
        self.assertLessEqual(stickers[0]["start"], 0.95)
        self.assertGreaterEqual(stickers[0]["start"], 0.35)
        self.assertTrue(stickers[0]["isOpening"])

    def test_new_modes_render_non_empty_pngs(self) -> None:
        from PIL import Image

        samples = [
            {"displayMode": "comparison", "label": "TOOL COMPARISON", "before": "2 个工具", "after": "1 个 Skill", "caption": "流程更简单"},
            {"displayMode": "process-flow", "label": "FLOW IN MOTION", "steps": ["输入概念", "生成节点", "动态演示"], "caption": "一步步看懂"},
            {"displayMode": "source-quote", "label": "SOURCE / QUOTE", "headline": "真正的知识，要能解决问题", "caption": "来自原书观点"},
        ]
        font = video_stickers.find_font()
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, sticker in enumerate(samples):
                image = video_stickers.render_sticker_image(sticker, 680, 280, "#38d5ff", font)
                self.assertIsInstance(image, Image.Image)
                self.assertIsNotNone(image.getbbox())
                image.save(Path(temp_dir) / f"sticker-{index}.png")

    def test_primary_modes_use_transparent_canvas_without_outer_frame(self) -> None:
        samples = [
            {"displayMode": "rectangle", "label": "KEY POINT", "headline": "内容更清晰", "caption": "信息更容易理解"},
            {"displayMode": "square", "label": "IDEA CHECK", "headline": "先整理重点", "caption": "再做关键判断"},
            {"displayMode": "comparison", "label": "WORKFLOW", "before": "旧方式", "after": "新方式", "caption": "信息更容易理解"},
        ]
        font = video_stickers.find_font()
        for sticker in samples:
            image = video_stickers.render_sticker_image(sticker, 520, 260, "#38d5ff", font)
            left_edge = image.getpixel((0, image.height // 2))
            right_edge = image.getpixel((image.width - 1, image.height // 2))
            self.assertEqual(image.getpixel((0, 0))[3], 0)
            self.assertEqual(image.getpixel((image.width - 1, image.height - 1))[3], 0)
            # A bright cyan perimeter would indicate that a hard outline returned.
            self.assertLess(max(left_edge[:3]), 120)
            self.assertLess(max(right_edge[:3]), 120)

    def test_related_pair_is_grouped_and_collision_free(self) -> None:
        window = {"start": 4.0, "end": 7.2, "text": "第一步输入概念然后生成3个节点最后动态演示"}
        stickers = video_stickers.stickers_from_window(window, 0, density="high")
        self.assertEqual(len(stickers), 2)
        video_stickers.apply_sticker_options(stickers, "glass", "mixed", "cyan")
        metadata = {"width": 1920, "height": 1080, "duration": 30, "fps": 30}
        video_stickers.apply_display_timing_and_layout(stickers, metadata, density="high", layout_mode="balanced")
        document = {"video": metadata, "options": {"density": "high", "displayMode": "mixed"}, "stickers": stickers}
        video_stickers.validate_active_sticker_layout(document)
        self.assertTrue(video_stickers.time_ranges_overlap(
            stickers[0]["start"], stickers[0]["start"] + stickers[0]["duration"],
            stickers[1]["start"], stickers[1]["start"] + stickers[1]["duration"],
        ))

    def test_balanced_layout_uses_visual_phrases(self) -> None:
        singles = [video_stickers.choose_group_positions(index, 1, False, layout_mode="balanced")[0] for index in range(8)]
        sides = ["right" if position.startswith("right") else "left" for position in singles]
        self.assertIn("left", sides)
        self.assertIn("right", sides)
        self.assertTrue(any(sides[index] == sides[index - 1] for index in range(1, len(sides))))
        self.assertFalse(all(sides[index] != sides[index - 1] for index in range(1, len(sides))))
        pairs = [video_stickers.choose_group_positions(index, 2, False, layout_mode="balanced") for index in range(6)]
        self.assertTrue(any(all(position.startswith("left") for position in pair) for pair in pairs))
        self.assertTrue(any(all(position.startswith("right") for position in pair) for pair in pairs))
        self.assertTrue(any(pair[0].startswith("left") != pair[1].startswith("left") for pair in pairs))

    def test_motion_presets_and_numeric_counter(self) -> None:
        numeric = {"displayMode": "numeric"}
        regular = {"displayMode": "rectangle"}
        self.assertEqual(video_stickers.choose_motion_preset(numeric, "smart"), "numeric-build")
        self.assertEqual(video_stickers.choose_motion_preset(regular, "smart"), "soft-slide")
        self.assertEqual(video_stickers.choose_motion_preset(numeric, "off"), "none")
        self.assertEqual(video_stickers.animated_numeric_headline("10", 0), "00")
        self.assertEqual(video_stickers.animated_numeric_headline("10", 1), "10")
        self.assertEqual(video_stickers.animated_numeric_headline("¥200+", 0.5), "¥100+")
        self.assertEqual(video_stickers.animated_numeric_headline("35-45", 0.5), "35-45")

    def test_numeric_motion_final_state_preserves_static_style(self) -> None:
        sticker = {
            "displayMode": "numeric",
            "motionPreset": "numeric-build",
            "label": "EFFICIENCY",
            "headline": "10",
            "unit": "X",
            "caption": "效率提升",
        }
        font = video_stickers.find_font()
        static = video_stickers.render_sticker_image(sticker, 460, 270, "#ffc857", font)
        state = video_stickers.sticker_motion_state(sticker, 2.0, 4.0)["numeric"]
        final_frame = video_stickers.render_sticker_image(sticker, 460, 270, "#ffc857", font, motion_state=state)
        self.assertEqual(static.tobytes(), final_frame.tobytes())

    def test_parallel_points_width_adapts_to_content(self) -> None:
        short = {"displayMode": "parallel-points", "points": ["复杂组件", "过度抽象", "不必要配置"]}
        long = {"displayMode": "parallel-points", "points": ["Token 消耗下降 22%", "成本节省 20%", "速度提升 27%"]}
        short_width = video_stickers.native_card_geometry(short, 1920, 1080)["w"]
        long_width = video_stickers.native_card_geometry(long, 1920, 1080)["w"]
        self.assertLess(short_width, long_width)
        self.assertLessEqual(long_width, int(1920 * 0.32))
        self.assertGreaterEqual(short_width, int(1920 * 0.21))

    def test_parallel_points_render_with_highlight_gap(self) -> None:
        sticker = {
            "displayMode": "parallel-points",
            "label": "BENCHMARK",
            "points": ["Token 消耗下降 22%", "成本节省 20%", "速度提升 27%"],
        }
        image = video_stickers.render_sticker_image(sticker, 500, 324, "#59df8b", video_stickers.find_font())
        self.assertIsNotNone(image.getbbox())

    def test_semantic_trigger_uses_word_timestamp(self) -> None:
        window = {
            "start": 4.0,
            "end": 10.0,
            "text": "先说一点背景第一步输入概念然后生成节点最后动态演示",
            "words": [
                {"text": "先说一点背景", "start": 4.0, "end": 5.8},
                {"text": "第一步", "start": 6.0, "end": 6.5},
                {"text": "输入概念", "start": 6.5, "end": 7.2},
                {"text": "然后生成节点", "start": 7.2, "end": 8.4},
                {"text": "最后动态演示", "start": 8.4, "end": 10.0},
            ],
        }
        sticker = video_stickers.sticker_from_window(window, 0, force=True)
        self.assertEqual(sticker["displayMode"] if "displayMode" in sticker else sticker["type"], "process-flow")
        self.assertEqual(sticker["start"], 6.12)

    def test_shared_font_fits_every_column_value(self) -> None:
        from PIL import Image, ImageDraw

        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        font = video_stickers.fit_shared_pil_font(
            ["未知项变多", "跑偏概率变大"],
            video_stickers.find_font(),
            48,
            180,
            min_size=20,
        )
        widths = [video_stickers.bbox_w(video_stickers.text_bbox(draw, text, font)) for text in ("未知项变多", "跑偏概率变大")]
        self.assertTrue(all(width <= 180 for width in widths))
        self.assertEqual(len(set([font.size, font.size])), 1)

    def test_square_caption_wrap_preserves_full_text(self) -> None:
        caption = "不清楚想要什么，就先看版本"
        lines = video_stickers.split_cjk_lines(caption, 8, max_lines=2)
        self.assertEqual("".join(lines), video_stickers.clean_text(caption))
        self.assertEqual(len(lines), 2)

    def test_square_headline_wrap_preserves_full_text(self) -> None:
        headline = "有价值，不等于马上做"
        lines = video_stickers.split_balanced_lines(headline)
        self.assertEqual("".join(lines), video_stickers.clean_text(headline))
        self.assertEqual(len(lines), 2)
        image = video_stickers.render_sticker_image(
            {"displayMode": "square", "label": "IDEA CHECK", "headline": headline, "caption": "先验证，再执行"},
            324,
            324,
            "#ffc857",
            video_stickers.find_font(),
        )
        self.assertIsNotNone(image.getbbox())

    def test_process_flow_long_steps_render_in_two_lines(self) -> None:
        steps = ["已知的已知", "知道自己不知道", "不知道自己知道", "未知的未知"]
        lines = [video_stickers.split_cjk_lines(step, 4, max_lines=2) for step in steps]
        self.assertTrue(all(len(line) <= 4 for group in lines for line in group))
        image = video_stickers.render_sticker_image(
            {"displayMode": "process-flow", "label": "FOUR QUADRANTS", "steps": steps, "caption": "从已知到完全未知"},
            653,
            302,
            "#59df8b",
            video_stickers.find_font(),
        )
        self.assertIsNotNone(image.getbbox())

    def test_balanced_wrap_never_splits_latin_word(self) -> None:
        lines = video_stickers.split_balanced_lines("可能比超长 Prompt 更有价值")
        self.assertEqual("".join(lines).replace(" ", ""), "可能比超长Prompt更有价值")
        self.assertTrue(any("Prompt" in line for line in lines))

    def test_numeric_value_uses_visible_ink_center(self) -> None:
        from PIL import Image, ImageDraw

        font = video_stickers.fit_pil_font("100", video_stickers.find_font(), 189, 242, min_size=58)
        x = video_stickers.centered_text_x_by_ink("100", font, 270)
        mask = Image.new("L", (270, 180))
        draw = ImageDraw.Draw(mask)
        box = draw.textbbox((0, 0), "100", font=font)
        draw.text((x - box[0], -box[1]), "100", font=font, fill=255)
        ink = mask.getbbox()
        self.assertIsNotNone(ink)
        self.assertLessEqual(abs(ink[0] - (270 - ink[2])), 1)

    def test_short_video_sticker_duration_stays_inside_video(self) -> None:
        stickers = [{"start": 1.2, "duration": 3.0, "displayMode": "rectangle", "type": "insight-card"}]
        metadata = {"width": 1920, "height": 1080, "fps": 30, "duration": 2.0}
        video_stickers.apply_display_timing_and_layout(stickers, metadata, density="high", layout_mode="balanced")
        self.assertLessEqual(stickers[0]["start"] + stickers[0]["duration"], metadata["duration"])


if __name__ == "__main__":
    unittest.main()
