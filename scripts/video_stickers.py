#!/usr/bin/env python3
"""Build transcript-driven video sticker overlays.

This script intentionally uses only Python stdlib plus local CLIs (`ffmpeg`,
`ffprobe`, and optionally a HyperFrames-compatible renderer).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path.home() / ".config/unclesix-video-stickers/config.json"


DEFAULT_JIEKOU_ENDPOINT = "https://api.jiekou.ai/v3/elevenlabs-scribe-v2"


DEFAULT_SCRIBE_PARAMS: dict[str, Any] = {
    "seed": 0,
    "diarize": False,
    "file_format": "other",
    "temperature": 0,
    "num_speakers": 1,
    "entity_detection": None,
    "tag_audio_events": True,
    "use_multi_channel": False,
    "diarization_threshold": 0.1,
    "timestamps_granularity": "word",
}


ACCENTS = {
    "blue": "#4b8cff",
    "green": "#58e27d",
    "gold": "#ffc857",
    "red": "#ff5470",
    "cyan": "#38d5ff",
    "purple": "#a78bfa",
    "orange": "#ff8a3d",
}

STYLES = ("glass", "solid-banner", "neon-metric", "editorial-cutout")
DISPLAY_MODES = (
    "rectangle",
    "square",
    "text-shadow",
    "numeric",
    "opinion",
    "parallel-points",
    "comparison",
    "process-flow",
    "source-quote",
    "mixed",
)
THEME_COLORS = tuple(ACCENTS.keys())
LAYOUT_MODES = ("auto", "right-only", "left-only", "balanced")
DENSITY_LEVELS = ("low", "medium", "high")
MOTION_MODES = ("off", "smart")

DENSITY_SETTINGS = {
    "low": {"default_max": 4, "window_seconds": 12.0, "near_existing": 10.0, "min_gap": 12.0, "gap_limit": 10.0, "span_limit": 14.0, "curated_separation": 3.2},
    "medium": {"default_max": 6, "window_seconds": 9.0, "near_existing": 7.0, "min_gap": 8.0, "gap_limit": 7.5, "span_limit": 11.5, "curated_separation": 2.5},
    "high": {"default_max": 22, "window_seconds": 3.0, "near_existing": 0.45, "min_gap": 1.2, "gap_limit": 3.3, "span_limit": 6.2, "curated_separation": 0.55},
}


POSITIONS = [
    "left-stack-1",
    "left-stack-2",
    "left-stack-3",
    "right-lower-1",
    "right-lower-2",
    "right-stack-1",
    "right-stack-2",
    "right-stack-3",
    "left-focus",
    "right-focus",
    "top-left",
    "top-right",
]


def run_cmd(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:  # noqa: BLE001 - CLI should explain how to recover.
        raise SystemExit("Missing required CLI: ffmpeg. Install it with Homebrew or `python3 -m pip install --user imageio-ffmpeg`.") from exc


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_cli(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"Missing required CLI: {name}")


def probe_video(video: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = run_cmd(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(video),
            ]
        )
        info = json.loads(result.stdout)
        video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        subtitle_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "subtitle"]
        fps = parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        return {
            "source": str(video.resolve()),
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "fps": fps,
            "duration": float(info.get("format", {}).get("duration") or video_stream.get("duration") or 0),
            "format": info.get("format", {}),
            "video_stream": video_stream,
            "subtitle_streams": subtitle_streams,
            "has_embedded_subtitles": bool(subtitle_streams),
        }
    return probe_video_with_ffmpeg(video)


def probe_video_with_ffmpeg(video: Path) -> dict[str, Any]:
    ffmpeg = find_ffmpeg()
    result = subprocess.run([ffmpeg, "-i", str(video)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = result.stderr + result.stdout
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    stream_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5}).*?(?:(\d+(?:\.\d+)?)\s*fps)?", output)
    duration = 0.0
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width = int(stream_match.group(1)) if stream_match else 0
    height = int(stream_match.group(2)) if stream_match else 0
    fps = float(stream_match.group(3)) if stream_match and stream_match.group(3) else 30.0
    return {
        "source": str(video.resolve()),
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "format": {},
        "video_stream": {},
        "subtitle_streams": [],
        "has_embedded_subtitles": bool(re.search(r"Subtitle:", output, re.IGNORECASE)),
        "probe": "ffmpeg-stderr-fallback",
    }


def require_subtitle_source(metadata: dict[str, Any], confirmed_burned_in: bool = False) -> str:
    if metadata.get("has_embedded_subtitles") or metadata.get("subtitle_streams"):
        return "embedded"
    if confirmed_burned_in:
        return "burned-in"
    raise SystemExit(
        "This skill only accepts videos that already contain subtitles. "
        "No embedded subtitle track was found. If subtitles are visibly burned into the picture, "
        "rerun with --confirm-burned-in-subtitles; otherwise provide a subtitled video."
    )


def parse_fps(raw: str | None) -> float:
    if not raw:
        return 30.0
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return 30.0
        return round(float(numerator) / denominator_value, 3)
    return float(raw)


def extract_audio(video: Path, audio_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )


def extract_embedded_subtitles(video: Path, subtitle_path: Path) -> None:
    ffmpeg = find_ffmpeg()
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_cmd(
            [
                ffmpeg,
                "-y",
                "-i",
                str(video),
                "-map",
                "0:s:0",
                "-c:s",
                "srt",
                str(subtitle_path),
            ]
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "An embedded subtitle track was detected but could not be converted to SRT. "
            "Provide it explicitly with --subtitle-file or pass a normalized --transcript."
        ) from exc


def subtitle_timestamp_seconds(value: str) -> float:
    normalized = value.strip().replace(",", ".")
    hours, minutes, seconds = normalized.split(":", 2)
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def subtitle_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*|[\u4e00-\u9fff]|[^\s]", text)


def parse_subtitle_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Subtitle file not found: {path}")
    raw = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    cue_pattern = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s+-->\s+"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
    )
    words: list[dict[str, Any]] = []
    cue_texts: list[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if cue_pattern.search(line)), None)
        if timing_index is None:
            continue
        match = cue_pattern.search(lines[timing_index])
        if match is None:
            continue
        start = subtitle_timestamp_seconds(match.group("start"))
        end = subtitle_timestamp_seconds(match.group("end"))
        text = " ".join(lines[timing_index + 1 :])
        text = html.unescape(re.sub(r"<[^>]+>|\{\\[^}]+\}", "", text)).strip()
        tokens = subtitle_tokens(text)
        if not tokens or end <= start:
            continue
        cue_texts.append(text)
        token_duration = (end - start) / len(tokens)
        for index, token in enumerate(tokens):
            token_start = start + index * token_duration
            words.append(
                {
                    "text": token,
                    "start": token_start,
                    "end": min(end, token_start + token_duration),
                    "confidence": None,
                }
            )
    if not words:
        raise SystemExit(f"No timed subtitle cues found in: {path}")
    return {
        "text": "\n".join(cue_texts),
        "words": words,
        "language": None,
        "duration": words[-1]["end"],
        "source": "subtitles",
    }


def upload_audio(audio_path: Path, config: dict[str, Any]) -> str:
    command = str(config.get("upload", {}).get("command") or "").strip()
    provider = str(config.get("upload", {}).get("provider") or "tmpfiles").strip()
    if not command and provider == "tmpfiles":
        return upload_to_tmpfiles(audio_path)
    if not command:
        raise SystemExit(
            "No --cloud-storage-url supplied and config upload.command is empty. "
            "Set upload.provider to tmpfiles, or set upload.command to a command "
            "that uploads the audio and prints one HTTPS URL."
        )
    prepared = command.replace("{file}", shlex.quote(str(audio_path)))
    result = subprocess.run(
        prepared,
        shell=True,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = result.stdout.strip().splitlines()[-1].strip()
    if not url.startswith("https://"):
        raise SystemExit("upload.command must print an HTTPS URL to stdout.")
    return url


def upload_to_tmpfiles(audio_path: Path) -> str:
    filename = audio_path.name
    boundary = uuid.uuid4().hex
    audio_data = audio_path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + audio_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "unclesix-video-stickers/1.0",
    }
    request = urllib.request.Request("https://tmpfiles.org/api/v1/upload", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - CLI should explain how to recover.
        raise SystemExit(f"Temporary upload failed: {exc}") from exc

    if result.get("status") != "success":
        raise SystemExit(f"Temporary upload failed: {result}")
    url = str(result.get("data", {}).get("url") or "")
    parts = url.replace("http://", "").replace("https://", "").split("/", 1)
    if len(parts) == 2 and parts[0] == "tmpfiles.org":
        direct_url = f"https://tmpfiles.org/dl/{parts[1]}"
    else:
        direct_url = url.replace("http://", "https://")
    if not direct_url.startswith("https://"):
        raise SystemExit(f"Temporary upload did not return an HTTPS URL: {result}")
    print(f"Uploaded temporary audio URL: {direct_url}", file=sys.stderr)
    return direct_url


def call_scribe(cloud_storage_url: str, config: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    jiekou = config.get("jiekou", {})
    api_key = str(jiekou.get("api_key") or os.environ.get("JIEKOU_API_KEY") or "").strip()
    if not api_key or api_key.startswith("REPLACE_"):
        raise SystemExit(
            "Missing JieKou API key. Put it in ~/.config/unclesix-video-stickers/config.json "
            "as jiekou.api_key, or set JIEKOU_API_KEY."
        )

    endpoint = str(jiekou.get("endpoint") or DEFAULT_JIEKOU_ENDPOINT)
    params = dict(DEFAULT_SCRIBE_PARAMS)
    params.update(jiekou.get("request_defaults") or {})
    if not params.get("diarize"):
        params.pop("diarization_threshold", None)
    params = {key: value for key, value in params.items() if value is not None}
    params["cloud_storage_url"] = cloud_storage_url

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        (out_dir / "scribe-error.txt").write_text(error_body, encoding="utf-8")
        raise SystemExit(f"Scribe request failed: HTTP {exc.code}. Error body saved to {out_dir / 'scribe-error.txt'}") from exc
    except Exception as exc:  # noqa: BLE001 - keep the CLI error actionable.
        raise SystemExit(f"Scribe request failed: {exc}") from exc

    write_json(out_dir / "transcript.raw.json", payload)
    normalized = normalize_transcript(payload)
    write_json(out_dir / "transcript.words.json", normalized)
    return normalized


def normalize_transcript(payload: dict[str, Any]) -> dict[str, Any]:
    words = find_words(payload)
    if not words:
        raise SystemExit("Scribe response did not include word-level timestamps.")

    normalized_words = []
    for item in words:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("word") or item.get("value") or ""
        start = item.get("start") if item.get("start") is not None else item.get("start_time")
        end = item.get("end") if item.get("end") is not None else item.get("end_time")
        if text == "" or start is None or end is None:
            continue
        normalized_words.append(
            {
                "text": str(text),
                "start": float(start),
                "end": float(end),
                "confidence": item.get("confidence"),
            }
        )

    if not normalized_words:
        raise SystemExit("Scribe response had a words list, but no usable start/end timestamps.")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    text = payload.get("text") or payload.get("transcript") or data.get("text") or join_words(normalized_words)
    return {
        "text": str(text),
        "words": normalized_words,
        "language": payload.get("language") or payload.get("language_code") or data.get("language"),
        "duration": normalized_words[-1]["end"],
    }


def find_words(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        words = value.get("words")
        if isinstance(words, list) and words:
            return words
        segments = value.get("segments")
        if isinstance(segments, list):
            merged = []
            for segment in segments:
                segment_words = find_words(segment)
                if segment_words:
                    merged.extend(segment_words)
            if merged:
                return merged
        for key in ("transcription", "data", "result", "response"):
            nested = value.get(key)
            found = find_words(nested)
            if found:
                return found
    return None


def join_words(words: list[dict[str, Any]]) -> str:
    result: list[str] = []
    for word in words:
        token = str(word.get("text", ""))
        if not result:
            result.append(token)
        elif re.match(r"^[,.;:!?，。！？、：；)]$", token):
            result[-1] += token
        elif re.search(r"[\u4e00-\u9fff]$", result[-1]) or re.match(r"^[\u4e00-\u9fff]", token):
            result.append(token)
        else:
            result.append(" " + token)
    return "".join(result)


def make_stickers(
    transcript: dict[str, Any],
    metadata: dict[str, Any],
    max_stickers: int,
    person_side: str = "center",
    style: str = "glass",
    display_mode: str = "rectangle",
    theme_color: str = "blue",
    density: str = "medium",
    layout_mode: str = "auto",
    motion: str = "smart",
) -> dict[str, Any]:
    words = transcript["words"]
    density_config = DENSITY_SETTINGS.get(density, DENSITY_SETTINGS["medium"])
    stickers: list[dict[str, Any]] = []
    windows = build_windows(words, seconds=float(density_config["window_seconds"]))
    for window in windows:
        if len(stickers) >= max_stickers:
            break
        if any(abs(float(sticker["start"]) - window["start"]) < float(density_config["near_existing"]) for sticker in stickers):
            continue
        candidates = stickers_from_window(window, len(stickers), density=density)
        stickers.extend(candidates[: max(0, max_stickers - len(stickers))])

    curated = curated_stickers(transcript, max_stickers, min_separation=float(density_config["curated_separation"]))
    for sticker in curated:
        if len(stickers) >= max_stickers:
            break
        if any(abs(float(existing["start"]) - float(sticker["start"])) < float(density_config["near_existing"]) for existing in stickers):
            continue
        stickers.append(sticker)

    if not stickers and windows:
        sticker = sticker_from_window(windows[0], 0, force=True)
        if sticker:
            stickers.append(sticker)

    ensure_opening_sticker(stickers, words)
    if density == "high":
        ensure_high_density_coverage(stickers, windows, max_stickers)
    apply_sticker_options(stickers, style=style, display_mode=display_mode, theme_color=theme_color, motion=motion)
    apply_display_timing_and_layout(stickers, metadata, person_side=person_side, density=density, layout_mode=layout_mode)

    return {
        "video": {
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "fps": metadata.get("fps"),
            "duration": metadata.get("duration"),
        },
        "options": {
            "style": style,
            "displayMode": display_mode,
            "themeColor": theme_color,
            "density": density,
            "layoutMode": layout_mode,
            "personSide": person_side,
            "motion": motion,
            "subtitleSource": metadata.get("subtitle_source"),
        },
        "transcript": "transcript.words.json",
        "safeZones": {
            "subtitleBand": {"x": 0, "y": 0.78, "w": 1, "h": 0.22},
            "faceAvoidance": True,
        },
        "stickers": stickers,
    }


def density_sticker_limit(metadata: dict[str, Any], density: str) -> int:
    duration = max(0.0, float(metadata.get("duration") or 0))
    base = int(DENSITY_SETTINGS[density]["default_max"])
    if density == "high":
        return min(42, max(base, math.ceil(duration / 4.6)))
    if density == "medium":
        return min(18, max(base, math.ceil(duration / 10.0)))
    return min(10, max(base, math.ceil(duration / 22.0)))


def ensure_opening_sticker(stickers: list[dict[str, Any]], words: list[dict[str, Any]]) -> None:
    """Make the first transcript-backed sticker enter within the first second."""
    if not stickers or not words:
        return

    first = min(stickers, key=lambda item: float(item.get("start", 0)))
    if float(first.get("start", 0)) <= 0.95:
        first["isOpening"] = True
        return

    first_word_start = float(words[0].get("start", 0))
    opening_start = min(0.82, max(0.35, first_word_start + 0.22))
    first["start"] = round(opening_start, 2)
    first["isOpening"] = True


def ensure_high_density_coverage(
    stickers: list[dict[str, Any]], windows: list[dict[str, Any]], max_stickers: int
) -> None:
    """Fill meaningful high-density gaps without turning the timeline into a metronome."""
    if not stickers or not windows:
        return

    def candidate_for(window: dict[str, Any]) -> dict[str, Any] | None:
        candidates = stickers_from_window(window, len(stickers), density="high")
        candidate = candidates[0] if candidates else sticker_from_window(window, len(stickers), force=True)
        if not candidate:
            return None
        candidate["semanticScore"] = max(1, int(candidate.get("semanticScore") or semantic_signal_score(clean_text(window["text"]))))
        candidate["clusterId"] = f"coverage-{window['start']:.2f}"
        candidate["allowCoexist"] = False
        candidate["coverageFill"] = True
        return candidate

    def add_candidate(window: dict[str, Any]) -> bool:
        if len(stickers) >= max_stickers:
            return False
        candidate = candidate_for(window)
        if not candidate:
            return False
        start = float(candidate["start"])
        if any(abs(start - float(existing.get("start", 0))) < 0.7 for existing in stickers):
            return False
        stickers.append(candidate)
        return True

    # The opening hook alone is not enough for a dense opening argument.
    while len([item for item in stickers if float(item.get("start", 0)) <= 10.0]) < 2 and len(stickers) < max_stickers:
        existing = [float(item.get("start", 0)) for item in stickers]
        candidates = []
        for window in windows:
            if float(window["start"]) >= 10.0:
                continue
            proposed = sticker_from_window(window, len(stickers), force=True)
            if proposed and all(abs(float(proposed["start"]) - start) >= 0.7 for start in existing):
                candidates.append((semantic_signal_score(clean_text(window["text"])), window))
        if not candidates:
            break
        _, selected = max(candidates, key=lambda item: (item[0], float(item[1]["start"])))
        if not add_candidate(selected):
            break

    # Keep semantic coverage flowing through longer explanations, especially in high mode.
    max_gap = 5.8
    while len(stickers) < max_stickers:
        starts = sorted(float(item.get("start", 0)) for item in stickers)
        duration = max(float(window["end"]) for window in windows)
        anchors = [0.0, *starts, duration]
        gaps = [(left, right) for left, right in zip(anchors, anchors[1:]) if right - left > max_gap]
        if not gaps:
            break
        left, right = max(gaps, key=lambda gap: gap[1] - gap[0])
        midpoint = (left + right) / 2
        eligible = [
            window
            for window in windows
            if left + 0.7 < float(window["start"]) < right - 0.7
        ]
        if not eligible:
            break
        selected = min(eligible, key=lambda window: abs(float(window["start"]) - midpoint))
        if not add_candidate(selected):
            break


def apply_sticker_options(stickers: list[dict[str, Any]], style: str, display_mode: str, theme_color: str, motion: str = "smart") -> None:
    for index, sticker in enumerate(stickers):
        sticker["style"] = style
        sticker["displayMode"] = choose_mixed_display_mode(sticker, index) if display_mode == "mixed" else display_mode
        if display_mode == "parallel-points" and len(sticker_points(sticker)) < 2:
            fallback_points = list(dict.fromkeys(str(sticker.get(key) or "").strip() for key in ("headline", "caption")))
            sticker["points"] = [point for point in fallback_points if point][:3]
        if theme_color in ACCENTS:
            sticker["themeColor"] = theme_color
        sticker["motionPreset"] = choose_motion_preset(sticker, motion)


def choose_motion_preset(sticker: dict[str, Any], motion: str) -> str:
    if motion == "off":
        return "none"
    if str(sticker.get("displayMode") or "") == "numeric":
        return "numeric-build"
    return "soft-slide"


def choose_mixed_display_mode(sticker: dict[str, Any], index: int) -> str:
    semantic_type = str(sticker.get("type") or "")
    if semantic_type == "comparison-card":
        return "comparison"
    if semantic_type == "process-flow":
        return "process-flow"
    if semantic_type == "source-quote":
        return "source-quote"
    if len(sticker_points(sticker)) >= 2:
        return "parallel-points"
    if (semantic_type == "metric-card" or re.search(r"[$¥￥%\d]", str(sticker.get("headline") or ""))) and is_compact_numeric_value(str(sticker.get("headline") or "")):
        return "numeric"
    if is_opinion_sticker(sticker):
        return "opinion"
    cycle = ("rectangle", "square", "text-shadow", "rectangle", "square", "opinion")
    return cycle[index % len(cycle)]


def is_compact_numeric_value(value: str) -> bool:
    return bool(re.fullmatch(r"[$¥￥]?\s*\d+(?:[.,]\d+)?(?:\s?(?:%|x|X|倍|万|亿|年|G|GB|元|小时|分钟|天|次|个|种|步|k|K|w|W|\+))?", clean_text(value)))


def is_opinion_sticker(sticker: dict[str, Any]) -> bool:
    text = " ".join(str(sticker.get(key) or "") for key in ("label", "headline", "caption"))
    label = str(sticker.get("label") or "").upper()
    if any(token in label for token in ("TAKEAWAY", "OPINION", "VERDICT", "BOTTOM LINE")):
        return True
    return any(token in text for token in ("不要", "必须", "关键", "真正", "别", "才", "优先", "最重要"))


def sticker_points(sticker: dict[str, Any]) -> list[str]:
    raw = sticker.get("points")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()][:3]
    if isinstance(raw, str):
        return [item.strip() for item in re.split(r"[|；;\n]", raw) if item.strip()][:3]
    return []


def sticker_steps(sticker: dict[str, Any]) -> list[str]:
    raw = sticker.get("steps")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()][:4]
    return []


def apply_display_timing_and_layout(
    stickers: list[dict[str, Any]],
    metadata: dict[str, Any],
    person_side: str = "center",
    density: str = "medium",
    layout_mode: str = "auto",
) -> None:
    if not stickers:
        return
    stickers.sort(key=lambda item: float(item.get("start", 0)))
    width = int(metadata.get("width") or 1920)
    height = int(metadata.get("height") or 1080)
    vertical = height > width
    max_visible = 2 if vertical or layout_mode in {"left-only", "right-only"} or person_side in {"left", "right"} else 3
    groups = group_stickers_for_display(stickers, max_visible=max_visible, vertical=vertical, density=density)
    video_duration = float(metadata.get("duration") or 0)

    for group_index, group in enumerate(groups):
        next_group_start = float(groups[group_index + 1][0].get("start", 0)) if group_index + 1 < len(groups) else video_duration
        last_original_end = max(float(item.get("start", 0)) + float(item.get("duration", 3)) for item in group)
        related_group = len(group) > 1 and all(bool(item.get("allowCoexist")) for item in group)
        if related_group:
            group_end = max(last_original_end, float(group[-1].get("start", 0)) + 3.2)
        else:
            group_end = max(last_original_end, float(group[-1].get("start", 0)) + 2.8)
        if next_group_start and not related_group:
            overlap_tail = 0.55 if density == "high" else 0.15
            group_end = min(group_end, max(float(group[-1].get("start", 0)) + 2.6, next_group_start + overlap_tail))
        if video_duration:
            group_end = min(group_end, video_duration - 0.2)

        positions = choose_group_positions(group_index, len(group), vertical, person_side, layout_mode=layout_mode)
        for slot_index, sticker in enumerate(group):
            sticker["position"] = positions[min(slot_index, len(positions) - 1)]
            minimum = 2.6 if density == "high" else 3.0
            duration = max(minimum, group_end - float(sticker.get("start", 0)))
            if video_duration:
                duration = min(duration, max(0.5, video_duration - float(sticker.get("start", 0))))
            sticker["duration"] = round(duration, 2)
    enforce_active_limit(stickers, 2 if vertical else 3)
    apply_collision_safe_layout(stickers, width, height)


def enforce_active_limit(stickers: list[dict[str, Any]], max_visible: int) -> None:
    ordered = sorted(stickers, key=lambda item: float(item.get("start", 0)))
    for index, sticker in enumerate(ordered):
        start = float(sticker.get("start", 0))
        active = [
            item
            for item in ordered[:index]
            if float(item.get("start", 0)) + float(item.get("duration", 3)) > start + 0.05
        ]
        while len(active) >= max_visible:
            cluster = sticker.get("clusterId")
            unrelated = [item for item in active if not cluster or item.get("clusterId") != cluster]
            victim = min(unrelated or active, key=lambda item: float(item.get("start", 0)))
            victim_start = float(victim.get("start", 0))
            victim["duration"] = round(max(0.5, start - victim_start - 0.08), 2)
            active.remove(victim)


def group_stickers_for_display(stickers: list[dict[str, Any]], max_visible: int, vertical: bool, density: str = "medium") -> list[list[dict[str, Any]]]:
    density_config = DENSITY_SETTINGS.get(density, DENSITY_SETTINGS["medium"])
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for sticker in stickers:
        start = float(sticker.get("start", 0))
        if not current:
            current = [sticker]
            continue
        previous = current[-1]
        previous_start = float(previous.get("start", 0))
        group_span = start - float(current[0].get("start", 0))
        gap = start - previous_start
        gap_limit = 9 if vertical else float(density_config["gap_limit"])
        span_limit = 13 if vertical else float(density_config["span_limit"])
        same_cluster = bool(sticker.get("clusterId")) and sticker.get("clusterId") == previous.get("clusterId")
        semantic_pair = (
            density == "high"
            and bool(sticker.get("allowCoexist"))
            and bool(previous.get("allowCoexist"))
            and gap <= min(2.2, gap_limit)
        )
        should_start_new = len(current) >= max_visible or group_span > span_limit or not (same_cluster or semantic_pair)
        if should_start_new:
            groups.append(current)
            current = [sticker]
        else:
            current.append(sticker)
    if current:
        groups.append(current)
    return groups


def choose_group_positions(group_index: int, group_size: int, vertical: bool, person_side: str = "center", layout_mode: str = "auto") -> list[str]:
    if vertical:
        if group_size == 1:
            rhythm = ["top-left", "top-left", "top-right", "top-right", "top-left", "top-right"]
            return [rhythm[group_index % len(rhythm)]]
        patterns = [["top-left", "top-right"], ["top-right", "top-left"]]
        return patterns[group_index % len(patterns)]

    side_mode = layout_mode
    if layout_mode == "auto":
        if person_side == "right":
            side_mode = "left-only"
        elif person_side == "left":
            side_mode = "right-only"
        else:
            side_mode = "balanced"

    if side_mode == "left-only":
        if group_size >= 3:
            patterns = [
                ["left-stack-1", "left-stack-2", "left-stack-3"],
                ["left-stack-1", "left-stack-2", "left-lower-1"],
                ["left-focus", "left-lower-1", "left-lower-2"],
            ]
        elif group_size == 2:
            patterns = [["left-stack-1", "left-stack-2"], ["left-focus", "left-lower-1"], ["left-lower-1", "left-lower-2"]]
        else:
            patterns = [["left-focus"], ["left-lower-1"], ["left-stack-1"]]
        return patterns[group_index % len(patterns)]

    if side_mode == "right-only":
        if group_size >= 3:
            patterns = [
                ["right-stack-1", "right-stack-2", "right-stack-3"],
                ["right-stack-1", "right-stack-2", "right-stack-3"],
                ["right-focus", "right-stack-1", "right-stack-2"],
            ]
        elif group_size == 2:
            patterns = [["right-stack-1", "right-stack-2"], ["right-stack-1", "right-stack-2"], ["right-stack-1", "right-stack-2"]]
        else:
            patterns = [["right-focus"], ["right-stack-1"], ["right-stack-2"]]
        return patterns[group_index % len(patterns)]

    if group_size >= 3:
        patterns = [
            ["left-stack-1", "right-stack-1", "left-stack-2"],
            ["right-stack-1", "left-stack-1", "right-stack-2"],
            ["left-stack-1", "right-stack-1", "right-stack-2"],
            ["right-stack-1", "left-stack-1", "left-stack-2"],
        ]
    elif group_size == 2:
        patterns = [
            ["left-stack-1", "left-stack-2"],
            ["left-stack-1", "right-stack-1"],
            ["right-stack-1", "right-stack-2"],
            ["right-stack-2", "left-stack-1"],
            ["left-stack-2", "left-lower-1"],
            ["right-stack-1", "left-stack-2"],
        ]
    else:
        patterns = [
            ["left-stack-1"],
            ["left-stack-2"],
            ["right-stack-1"],
            ["right-stack-2"],
            ["right-focus"],
            ["left-focus"],
            ["left-stack-1"],
            ["right-lower-1"],
        ]
    return patterns[group_index % len(patterns)]


def apply_collision_safe_layout(stickers: list[dict[str, Any]], width: int, height: int) -> None:
    vertical = height > width
    for sticker in stickers:
        sticker.pop("layoutX", None)
        sticker.pop("layoutY", None)

    placed: list[dict[str, Any]] = []
    safe_top = int(height * (0.04 if vertical else 0.065))
    safe_bottom = int(height * (0.82 if vertical else 0.88))
    gap = max(18, int(height * (0.018 if vertical else 0.028)))

    for sticker in sorted(stickers, key=lambda item: float(item.get("start", 0))):
        original_position = str(sticker.get("position", "left-stack-1"))
        start = float(sticker.get("start", 0))
        end = start + float(sticker.get("duration", 3))
        active = [item for item in placed if time_ranges_overlap(start, end, item["start"], item["end"])]

        chosen: dict[str, int] | None = None
        chosen_position = original_position
        for position in collision_position_candidates(original_position, vertical):
            sticker["position"] = position
            geom = native_card_geometry(sticker, width, height)
            packed = pack_geometry_below_active(geom, active, safe_top, safe_bottom, gap)
            if packed and not any(rects_overlap(packed, item["geom"], gap) for item in active):
                chosen = packed
                chosen_position = position
                break

        if chosen is None:
            sticker["position"] = original_position
            chosen = native_card_geometry(sticker, width, height)
            chosen["y"] = max(safe_top, min(chosen["y"], safe_bottom - chosen["h"]))

        sticker["position"] = chosen_position
        sticker["layoutX"] = int(chosen["x"])
        sticker["layoutY"] = int(chosen["y"])
        placed.append({"start": start, "end": end, "geom": chosen})


def collision_position_candidates(position: str, vertical: bool) -> list[str]:
    if vertical:
        order = [position, "top-left", "top-right"]
    elif position.startswith("right"):
        order = [
            position,
            "right-stack-1",
            "right-stack-2",
            "right-stack-3",
            "right-lower-1",
            "right-lower-2",
            "right-focus",
            "left-stack-1",
            "left-stack-2",
            "left-lower-1",
        ]
    else:
        order = [
            position,
            "left-stack-1",
            "left-stack-2",
            "left-stack-3",
            "left-lower-1",
            "left-lower-2",
            "left-focus",
            "right-stack-1",
            "right-stack-2",
            "right-lower-1",
        ]
    return list(dict.fromkeys(order))


def pack_geometry_below_active(
    geom: dict[str, int],
    active: list[dict[str, Any]],
    safe_top: int,
    safe_bottom: int,
    gap: int,
) -> dict[str, int] | None:
    packed = dict(geom)
    packed["y"] = max(safe_top, packed["y"])
    for _ in range(len(active) + 2):
        blockers = [item["geom"] for item in active if rects_overlap(packed, item["geom"], gap)]
        if not blockers:
            return packed if packed["y"] + packed["h"] <= safe_bottom else None
        packed["y"] = max(blocker["y"] + blocker["h"] + gap for blocker in blockers)
        if packed["y"] + packed["h"] > safe_bottom:
            return None
    return None


def time_ranges_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end) - 0.05


def rects_overlap(a: dict[str, int], b: dict[str, int], margin: int = 0) -> bool:
    return not (
        a["x"] + a["w"] + margin <= b["x"]
        or b["x"] + b["w"] + margin <= a["x"]
        or a["y"] + a["h"] + margin <= b["y"]
        or b["y"] + b["h"] + margin <= a["y"]
    )


def curated_stickers(transcript: dict[str, Any], max_stickers: int, min_separation: float = 2.5) -> list[dict[str, Any]]:
    words = transcript["words"]
    rules = [
        {
            "terms": ["贴片效果", "信息贴片"],
            "type": "insight-card",
            "label": "AUTO STICKERS",
            "headline": "自动贴片",
            "caption": "视频内容生成",
            "accent": "blue",
            "duration": 5.2,
        },
        {
            "terms": ["Codex加Hyperframes", "Codex", "Hyperframes"],
            "type": "insight-card",
            "label": "TOOL STACK",
            "headline": "Codex + HF",
            "caption": "自动生成贴片",
            "accent": "green",
            "duration": 5.0,
        },
        {
            "terms": ["花了一天", "一天"],
            "type": "metric-card",
            "label": "BUILD TIME",
            "headline": "1",
            "unit": "天",
            "caption": "手搓出来",
            "accent": "gold",
            "duration": 4.5,
        },
        {
            "terms": ["很多博主都这么在做", "博主都这么在做"],
            "type": "insight-card",
            "label": "CREATOR TREND",
            "headline": "博主都在用",
            "caption": "增强信息密度",
            "accent": "blue",
            "duration": 4.0,
        },
        {
            "terms": ["一直很羡慕", "看着一直很羡慕", "很羡慕"],
            "type": "insight-card",
            "label": "VISUAL HOOK",
            "headline": "效果很抓眼",
            "caption": "让人想停留",
            "accent": "gold",
            "duration": 4.0,
        },
        {
            "terms": ["字体的大小", "排版", "布局"],
            "type": "insight-card",
            "label": "LAYOUT",
            "headline": "字体排版",
            "caption": "布局一起调",
            "accent": "blue",
            "duration": 4.6,
        },
        {
            "terms": ["安全区域", "显示的安全区域"],
            "type": "insight-card",
            "label": "SAFE AREA",
            "headline": "安全区域",
            "caption": "不贴脸不贴边",
            "accent": "green",
            "duration": 4.8,
        },
        {
            "terms": ["反复地调试", "反复地调整", "反复调试"],
            "type": "insight-card",
            "label": "ITERATION",
            "headline": "反复调试",
            "caption": "节奏和安全区",
            "accent": "blue",
            "duration": 4.2,
        },
        {
            "terms": ["兼容竖屏和横屏", "竖屏和横屏"],
            "type": "insight-card",
            "label": "RESPONSIVE",
            "headline": "横竖兼容",
            "caption": "版式自动切",
            "accent": "gold",
            "duration": 4.8,
        },
        {
            "terms": ["横屏效果会更好", "横屏效果"],
            "type": "insight-card",
            "label": "BEST FORMAT",
            "headline": "横屏更稳",
            "caption": "两边留空间",
            "accent": "green",
            "duration": 4.8,
        },
        {
            "terms": ["两边留下来的区域", "两边留下来的", "留下来的区域"],
            "type": "insight-card",
            "label": "SIDE SPACE",
            "headline": "两边留白",
            "caption": "贴片更好放",
            "accent": "green",
            "duration": 4.2,
        },
        {
            "terms": ["不太容易贴到你的脸上", "不容易贴到你的脸上", "贴到你的脸上"],
            "type": "insight-card",
            "label": "FACE SAFE",
            "headline": "避开脸部",
            "caption": "观看更舒服",
            "accent": "red",
            "duration": 4.0,
        },
        {
            "terms": ["特别适合口播", "口播的场景"],
            "type": "insight-card",
            "label": "USE CASE",
            "headline": "适合口播",
            "caption": "信息点自动补强",
            "accent": "red",
            "duration": 4.8,
        },
        {
            "terms": ["Windows版本终于更新", "Windows"],
            "type": "insight-card",
            "label": "CODEX UPDATE",
            "headline": "Windows 版",
            "caption": "终于更新",
            "accent": "blue",
            "duration": 4.2,
        },
        {
            "terms": ["两个你们最爱的功能", "两个"],
            "type": "metric-card",
            "label": "NEW FEATURES",
            "headline": "2",
            "unit": "个功能",
            "caption": "这次都来了",
            "accent": "gold",
            "duration": 4.0,
        },
        {
            "terms": ["ComputerUse", "Computer Use", "Computer"],
            "type": "insight-card",
            "label": "COMPUTER USE",
            "headline": "操控电脑",
            "caption": "组件任务直接做",
            "accent": "green",
            "duration": 4.8,
        },
        {
            "terms": ["画板去画一个草稿", "画板"],
            "type": "insight-card",
            "label": "EXAMPLE",
            "headline": "画板草稿",
            "caption": "调用应用搞定",
            "accent": "green",
            "duration": 4.0,
        },
        {
            "terms": ["手机端远程操控", "手机端"],
            "type": "insight-card",
            "label": "MOBILE REMOTE",
            "headline": "手机远程",
            "caption": "Windows 也支持",
            "accent": "blue",
            "duration": 5.0,
        },
        {
            "terms": ["扫码配对连接", "扫码"],
            "type": "insight-card",
            "label": "PAIRING",
            "headline": "扫码配对",
            "caption": "连接方式一样",
            "accent": "gold",
            "duration": 4.0,
        },
        {
            "terms": ["任意的地点", "任意地点"],
            "type": "insight-card",
            "label": "WEB CODING",
            "headline": "随时开工",
            "caption": "任意地点写代码",
            "accent": "green",
            "duration": 4.5,
        },
        {
            "terms": ["一视同仁", "终于一视同仁"],
            "type": "insight-card",
            "label": "TAKEAWAY",
            "headline": "一视同仁",
            "caption": "Windows 用户补齐",
            "accent": "red",
            "duration": 4.5,
        },
    ]
    stickers: list[dict[str, Any]] = []
    used_times: list[float] = []
    for rule in rules:
        timing = find_terms_timing(words, rule["terms"])
        if not timing:
            continue
        start, end, evidence_text = timing
        if any(abs(start - used) < min_separation for used in used_times):
            continue
        stickers.append(
            {
                "start": round(max(0, start - 0.2), 2),
                "duration": rule["duration"],
                "type": rule["type"],
                "position": POSITIONS[len(stickers) % len(POSITIONS)],
                "label": rule["label"],
                "headline": rule["headline"],
                "unit": rule.get("unit", ""),
                "caption": rule["caption"],
                "accent": rule["accent"],
                "evidence": f"transcript {start:.2f}-{end:.2f}: {evidence_text[:80]}",
            }
        )
        used_times.append(start)
        if len(stickers) >= max_stickers:
            break
    return stickers


def find_terms_timing(words: list[dict[str, Any]], terms: list[str]) -> tuple[float, float, str] | None:
    normalized_terms = [clean_text(term).lower() for term in terms]
    for index, word in enumerate(words):
        collected: list[dict[str, Any]] = []
        text = ""
        for word_inner in words[index : min(len(words), index + 32)]:
            collected.append(word_inner)
            text += str(word_inner.get("text", ""))
            normalized_text = clean_text(text).lower()
            matched_term = next((term for term in normalized_terms if term and term in normalized_text), None)
            if matched_term:
                exact_words = narrow_match_words(collected, matched_term)
                return (
                    float(exact_words[0]["start"]),
                    float(exact_words[-1]["end"]),
                    clean_text("".join(str(item.get("text", "")) for item in exact_words)),
                )
    return None


def narrow_match_words(words: list[dict[str, Any]], term: str) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] | None = None
    best_length: int | None = None
    for start_index in range(len(words)):
        text = ""
        for end_index in range(start_index, len(words)):
            text += str(words[end_index].get("text", ""))
            if term in clean_text(text).lower():
                candidate = words[start_index : end_index + 1]
                candidate_length = len(clean_text(text))
                if best is None or candidate_length < (best_length or 10**9):
                    best = candidate
                    best_length = candidate_length
                break
    return best or words


def build_windows(words: list[dict[str, Any]], seconds: float) -> list[dict[str, Any]]:
    if not words:
        return []
    windows = []
    current: list[dict[str, Any]] = []
    window_start = float(words[0]["start"])
    for word in words:
        if current and float(word["start"]) - window_start > seconds:
            windows.append(window_from_words(current))
            current = []
            window_start = float(word["start"])
        current.append(word)
    if current:
        windows.append(window_from_words(current))
    return windows


def window_from_words(words: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start": float(words[0]["start"]),
        "end": float(words[-1]["end"]),
        "text": join_words(words),
        "words": words,
    }


def semantic_trigger_time(window: dict[str, Any], terms: list[str] | tuple[str, ...]) -> float:
    words = window.get("words")
    if isinstance(words, list) and words:
        normalized_words = [clean_text(str(word.get("text", ""))).replace(" ", "").lower() for word in words]
        joined = "".join(normalized_words)
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for index, word in enumerate(normalized_words):
            offsets.append((cursor, index))
            cursor += len(word)
        for term in terms:
            normalized_term = clean_text(str(term)).replace(" ", "").lower()
            if not normalized_term:
                continue
            position = joined.find(normalized_term)
            if position >= 0:
                word_index = next(index for offset, index in reversed(offsets) if offset <= position)
                return round(float(words[word_index]["start"]) + 0.12, 2)
    return round(max(0, float(window.get("start", 0))) + 0.12, 2)


def stickers_from_window(window: dict[str, Any], index: int, density: str) -> list[dict[str, Any]]:
    text = clean_text(window["text"])
    signal_score = semantic_signal_score(text)
    primary = sticker_from_window(window, index, force=density == "high" and signal_score >= 2)
    if not primary:
        return []
    primary["semanticScore"] = signal_score
    primary["clusterId"] = f"segment-{window['start']:.2f}"
    primary["allowCoexist"] = bool(
        density == "high"
        and primary["semanticScore"] >= 3
        and str(primary.get("type")) in {"comparison-card", "process-flow", "source-quote", "parallel-points"}
    )
    results = [primary]

    number_match = find_number(text)
    if density == "high" and primary["allowCoexist"] and number_match and str(primary.get("type")) != "metric-card":
        number = number_match.group(0).replace(" ", "")
        companion = {
            "start": round(float(primary["start"]) + 0.7, 2),
            "duration": max(3.0, float(primary["duration"]) - 0.7),
            "type": "metric-card",
            "position": POSITIONS[(index + 1) % len(POSITIONS)],
            "label": guess_label(text),
            "headline": number,
            "unit": "",
            "caption": compact_caption(text.replace(number_match.group(0), ""), limit=12) or "关键数据",
            "accent": guess_accent(text, index + 1),
            "evidence": primary["evidence"],
            "semanticScore": primary["semanticScore"],
            "clusterId": primary["clusterId"],
            "allowCoexist": True,
        }
        results.append(companion)
    return results


def sticker_from_window(window: dict[str, Any], index: int, force: bool = False) -> dict[str, Any] | None:
    text = clean_text(window["text"])
    comparison = extract_comparison(text)
    if comparison:
        before, after = comparison
        return {
            "start": semantic_trigger_time(window, ("以前", "之前", "原来", "现在", "如今", "后来", "不是", "而是", "变成", "变为")),
            "duration": round(max(4.0, min(6.0, window["end"] - window["start"] + 1.0)), 2),
            "type": "comparison-card",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": "TOOL COMPARISON",
            "headline": after,
            "caption": "变化一眼看懂",
            "before": before,
            "after": after,
            "accent": "green",
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }
    steps = extract_process_steps(text)
    if len(steps) >= 2:
        return {
            "start": semantic_trigger_time(window, ("第一步", "第一件", "首先", "第一个", "先")),
            "duration": round(max(4.2, min(6.4, window["end"] - window["start"] + 1.2)), 2),
            "type": "process-flow",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": "FLOW IN MOTION",
            "headline": steps[-1],
            "caption": "一步步看懂",
            "steps": steps,
            "accent": "cyan",
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }
    source_quote = extract_source_quote(text)
    if source_quote:
        quote, source = source_quote
        return {
            "start": semantic_trigger_time(window, ("作者", "原书", "研究", "报告", "采访")),
            "duration": round(max(4.0, min(6.0, window["end"] - window["start"] + 0.9)), 2),
            "type": "source-quote",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": "SOURCE / QUOTE",
            "headline": quote,
            "caption": source,
            "source": source,
            "accent": "cyan",
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }
    parallel_points = extract_parallel_points(text)
    if parallel_points:
        return {
            "start": semantic_trigger_time(window, ("第一个", "第一", "首先", "一是")),
            "duration": round(max(4.0, min(6.2, window["end"] - window["start"] + 1.0)), 2),
            "type": "parallel-points",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": "KEY POINTS",
            "headline": parallel_points[0],
            "unit": "",
            "caption": parallel_points[-1],
            "points": parallel_points,
            "accent": guess_accent(text, index),
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }
    number_match = find_number(text)
    if number_match:
        headline = normalize_metric_headline(number_match.group(0))
        caption = compact_caption(text.replace(number_match.group(0), ""), limit=14)
        return {
            "start": semantic_trigger_time(window, (number_match.group(0),)),
            "duration": round(max(3.2, min(5.5, window["end"] - window["start"] + 0.6)), 2),
            "type": "metric-card",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": guess_label(text),
            "headline": headline,
            "unit": "",
            "caption": caption or "重点数据",
            "accent": guess_accent(text, index),
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }

    if force or has_strong_signal(text):
        trigger_terms = tuple(token for token in ("关键", "真正", "必须", "不要", "所以", "前提", "核心", "记住") if token in text)
        return {
            "start": semantic_trigger_time(window, trigger_terms),
            "duration": round(max(3.0, min(5.0, window["end"] - window["start"] + 0.4)), 2),
            "type": "insight-card",
            "position": POSITIONS[index % len(POSITIONS)],
            "label": "DEEP DIVE",
            "headline": compact_caption(text, limit=10),
            "unit": "",
            "caption": compact_caption(text, limit=18),
            "accent": guess_accent(text, index),
            "evidence": f"transcript {window['start']:.2f}-{window['end']:.2f}: {text[:80]}",
        }
    return None


def find_number(text: str) -> re.Match[str] | None:
    return re.search(
        r"([$¥￥]?\s?\d+(?:[.,]\d+)?(?:\s?[-到~]\s?[$¥￥]?\d+(?:[.,]\d+)?)?\s?(?:%|块|元|万|小时|天|年|次|个|种|步|k|K|w|W|\+)?|百分之[一二三四五六七八九十百千万零〇两\d]+)",
        text,
    )


def normalize_metric_headline(value: str) -> str:
    compact = clean_text(value)
    percent = re.fullmatch(r"百分之([一二三四五六七八九十百千万零〇两\d]+)", compact)
    if percent:
        normalized = chinese_number_to_int(percent.group(1))
        if normalized is not None:
            return f"{normalized}%"
    return compact


def chinese_number_to_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            return None
    return total + current


def extract_comparison(text: str) -> tuple[str, str] | None:
    patterns = (
        r"(?:以前|之前|原来)(.{2,10}?)(?:现在|如今|后来)(.{2,10})",
        r"(?:从)(.{2,10}?)(?:变成|变为|到)(.{2,10})",
        r"(?:不是)(.{2,10}?)(?:而是)(.{2,10})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            before = compact_caption(match.group(1), 10)
            after = compact_caption(match.group(2), 10)
            if before and after and before != after:
                return before, after
    return None


def extract_process_steps(text: str) -> list[str]:
    if not any(token in text for token in ("第一步", "第二步", "第三步", "先", "然后", "接着", "最后")):
        return []
    parts = re.split(r"(?:第一步|第二步|第三步|第四步|首先|然后|接着|最后)", text)
    steps = [compact_caption(part, 8) for part in parts if 2 <= len(compact_caption(part, 8)) <= 8]
    return list(dict.fromkeys(steps))[:4] if len(steps) >= 2 else []


def extract_source_quote(text: str) -> tuple[str, str] | None:
    source_tokens = ("书里", "原书", "报告", "研究", "论文", "采访", "他说", "她说", "作者认为", "数据显示")
    token = next((item for item in source_tokens if item in text), None)
    if not token:
        return None
    quote = compact_caption(text.replace(token, ""), 18)
    if len(quote) < 6:
        return None
    source_map = {"书里": "来自原书观点", "原书": "来自原书观点", "报告": "来自报告", "研究": "来自研究", "论文": "来自论文", "采访": "来自采访"}
    return quote, source_map.get(token, "来自人物原话")


def semantic_signal_score(text: str) -> int:
    groups = (
        ("所以", "因此", "结论", "关键", "核心", "真正", "记住"),
        ("但是", "不过", "相反", "不是", "而是", "以前", "现在", "相比"),
        ("第一", "第二", "第三", "步骤", "先", "然后", "最后"),
        ("书里", "报告", "研究", "论文", "采访", "他说", "她说"),
    )
    score = sum(1 for group in groups if any(token in text for token in group))
    if find_number(text):
        score += 1
    if len(text) >= 12:
        score += 1
    return score


def extract_parallel_points(text: str) -> list[str]:
    if not any(token in text for token in ("第一", "第二", "第三", "分别", "三种", "三个", "几种", "区别")):
        return []
    clauses = [compact_caption(item, limit=12) for item in re.split(r"[。；;]", text) if item]
    clauses = [item for item in clauses if 3 <= len(item) <= 12]
    if len(clauses) >= 2:
        return clauses[:3]
    comma_clauses = [compact_caption(item, limit=10) for item in re.split(r"[，、]", text) if item]
    comma_clauses = [item for item in comma_clauses if 2 <= len(item) <= 10]
    return comma_clauses[:3] if len(comma_clauses) >= 2 else []


def clean_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def compact_caption(text: str, limit: int) -> str:
    text = re.sub(r"[，。！？、,.!?]", "", clean_text(text))
    if len(text) <= limit:
        return text
    return text[:limit]


def guess_label(text: str) -> str:
    if any(token in text for token in ("美国", "US", "美元", "$")):
        return "US-BASED"
    if any(token in text for token in ("海外", "国外", "境外")):
        return "OVERSEAS"
    if any(token in text for token in ("兼职", "副业")):
        return "PART-TIME"
    if any(token in text for token in ("工资", "收入", "赚钱", "价格", "报价", "钱")):
        return "PAY DIVE"
    if any(token in text for token in ("步骤", "第一", "第二", "第三")):
        return "STEP"
    return "KEY POINT"


def guess_accent(text: str, index: int) -> str:
    if any(token in text for token in ("海外", "增长", "可以", "机会", "赚")):
        return "green"
    if any(token in text for token in ("风险", "别", "坑", "问题")):
        return "red"
    if any(token in text for token in ("美国", "US", "$", "美元")):
        return "blue"
    return ["gold", "blue", "green"][index % 3]


def has_strong_signal(text: str) -> bool:
    signals = ("所以", "关键", "真正", "核心", "记住", "其实", "但是", "第一", "第二", "第三")
    return any(signal in text for signal in signals) and len(text) >= 8


def write_review(stickers_doc: dict[str, Any], path: Path) -> None:
    lines = [
        "# Stickers Review",
        "",
        "| # | Time | Type | Position | Headline | Caption | Evidence |",
        "|---|---:|---|---|---|---|---|",
    ]
    for idx, sticker in enumerate(stickers_doc.get("stickers", []), start=1):
        lines.append(
            "| {idx} | {start:.2f}s / {duration:.2f}s | {type} | {position} | {headline} | {caption} | {evidence} |".format(
                idx=idx,
                start=float(sticker.get("start", 0)),
                duration=float(sticker.get("duration", 0)),
                type=escape_md(str(sticker.get("type", ""))),
                position=escape_md(str(sticker.get("position", ""))),
                headline=escape_md(str(sticker.get("headline", ""))),
                caption=escape_md(str(sticker.get("caption", ""))),
                evidence=escape_md(str(sticker.get("evidence", ""))),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_composition(video: Path, stickers_doc: dict[str, Any], path: Path) -> None:
    sticker_html = "\n".join(render_sticker(sticker) for sticker in stickers_doc.get("stickers", []))
    width = int(stickers_doc.get("video", {}).get("width") or 1920)
    height = int(stickers_doc.get("video", {}).get("height") or 1080)
    aspect_class = "vertical" if height > width else "horizontal"
    anime_path = Path(__file__).resolve().parent.parent / "assets" / "anime.min.js"
    anime_url = anime_path.resolve().as_uri()
    motion_mode = str(stickers_doc.get("options", {}).get("motion") or "smart")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Unclesix Video Stickers</title>
  <style>
    :root {{
      --stage-w: {width};
      --stage-h: {height};
      color-scheme: dark;
      font-family: "Microsoft YaHei", "微软雅黑", "PingFang SC", "STHeiti", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #080a0f;
    }}
    .stage {{
      position: relative;
      width: min(100vw, calc(100vh * {width} / {height}));
      aspect-ratio: {width} / {height};
      overflow: hidden;
      background: #05070a;
    }}
    video {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .sticker {{
      position: absolute;
      width: 36%;
      min-height: 11%;
      border: 1px solid color-mix(in srgb, var(--accent), transparent 18%);
      border-radius: 7px;
      background: linear-gradient(180deg, rgba(12, 14, 21, .86), rgba(8, 9, 14, .76));
      box-shadow: 0 0 0 1px rgba(255,255,255,.06) inset, 0 18px 50px rgba(0,0,0,.36), 0 0 24px color-mix(in srgb, var(--accent), transparent 76%);
      backdrop-filter: blur(10px);
      padding: clamp(10px, 1.25vw, 20px) clamp(12px, 1.55vw, 24px);
      opacity: 0;
      transform: translate3d(0, 10px, 0) scale(.985);
      transition: opacity .22s ease, transform .22s ease;
    }}
    .sticker.is-visible {{
      opacity: 1;
      transform: translate3d(0, 0, 0) scale(1);
    }}
    .style-neon-metric {{
      background: rgba(0, 0, 0, .78);
      border-width: 2px;
      box-shadow: 0 0 0 1px rgba(255,255,255,.08) inset, 0 18px 46px rgba(0,0,0,.42), 0 0 26px color-mix(in srgb, var(--accent), transparent 68%);
    }}
    .style-neon-metric::before {{
      content: "";
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 7px;
      background: var(--accent);
      box-shadow: 0 0 18px var(--accent);
    }}
    .style-neon-metric .headline {{
      display: inline-flex;
      padding: .08em .18em .1em;
      background: color-mix(in srgb, var(--accent), transparent 78%);
    }}
    .style-solid-banner {{
      background: var(--accent);
      border-color: rgba(255,255,255,.92);
      box-shadow: 0 14px 36px rgba(0,0,0,.24);
    }}
    .style-solid-banner .label {{ color: rgba(0,0,0,.78); }}
    .style-editorial-cutout {{
      background: rgba(255,255,255,.92);
      border-color: rgba(0,0,0,.78);
      box-shadow: 0 14px 32px rgba(0,0,0,.22);
    }}
    .style-editorial-cutout .headline,
    .style-editorial-cutout .caption {{ color: #0b0b0c; }}
    .left-stack-1 {{ left: 6%; top: 9%; }}
    .left-stack-2 {{ left: 6%; top: 26.5%; }}
    .left-stack-3 {{ left: 6%; top: 44%; }}
    .right-stack-1 {{ right: 6%; top: 9%; }}
    .right-stack-2 {{ right: 6%; top: 26.5%; }}
    .right-stack-3 {{ right: 6%; top: 44%; }}
    .left-focus {{ left: 6%; top: 24%; }}
    .right-focus {{ right: 6%; top: 24%; }}
    .right-lower-1 {{ right: 6%; top: 52%; }}
    .right-lower-2 {{ right: 6%; top: 67%; }}
    .left-lower-1 {{ left: 6%; top: 52%; }}
    .left-lower-2 {{ left: 6%; top: 67%; }}
    .top-left {{ left: 5.5%; top: 4%; }}
    .top-right {{ right: 5.5%; top: 4%; }}
    .vertical .sticker {{ width: 33.5%; min-height: 7.8%; }}
    .vertical .top-left {{ left: 5.5%; top: 4%; }}
    .vertical .top-right {{ right: 5.5%; top: 4%; }}
    .label {{
      display: flex;
      align-items: center;
      gap: .45em;
      color: var(--accent);
      font-size: clamp(10px, .86vw, 15px);
      font-weight: 800;
      letter-spacing: .14em;
      text-transform: uppercase;
      line-height: 1;
      margin-bottom: .5em;
    }}
    .label::before {{
      content: "";
      width: .62em;
      height: .62em;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 14px var(--accent);
    }}
    .headline {{
      display: flex;
      align-items: flex-end;
      gap: .16em;
      color: #f8fafc;
      font-size: clamp(28px, 3.4vw, 64px);
      font-weight: 900;
      line-height: .92;
      white-space: nowrap;
    }}
    .unit {{
      color: rgba(248, 250, 252, .82);
      font-size: .34em;
      font-weight: 850;
      line-height: 1.15;
      padding-bottom: .18em;
    }}
    .caption {{
      margin-top: .56em;
      color: rgba(248, 250, 252, .86);
      font-size: clamp(13px, 1.05vw, 18px);
      font-weight: 700;
      line-height: 1.25;
      white-space: nowrap;
    }}
    .sticker.mode-rectangle {{
      background: rgba(18,19,21,.79);
      border: 1px solid var(--accent);
      border-left-width: 5px;
      box-shadow: 0 12px 30px rgba(0,0,0,.24);
    }}
    .sticker.mode-rectangle::before,
    .sticker.mode-square::before,
    .sticker.mode-text-shadow::before,
    .sticker.mode-numeric::before,
    .sticker.mode-opinion::before,
    .sticker.mode-parallel-points::before {{ display: none; }}
    .mode-rectangle .headline,
    .mode-square .headline,
    .mode-text-shadow .headline,
    .mode-numeric .headline,
    .mode-parallel-points .headline {{ padding: 0; background: transparent; }}
    .mode-rectangle .label {{ color: rgba(255,255,255,.94); }}
    .mode-rectangle .headline {{ color: var(--accent); }}
    .sticker.mode-square {{
      width: 28%;
      aspect-ratio: 1;
      background: rgba(16,17,19,.76);
      border: 0;
      box-shadow: inset 2px 2px 0 var(--accent), inset -2px -2px 0 var(--accent);
    }}
    .mode-square .caption {{ display: inline-block; padding: .14em .3em; color: #08090a; background: var(--accent); }}
    .sticker.mode-text-shadow {{
      background: rgba(255,255,255,.70);
      border: 0;
      border-left: 3px solid var(--accent);
      box-shadow: 0 10px 24px rgba(0,0,0,.14);
    }}
    .mode-text-shadow .headline,
    .mode-text-shadow .caption {{ color: #08090a; }}
    .mode-text-shadow .headline {{ border-bottom: 4px solid var(--accent); }}
    .sticker.mode-numeric {{
      display: grid;
      width: 32%;
      grid-template-columns: 44% 1fr;
      gap: 14px;
      align-items: center;
      background: transparent;
      border: 0;
      box-shadow: none;
      backdrop-filter: none;
      padding: 0;
    }}
    .mode-numeric .numeric-rail {{ position: absolute; left: 44%; right: 0; top: 58%; height: 2px; background: var(--accent); z-index: 0; transform-origin: left center; }}
    .mode-numeric .numeric-tile {{ align-self: center; aspect-ratio: 1; display: grid; align-content: center; padding: 12px 16px; background: rgba(18,19,21,.86); border: 1px solid rgba(255,255,255,.25); border-top: 4px solid var(--accent); border-radius: 8px; z-index: 1; }}
    .mode-numeric .headline {{ font-size: clamp(46px, 6vw, 94px); }}
    .mode-numeric .numeric-copy {{ display: grid; justify-items: start; gap: 18px; z-index: 1; }}
    .mode-numeric .numeric-copy .headline {{ color: var(--accent); text-shadow: 2px 2px 4px rgba(0,0,0,.7); }}
    .mode-numeric .numeric-copy .caption {{ margin: 0; color: white; text-shadow: 2px 2px 4px rgba(0,0,0,.82); }}
    .sticker.mode-opinion {{ background: transparent; border: 0; box-shadow: none; padding: 0; }}
    .mode-opinion .label {{ color: #111; margin-left: 1.6em; }}
    .mode-opinion .headline {{ display: inline-flex; padding: .08em .18em; color: #08090a; background: var(--accent); }}
    .mode-opinion .caption {{ display: inline-flex; padding: .16em .35em; color: white; background: rgba(18,19,21,.82); }}
    .sticker.mode-parallel-points {{
      width: 38%;
      background: rgba(16,17,19,.75);
      border: 0;
      box-shadow: 0 12px 30px rgba(0,0,0,.24);
    }}
    .point-row {{ display: grid; grid-template-columns: 2.6em 1fr; gap: .75em; align-items: center; margin-top: .72em; font-size: clamp(18px, 1.8vw, 32px); font-weight: 900; }}
    .point-index {{ display: grid; place-items: center; min-height: 1.7em; color: #08090a; background: var(--accent); border-radius: 3px; }}
    .point-copy {{ color: white; }}
    .sticker.mode-comparison {{ width: 34%; background: rgba(18,19,21,.80); border: 0; }}
    .comparison-grid {{ display: grid; grid-template-columns: 1fr auto 1fr; gap: .7em; align-items: center; }}
    .comparison-state {{ padding: .7em; border: 1px solid var(--state-color); border-radius: 5px; text-align: center; }}
    .comparison-name {{ color: var(--state-color); font-weight: 900; font-size: .82em; }}
    .comparison-value {{ margin-top: .35em; color: var(--state-color); font-size: clamp(22px, 2.2vw, 40px); font-weight: 900; }}
    .comparison-arrow {{ color: white; font-size: 1.8em; }}
    .mode-comparison .caption {{ padding-top: .6em; border-top: 1px solid rgba(255,255,255,.18); text-align: center; }}
    .sticker.mode-process-flow {{ width: 37%; background: rgba(18,19,21,.78); border: 0; }}
    .flow-row {{ display: grid; grid-template-columns: repeat(var(--step-count), 1fr); gap: .7em; align-items: start; }}
    .flow-step {{ position: relative; text-align: center; color: white; font-weight: 900; }}
    .flow-index {{ display: grid; place-items: center; width: 2.2em; height: 2.2em; margin: 0 auto .5em; color: var(--accent); border: 2px solid var(--accent); border-radius: 999px; }}
    .flow-step:not(:last-child)::after {{ content: "→"; position: absolute; top: .35em; right: -.75em; color: var(--accent); }}
    .sticker.mode-source-quote {{ width: 32%; background: rgba(22,23,24,.70); border: 0; border-left: 3px solid var(--accent); box-shadow: 0 10px 26px rgba(0,0,0,.2); }}
    .mode-source-quote .quote-mark {{ color: white; font-size: 2.8em; line-height: .6; }}
    .mode-source-quote .headline {{ margin-top: .25em; white-space: normal; line-height: 1.16; }}
    .mode-source-quote .caption {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main class="stage {aspect_class}">
    <video id="source-video" src="{html.escape(video.resolve().as_uri())}" controls playsinline></video>
    {sticker_html}
  </main>
  <script src="{anime_url}"></script>
  <script>
    const video = document.getElementById('source-video');
    const motionMode = {json.dumps(motion_mode)};
    const stickers = [...document.querySelectorAll('.sticker')].map((node) => {{
      const start = Number(node.dataset.start);
      const duration = Number(node.dataset.duration);
      if (motionMode === 'off' || node.dataset.motion === 'none' || !window.anime) {{
        return {{ node, start, duration, timeline: null }};
      }}
      const durationMs = duration * 1000;
      const fromX = node.classList.contains('top-right') || [...node.classList].some((name) => name.startsWith('right')) ? 22 : -22;
      anime.set(node, {{ opacity: 0, translateX: fromX, scale: .982 }});
      const timeline = anime.timeline({{ autoplay: false }});
      timeline.add({{ targets: node, opacity: [0, 1], translateX: [fromX, 0], scale: [.982, 1], duration: 420, easing: 'easeOutCubic' }}, 0);

      if (node.dataset.motion === 'numeric-build') {{
        const valueNode = node.querySelector('.numeric-value');
        const unitNode = node.querySelector('.unit');
        const railNode = node.querySelector('.numeric-rail');
        const captionNode = node.querySelector('.caption');
        const raw = node.dataset.headline || '';
        const match = raw.match(/^([^\\d-]*)(\\d+(?:\\.\\d+)?)([^\\d-]*)$/);
        anime.set(valueNode, {{ opacity: .35, translateY: 10, filter: 'blur(4px)' }});
        anime.set(unitNode, {{ opacity: 0, translateY: -10, scale: .86 }});
        anime.set(railNode, {{ opacity: .35, scaleX: 0 }});
        anime.set(captionNode, {{ opacity: 0, translateY: 9 }});
        if (match) {{
          const prefix = match[1];
          const target = Number(match[2]);
          const suffix = match[3];
          const decimals = (match[2].split('.')[1] || '').length;
          const integerWidth = match[2].split('.')[0].length;
          const counter = {{ value: 0 }};
          timeline.add({{
            targets: counter,
            value: [0, target],
            duration: 680,
            easing: 'easeOutExpo',
            update: () => {{
              const value = decimals ? counter.value.toFixed(decimals) : String(Math.round(counter.value)).padStart(integerWidth, '0');
              valueNode.textContent = `${{prefix}}${{value}}${{suffix}}`;
            }}
          }}, 100);
        }}
        timeline
          .add({{ targets: valueNode, opacity: [.35, 1], translateY: [10, 0], filter: ['blur(4px)', 'blur(0px)'], duration: 560, easing: 'easeOutCubic' }}, 100)
          .add({{ targets: unitNode, opacity: [0, 1], translateY: [-10, 0], scale: [.86, 1], duration: 310, easing: 'easeOutBack' }}, 240)
          .add({{ targets: railNode, opacity: [.35, 1], scaleX: [0, 1], duration: 430, easing: 'easeOutCubic' }}, 320)
          .add({{ targets: captionNode, opacity: [0, 1], translateY: [9, 0], duration: 330, easing: 'easeOutCubic' }}, 440)
          .add({{ targets: node, scale: [1, 1.018, 1], duration: 460, easing: 'easeInOutQuad' }}, 900);
      }}

      timeline.add({{ targets: node, opacity: [1, 0], translateX: [0, fromX * .55], duration: 320, easing: 'easeInCubic' }}, Math.max(900, durationMs - 320));
      return {{ node, start, duration, timeline }};
    }});
    function sync() {{
      const time = video.currentTime || 0;
      for (const sticker of stickers) {{
        const relativeMs = (time - sticker.start) * 1000;
        if (!sticker.timeline) {{
          sticker.node.classList.toggle('is-visible', relativeMs >= 0 && relativeMs <= sticker.duration * 1000);
          continue;
        }}
        sticker.timeline.seek(Math.max(0, Math.min(relativeMs, sticker.duration * 1000)));
      }}
      requestAnimationFrame(sync);
    }}
    sync();
  </script>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")


def render_sticker(sticker: dict[str, Any]) -> str:
    style = str(sticker.get("style") or "glass")
    display_mode = str(sticker.get("displayMode") or "rectangle")
    accent = native_accent(sticker, style)
    classes = f"sticker style-{html.escape(style)} mode-{html.escape(display_mode)} {html.escape(str(sticker.get('position', 'left-top')))}"
    label = html.escape(str(sticker.get("label", "KEY POINT")))
    headline = html.escape(str(sticker.get("headline", "")))
    unit = html.escape(str(sticker.get("unit", "")))
    caption = html.escape(str(sticker.get("caption", "")))
    if display_mode == "parallel-points":
        rows = "".join(
            f'<div class="point-row"><span class="point-index">{index:02d}</span><span class="point-copy">{html.escape(point)}</span></div>'
            for index, point in enumerate(sticker_points(sticker), start=1)
        )
        content = f'<div class="label">{label}</div>{rows}'
    elif display_mode == "numeric":
        content = (
            f'<div class="numeric-tile"><div class="label">{label}</div><div class="headline numeric-value">{headline}</div></div>'
            f'<div class="numeric-copy"><div class="headline"><span class="unit">{unit}</span></div><div class="numeric-rail"></div><div class="caption">{caption}</div></div>'
        )
    elif display_mode == "comparison":
        before = html.escape(str(sticker.get("before") or headline))
        after = html.escape(str(sticker.get("after") or caption))
        content = (
            f'<div class="label">{label}</div><div class="comparison-grid">'
            f'<div class="comparison-state" style="--state-color:#ff806f"><div class="comparison-name">BEFORE</div><div class="comparison-value">{before}</div></div>'
            f'<div class="comparison-arrow">→</div>'
            f'<div class="comparison-state" style="--state-color:{accent}"><div class="comparison-name">NOW</div><div class="comparison-value">{after}</div></div>'
            f'</div><div class="caption">{caption}</div>'
        )
    elif display_mode == "process-flow":
        steps = sticker_steps(sticker)
        rows = "".join(
            f'<div class="flow-step"><span class="flow-index">{index:02d}</span><span>{html.escape(step)}</span></div>'
            for index, step in enumerate(steps, start=1)
        )
        content = f'<div class="label">{label}</div><div class="flow-row" style="--step-count:{max(1, len(steps))}">{rows}</div><div class="caption">{caption}</div>'
    elif display_mode == "source-quote":
        content = f'<div class="label">{label}</div><div class="quote-mark">“</div><div class="headline">{headline}</div><div class="caption">{caption}</div>'
    else:
        content = f'<div class="label">{label}</div><div class="headline">{headline}<span class="unit">{unit}</span></div><div class="caption">{caption}</div>'
    return textwrap.dedent(
        f"""
        <section
          class="{classes}"
          data-start="{float(sticker.get('start', 0)):.2f}"
          data-duration="{float(sticker.get('duration', 3)):.2f}"
          data-motion="{html.escape(str(sticker.get('motionPreset') or 'soft-slide'))}"
          data-headline="{headline}"
          style="--accent: {accent};">
          {content}
        </section>
        """
    ).strip()


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def ease_out_cubic(value: float) -> float:
    value = clamp01(value)
    return 1 - (1 - value) ** 3


def ease_in_cubic(value: float) -> float:
    value = clamp01(value)
    return value**3


def ease_out_expo(value: float) -> float:
    value = clamp01(value)
    return 1.0 if value >= 1 else 1 - 2 ** (-10 * value)


def animated_numeric_headline(headline: str, progress: float) -> str:
    match = re.fullmatch(r"([^\d-]*)(\d+(?:\.\d+)?)([^\d-]*)", headline.strip())
    if not match:
        return headline
    prefix, raw_value, suffix = match.groups()
    target = float(raw_value)
    decimals = len(raw_value.split(".", 1)[1]) if "." in raw_value else 0
    current = target * clamp01(progress)
    value = f"{current:.{decimals}f}" if decimals else str(round(current)).zfill(len(raw_value))
    return f"{prefix}{value}{suffix}"


def sticker_motion_state(sticker: dict[str, Any], local_time: float, duration: float) -> dict[str, Any]:
    entry = ease_out_cubic(local_time / 0.42)
    exit_progress = ease_in_cubic((local_time - max(0.0, duration - 0.32)) / 0.32)
    position = str(sticker.get("position") or "")
    direction = 1 if position.startswith("right") or position == "top-right" else -1
    scale = 0.982 + 0.018 * entry
    numeric_state: dict[str, Any] | None = None
    if str(sticker.get("motionPreset") or "") == "numeric-build":
        count_progress = ease_out_expo((local_time - 0.10) / 0.68)
        value_progress = ease_out_cubic((local_time - 0.10) / 0.56)
        pulse_progress = clamp01((local_time - 0.90) / 0.46)
        scale *= 1 + 0.018 * max(0.0, math.sin(math.pi * pulse_progress))
        numeric_state = {
            "headline": animated_numeric_headline(str(sticker.get("headline") or ""), count_progress),
            "valueAlpha": 0.35 + 0.65 * value_progress,
            "valueOffsetY": int(round(10 * (1 - value_progress))),
            "unitAlpha": ease_out_cubic((local_time - 0.24) / 0.31),
            "railProgress": ease_out_cubic((local_time - 0.32) / 0.43),
            "captionAlpha": ease_out_cubic((local_time - 0.44) / 0.33),
        }
    return {
        "alpha": entry * (1 - exit_progress),
        "offsetX": direction * int(round(22 * (1 - entry) + 12 * exit_progress)),
        "scale": scale,
        "numeric": numeric_state,
    }


def render_motion_frame(
    sticker: dict[str, Any],
    width: int,
    height: int,
    accent: str,
    font_path: str,
    local_time: float,
    duration: float,
    pad: int,
) -> Any:
    from PIL import Image

    state = sticker_motion_state(sticker, local_time, duration)
    image = render_sticker_image(sticker, width, height, accent, font_path, motion_state=state["numeric"])
    scale = float(state["scale"])
    scaled_w = max(1, int(round(width * scale)))
    scaled_h = max(1, int(round(height * scale)))
    if scaled_w != width or scaled_h != height:
        image = image.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
    alpha = float(state["alpha"])
    if alpha < 0.999:
        image.putalpha(image.getchannel("A").point(lambda value: int(value * alpha)))
    frame = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    x = pad + (width - scaled_w) // 2 + int(state["offsetX"])
    y = pad + (height - scaled_h) // 2
    frame.alpha_composite(image, (x, y))
    return frame


def render_sticker_motion_clips(stickers_doc: dict[str, Any], asset_dir: Path) -> list[dict[str, Any]]:
    try:
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("Pillow is required for sticker motion rendering.") from exc

    video_meta = stickers_doc.get("video", {})
    video_width = int(video_meta.get("width") or 1920)
    video_height = int(video_meta.get("height") or 1080)
    source_fps = float(video_meta.get("fps") or 30)
    motion_fps = max(12, min(24, int(round(source_fps))))
    default_style = str(stickers_doc.get("options", {}).get("style") or "glass")
    font_path = find_font()
    ffmpeg = find_ffmpeg()
    asset_dir.mkdir(parents=True, exist_ok=True)
    overlays: list[dict[str, Any]] = []
    for index, sticker in enumerate(stickers_doc.get("stickers", []), start=1):
        geom = native_card_geometry(sticker, video_width, video_height)
        accent = native_accent(sticker, str(sticker.get("style") or default_style))
        duration = float(sticker.get("duration", 3))
        pad = max(28, int(min(geom["w"], geom["h"]) * 0.12))
        frame_dir = asset_dir / f"motion-{index:02d}-frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        for old_frame in frame_dir.glob("frame-*.png"):
            old_frame.unlink()
        frame_count = max(2, int(math.ceil(duration * motion_fps)))
        for frame_index in range(frame_count):
            local_time = min(duration, frame_index / motion_fps)
            frame = render_motion_frame(sticker, geom["w"], geom["h"], accent, font_path, local_time, duration, pad)
            frame.save(frame_dir / f"frame-{frame_index:04d}.png", compress_level=2)
        clip_path = asset_dir / f"motion-{index:02d}.mov"
        run_cmd(
            [
                ffmpeg,
                "-y",
                "-framerate",
                str(motion_fps),
                "-i",
                str(frame_dir / "frame-%04d.png"),
                "-c:v",
                "qtrle",
                "-pix_fmt",
                "argb",
                str(clip_path),
            ]
        )
        for frame_path in frame_dir.glob("frame-*.png"):
            frame_path.unlink()
        frame_dir.rmdir()
        overlays.append(
            {
                "path": clip_path,
                "x": geom["x"] - pad,
                "y": geom["y"] - pad,
                "start": float(sticker.get("start", 0)),
                "duration": duration,
                "animated": True,
            }
        )
    return overlays


def render_native_video(video: Path, stickers_doc: dict[str, Any], output: Path) -> None:
    validate_visual_variety(stickers_doc)
    ffmpeg = find_ffmpeg()
    motion_mode = str(stickers_doc.get("options", {}).get("motion") or "smart")
    asset_dir = output.parent / f"{output.stem}-assets"
    overlays = render_sticker_motion_clips(stickers_doc, asset_dir) if motion_mode == "smart" else render_sticker_pngs(stickers_doc, asset_dir)
    if not overlays:
        filters = build_native_filters(stickers_doc)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(video),
            "-vf",
            filters,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        run_cmd(command)
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-i", str(video)]
    for overlay in overlays:
        if overlay.get("animated"):
            command.extend(["-i", str(overlay["path"])])
        else:
            command.extend(["-loop", "1", "-i", str(overlay["path"])])
    filter_parts: list[str] = []
    previous = "[0:v]"
    for index, overlay in enumerate(overlays, start=1):
        output_label = f"[v{index}]"
        start = float(overlay["start"])
        if overlay.get("animated"):
            motion_label = f"[m{index}]"
            filter_parts.append(f"[{index}:v]format=rgba,setpts=PTS-STARTPTS+{start:.3f}/TB{motion_label}")
            filter_parts.append(
                f"{previous}{motion_label}overlay=x={overlay['x']}:y={overlay['y']}:eof_action=pass:shortest=0{output_label}"
            )
        else:
            end = start + float(overlay["duration"])
            enable = f"between(t\\,{start:.2f}\\,{end:.2f})"
            filter_parts.append(
                f"{previous}[{index}:v]overlay=x={overlay['x']}:y={overlay['y']}:shortest=1:enable='{enable}'{output_label}"
            )
        previous = output_label
    command.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            previous,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    run_cmd(command)


def validate_visual_variety(stickers_doc: dict[str, Any]) -> None:
    stickers = list(stickers_doc.get("stickers", []))
    options = stickers_doc.get("options", {})
    density = str(options.get("density") or "")
    requested_mode = str(options.get("displayMode") or "")
    for sticker in stickers:
        mode = str(sticker.get("displayMode") or "")
        if mode == "parallel-points" and len(sticker_points(sticker)) < 2:
            raise SystemExit("parallel-points stickers require a points array with two or three items.")
        if mode == "process-flow" and len(sticker_steps(sticker)) < 2:
            raise SystemExit("process-flow stickers require a steps array with two to four items.")
        if mode == "comparison" and (not sticker.get("before") or not sticker.get("after")):
            raise SystemExit("comparison stickers require before and after values.")
        if mode == "source-quote" and not str(sticker.get("caption") or sticker.get("source") or "").strip():
            raise SystemExit("source-quote stickers require a visible source caption.")

    validate_active_sticker_layout(stickers_doc)

    if density != "high" or len(stickers) < 4:
        return

    modes = {str(sticker.get("displayMode") or "rectangle") for sticker in stickers}
    required_modes = 4 if len(stickers) >= 6 else 3
    if requested_mode == "mixed" and len(modes) < required_modes:
        raise SystemExit(f"High-density mixed layout must use at least {required_modes} sticker display modes.")

    sides = ["right" if str(sticker.get("position", "")).startswith("right") or str(sticker.get("position", "")) == "top-right" else "left" for sticker in stickers]
    mechanically_alternating = len(sides) >= 4 and all(sides[index] != sides[index - 1] for index in range(1, len(sides)))
    if mechanically_alternating:
        raise SystemExit("High-density layout uses a rigid left/right alternation. Use grouped or varied placement rhythm.")


def validate_active_sticker_layout(stickers_doc: dict[str, Any]) -> None:
    stickers = list(stickers_doc.get("stickers", []))
    video = stickers_doc.get("video", {})
    width = int(video.get("width") or 1920)
    height = int(video.get("height") or 1080)
    max_visible = 2 if height > width else 3
    checkpoints = sorted({float(item.get("start", 0)) for item in stickers})
    for time in checkpoints:
        active = [
            item
            for item in stickers
            if float(item.get("start", 0)) <= time < float(item.get("start", 0)) + float(item.get("duration", 3))
        ]
        if len(active) > max_visible:
            raise SystemExit(f"Too many simultaneous stickers at {time:.2f}s: {len(active)} > {max_visible}.")
        geometries = [native_card_geometry(item, width, height) for item in active]
        for index, first in enumerate(geometries):
            if first["x"] < 0 or first["y"] < 0 or first["x"] + first["w"] > width or first["y"] + first["h"] > int(height * 0.90):
                raise SystemExit(f"Sticker leaves the safe region at {time:.2f}s.")
            for second in geometries[index + 1 :]:
                if rects_overlap(first, second, margin=max(8, int(height * 0.012))):
                    raise SystemExit(f"Sticker collision detected at {time:.2f}s.")


def render_sticker_pngs(stickers_doc: dict[str, Any], asset_dir: Path) -> list[dict[str, Any]]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("Pillow is required for precision sticker layout. Install with `python3 -m pip install --user pillow`.") from exc

    video_meta = stickers_doc.get("video", {})
    width = int(video_meta.get("width") or 1920)
    height = int(video_meta.get("height") or 1080)
    default_style = str(stickers_doc.get("options", {}).get("style") or "glass")
    font_path = find_font()
    asset_dir.mkdir(parents=True, exist_ok=True)
    overlays: list[dict[str, Any]] = []
    for index, sticker in enumerate(stickers_doc.get("stickers", []), start=1):
        style = str(sticker.get("style") or default_style)
        geom = native_card_geometry(sticker, width, height)
        accent = native_accent(sticker, style)
        image = render_sticker_image(sticker, geom["w"], geom["h"], accent, font_path)
        asset_path = asset_dir / f"sticker-{index:02d}.png"
        image.save(asset_path)
        overlays.append(
            {
                "path": asset_path,
                "x": geom["x"],
                "y": geom["y"],
                "start": float(sticker.get("start", 0)),
                "duration": float(sticker.get("duration", 3)),
            }
        )
    return overlays


def render_sticker_image(
    sticker: dict[str, Any],
    width: int,
    height: int,
    accent: str,
    font_path: str,
    motion_state: dict[str, Any] | None = None,
) -> Any:
    from PIL import Image, ImageDraw

    display_mode = str(sticker.get("displayMode") or "rectangle")
    label = str(sticker.get("label") or "KEY POINT")
    headline = str(sticker.get("headline") or "")
    unit = str(sticker.get("unit") or "")
    caption = str(sticker.get("caption") or "")
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    combined_headline = f"{headline} {unit}".strip() if unit else headline
    if display_mode == "square":
        draw_square_sticker(draw, (width, height), label, combined_headline, caption, accent, font_path)
    elif display_mode == "text-shadow":
        draw_text_shadow_sticker(draw, (width, height), label, combined_headline, caption, accent, font_path)
    elif display_mode == "numeric":
        draw_numeric_sticker(draw, (width, height), label, headline, unit, caption, accent, font_path, motion_state=motion_state)
    elif display_mode == "opinion":
        draw_opinion_sticker(draw, (width, height), label, combined_headline, caption, accent, font_path)
    elif display_mode == "parallel-points":
        points = sticker_points(sticker) or [value for value in (headline, caption) if value]
        draw_parallel_points_sticker(draw, (width, height), label, points, accent, font_path)
    elif display_mode == "comparison":
        before = str(sticker.get("before") or headline or "之前")
        after = str(sticker.get("after") or caption or "现在")
        draw_comparison_sticker(draw, (width, height), label, before, after, caption, accent, font_path)
    elif display_mode == "process-flow":
        steps = sticker_steps(sticker) or [value for value in (headline, caption) if value]
        draw_process_flow_sticker(draw, (width, height), label, steps, caption, accent, font_path)
    elif display_mode == "source-quote":
        draw_source_quote_sticker(draw, (width, height), label, headline, caption, accent, font_path)
    else:
        draw_rectangle_sticker(draw, (width, height), label, combined_headline, caption, accent, font_path)
    return image


def draw_rectangle_sticker(draw: Any, size: tuple[int, int], label: str, headline: str, caption: str, accent: str, font_path: str) -> None:
    width, height = size
    pad_x = max(24, int(width * 0.075))
    max_text_w = width - pad_x * 2
    label_font = fit_pil_font(label, font_path, int(height * 0.16), max_text_w, min_size=20)
    headline_font = fit_pil_font(headline, font_path, int(height * 0.34), max_text_w, min_size=30)
    caption_font = fit_pil_font(caption, font_path, int(height * 0.18), max_text_w, min_size=22)
    label_box = text_bbox(draw, label, label_font)
    headline_box = text_bbox(draw, headline, headline_font)
    caption_box = text_bbox(draw, caption, caption_font)
    label_h = bbox_h(label_box)
    headline_h = bbox_h(headline_box)
    caption_h = bbox_h(caption_box)
    gap1 = max(10, int(height * 0.075))
    gap2 = max(12, int(height * 0.085))
    content_h = label_h + gap1 + headline_h + gap2 + caption_h
    y = max(12, int((height - content_h) / 2))
    headline_y = y + label_h + gap1
    caption_y = headline_y + headline_h + gap2
    draw_text_with_soft_shadow(draw, label, pad_x, y, label_font, (245, 245, 245, 255))
    draw_text_with_soft_shadow(draw, headline, pad_x, headline_y, headline_font, hex_to_rgba(accent, 255), faux_bold=True)
    draw_text_with_soft_shadow(draw, caption, pad_x, caption_y, caption_font, (255, 255, 255, 255))


def draw_square_sticker(draw: Any, size: tuple[int, int], label: str, headline: str, caption: str, accent: str, font_path: str) -> None:
    width, height = size
    inset = max(10, int(width * 0.045))
    pad_x = max(22, int(width * 0.085))
    max_text_w = width - pad_x * 2
    label_font = fit_pil_font(label, font_path, int(height * 0.12), max_text_w, min_size=22)
    single_headline_font = fit_pil_font(headline, font_path, int(height * 0.22), max_text_w, min_size=34)
    headline_lines = [headline] if single_headline_font.size >= int(height * 0.12) else split_balanced_lines(headline)
    headline_font = fit_shared_pil_font(headline_lines, font_path, int(height * 0.22), max_text_w, min_size=30)
    caption_lines = split_cjk_lines(caption, 8, max_lines=2)
    caption_font = fit_shared_pil_font(caption_lines, font_path, int(height * 0.115), max_text_w, min_size=20)
    label_box = text_bbox(draw, label, label_font)
    label_h = bbox_h(label_box)
    headline_boxes = [text_bbox(draw, line, headline_font) for line in headline_lines]
    headline_line_gap = max(3, int(height * 0.014))
    headline_text_h = sum(bbox_h(box) for box in headline_boxes) + headline_line_gap * max(0, len(headline_lines) - 1)
    caption_boxes = [text_bbox(draw, line, caption_font) for line in caption_lines]
    caption_line_gap = max(3, int(height * 0.014))
    caption_text_h = sum(bbox_h(box) for box in caption_boxes) + caption_line_gap * max(0, len(caption_lines) - 1)
    gap1 = max(15, int(height * 0.06))
    gap2 = max(16, int(height * 0.065))
    content_h = label_h + gap1 + headline_text_h + gap2 + caption_text_h
    y = max(inset + 6, int((height - content_h) / 2))

    draw_text_with_soft_shadow(draw, label, pad_x, y, label_font, hex_to_rgba(accent, 255))
    headline_y = y + label_h + gap1
    caption_y = headline_y + headline_text_h + gap2
    split_at = max(1, len(headline) // 2)
    headline_cursor = 0
    for line, box in zip(headline_lines, headline_boxes):
        split_index = max(0, min(len(line), split_at - headline_cursor))
        first, second = line[:split_index], line[split_index:]
        text_x = pad_x
        if first:
            draw_text_with_soft_shadow(draw, first, text_x, headline_y, headline_font, (255, 255, 255, 255), faux_bold=True)
            text_x += bbox_w(text_bbox(draw, first, headline_font))
        if second:
            draw_text_with_soft_shadow(draw, second, text_x, headline_y, headline_font, hex_to_rgba(accent, 255), faux_bold=True)
        headline_y += bbox_h(box) + headline_line_gap
        headline_cursor += len(line)
    max_caption_w = max(bbox_w(box) for box in caption_boxes)
    caption_pad_x = max(9, int(caption_font.size * 0.24))
    caption_pad_y = max(6, int(caption_font.size * 0.16))
    draw.rounded_rectangle(
        (
            pad_x - caption_pad_x,
            caption_y - caption_pad_y,
            min(width - inset, pad_x + max_caption_w + caption_pad_x),
            min(height - inset, caption_y + caption_text_h + caption_pad_y),
        ),
        radius=4,
        fill=hex_to_rgba(accent, 255),
    )
    line_y = caption_y
    for line, box in zip(caption_lines, caption_boxes):
        draw_text_top(draw, line, pad_x, line_y, caption_font, (7, 8, 9, 255), faux_bold=True)
        line_y += bbox_h(box) + caption_line_gap


def draw_text_shadow_sticker(draw: Any, size: tuple[int, int], label: str, headline: str, caption: str, accent: str, font_path: str) -> None:
    width, height = size
    pad_x = max(24, int(width * 0.09))
    max_text_w = width - pad_x * 2
    label_font = fit_pil_font(label, font_path, int(height * 0.16), max_text_w, min_size=22)
    headline_font = fit_pil_font(headline, font_path, int(height * 0.36), max_text_w, min_size=40)
    caption_font = fit_pil_font(caption, font_path, int(height * 0.18), max_text_w, min_size=26)
    label_box = text_bbox(draw, label, label_font)
    headline_box = text_bbox(draw, headline, headline_font)
    caption_box = text_bbox(draw, caption, caption_font)
    label_h = bbox_h(label_box)
    headline_h = bbox_h(headline_box)
    caption_h = bbox_h(caption_box)
    gap1 = max(10, int(height * 0.065))
    gap2 = max(13, int(height * 0.085))
    content_h = label_h + gap1 + headline_h + gap2 + caption_h
    y = max(10, int((height - content_h) / 2))
    draw_text_with_soft_shadow(draw, label, pad_x, y, label_font, hex_to_rgba(accent, 255))
    headline_y = y + label_h + gap1
    caption_y = headline_y + headline_h + gap2
    underline_h = max(4, int(headline_h * 0.08))
    underline_y = headline_y + headline_h + max(2, int(height * 0.015))
    draw.rounded_rectangle((pad_x, underline_y, pad_x + bbox_w(headline_box), underline_y + underline_h), radius=underline_h, fill=hex_to_rgba(accent, 255))
    draw_text_with_soft_shadow(draw, headline, pad_x, headline_y, headline_font, (255, 255, 255, 255), faux_bold=True)
    draw_text_with_soft_shadow(draw, caption, pad_x, caption_y, caption_font, (255, 255, 255, 255), faux_bold=True)


def draw_numeric_sticker(
    draw: Any,
    size: tuple[int, int],
    label: str,
    headline: str,
    unit: str,
    caption: str,
    accent: str,
    font_path: str,
    motion_state: dict[str, Any] | None = None,
) -> None:
    width, height = size
    state = motion_state or {}
    display_headline = str(state.get("headline") or headline)
    value_alpha = max(0.0, min(1.0, float(state.get("valueAlpha", 1.0))))
    value_offset = int(state.get("valueOffsetY", 0))
    unit_alpha = max(0.0, min(1.0, float(state.get("unitAlpha", 1.0))))
    rail_progress = max(0.0, min(1.0, float(state.get("railProgress", 1.0))))
    caption_alpha = max(0.0, min(1.0, float(state.get("captionAlpha", 1.0))))
    tile_w = int(width * 0.44)
    tile_pad = max(14, int(tile_w * 0.07))
    label_font = fit_pil_font(label, font_path, int(height * 0.12), tile_w - tile_pad * 2, min_size=19)
    number_font = fit_pil_font(headline, font_path, int(height * 0.70), tile_w - tile_pad * 2, min_size=58)
    label_box = text_bbox(draw, label, label_font)
    number_box = text_bbox(draw, display_headline, number_font)
    label_x = max(tile_pad, int((tile_w - bbox_w(label_box)) / 2))
    number_x = centered_text_x_by_ink(display_headline, number_font, tile_w)
    label_y = int(height * 0.095)
    draw_text_with_soft_shadow(draw, label, label_x, label_y, label_font, (245, 245, 245, 255))
    number_top = label_y + bbox_h(label_box) + max(12, int(height * 0.06))
    number_bottom = height - int(height * 0.07)
    remaining_space = max(0, number_bottom - number_top - bbox_h(number_box))
    number_y = number_top + int(remaining_space * 0.45)
    draw_text_with_soft_shadow(
        draw,
        display_headline,
        number_x,
        number_y + value_offset,
        number_font,
        (255, 255, 255, int(255 * value_alpha)),
        faux_bold=True,
    )

    rail_x = tile_w + max(14, int(width * 0.028))
    rail_w = width - rail_x - max(4, int(width * 0.012))
    unit_text = unit or "重点数据"
    unit_font = fit_pil_font(unit_text, font_path, int(height * 0.27), rail_w, min_size=32)
    single_caption_font = fit_pil_font(caption, font_path, int(height * 0.17), rail_w, min_size=20)
    caption_lines = [caption] if single_caption_font.size >= int(height * 0.115) else split_balanced_lines(caption)
    caption_font = fit_shared_pil_font(caption_lines, font_path, int(height * 0.17), rail_w, min_size=24)
    unit_y = int(height * 0.33)
    line_y = int(height * 0.58)
    shadow_offset = max(2, int(height * 0.012))
    draw_text_top(draw, unit_text, rail_x + shadow_offset, unit_y + shadow_offset, unit_font, (0, 0, 0, int(185 * unit_alpha)), faux_bold=True)
    draw_text_top(draw, unit_text, rail_x, unit_y, unit_font, hex_to_rgba(accent, int(255 * unit_alpha)), faux_bold=True)
    rail_end = tile_w + 1 + int((width - tile_w - 3) * rail_progress)
    if rail_progress > 0:
        draw.line((tile_w + 1, line_y, rail_end, line_y), fill=hex_to_rgba(accent, int(245 * min(1.0, rail_progress * 2))), width=max(2, int(height * 0.013)))
    caption_y = line_y + max(12, int(height * 0.065))
    caption_line_gap = max(3, int(height * 0.018))
    for line in caption_lines:
        line_box = text_bbox(draw, line, caption_font)
        draw_text_top(draw, line, rail_x + shadow_offset, caption_y + shadow_offset, caption_font, (0, 0, 0, int(205 * caption_alpha)), faux_bold=True)
        draw_text_top(draw, line, rail_x, caption_y, caption_font, (255, 255, 255, int(255 * caption_alpha)), faux_bold=True)
        caption_y += bbox_h(line_box) + caption_line_gap


def draw_opinion_sticker(draw: Any, size: tuple[int, int], label: str, headline: str, caption: str, accent: str, font_path: str) -> None:
    width, height = size
    pad_x = max(14, int(width * 0.04))
    label_font = fit_pil_font(label, font_path, int(height * 0.13), width - pad_x * 2, min_size=19)
    headline_font = fit_pil_font(headline, font_path, int(height * 0.36), width - pad_x * 2, min_size=38)
    caption_font = fit_pil_font(caption, font_path, int(height * 0.17), width - pad_x * 2, min_size=22)
    quote_font = load_font(font_path, max(42, int(height * 0.34)))
    draw_text_top(draw, "“", pad_x, 0, quote_font, hex_to_rgba(accent, 255), faux_bold=True)
    label_y = int(height * 0.15)
    draw_text_with_soft_shadow(draw, label, pad_x + int(height * 0.18), label_y, label_font, (255, 255, 255, 245), faux_bold=True)
    headline_y = int(height * 0.36)
    headline_box = text_bbox(draw, headline, headline_font)
    slab_pad_x = max(10, int(height * 0.06))
    slab_pad_y = max(7, int(height * 0.04))
    slab_right = min(width - 2, pad_x + bbox_w(headline_box) + slab_pad_x * 2)
    draw.rounded_rectangle((pad_x, headline_y - slab_pad_y, slab_right, headline_y + bbox_h(headline_box) + slab_pad_y), radius=max(5, slab_pad_y), fill=hex_to_rgba(accent, 255))
    draw_text_top(draw, headline, pad_x + slab_pad_x, headline_y, headline_font, (7, 8, 9, 255), faux_bold=True)
    caption_y = headline_y + bbox_h(headline_box) + slab_pad_y + max(12, int(height * 0.065))
    draw_text_with_soft_shadow(draw, caption, pad_x, caption_y, caption_font, (255, 255, 255, 248), faux_bold=True)


def draw_parallel_points_sticker(draw: Any, size: tuple[int, int], label: str, points: list[str], accent: str, font_path: str) -> None:
    width, height = size
    points = [re.sub(r"^\s*\d{1,2}[.、\s-]*", "", point).strip() for point in points if point.strip()][:3]
    if len(points) < 2:
        points = (points + ["补充观点"])[:2]
    pad_x = max(20, int(width * 0.055))
    label_font = fit_pil_font(label, font_path, int(height * 0.095), width - pad_x * 2, min_size=20)
    draw_text_with_soft_shadow(draw, label, pad_x, int(height * 0.075), label_font, hex_to_rgba(accent, 255), faux_bold=True)
    row_top = int(height * 0.25)
    row_gap = max(12, int(height * 0.055))
    available_h = height - row_top - int(height * 0.08) - row_gap * (len(points) - 1)
    row_h = max(38, int(available_h / len(points)))
    badge_w = max(42, int(row_h * 0.72))
    badge_h = max(32, int(row_h * 0.56))
    badge_gap = max(18, int(width * 0.035))
    text_x = pad_x + badge_w + badge_gap
    max_text_w = width - text_x - pad_x
    spine_x = pad_x + badge_w // 2
    first_center = row_top + row_h // 2
    last_center = row_top + (len(points) - 1) * (row_h + row_gap) + row_h // 2
    draw.line((spine_x, first_center, spine_x, last_center), fill=hex_to_rgba(accent, 255), width=max(2, int(width * 0.004)))
    for index, point in enumerate(points, start=1):
        y = row_top + (index - 1) * (row_h + row_gap)
        highlight_gap = max(6, int(row_h * 0.08))
        text_font = fit_pil_font(point, font_path, int(row_h * 0.60), max_text_w - highlight_gap, min_size=26)
        text_h = bbox_h(text_bbox(draw, point, text_font))
        text_y = y + max(0, (row_h - text_h) // 2)
        index_text = f"{index:02d}"
        index_font = fit_pil_font(index_text, font_path, int(badge_h * 0.50), badge_w - 10, min_size=18)
        index_box = text_bbox(draw, index_text, index_font)
        badge_y = y + max(0, (row_h - badge_h) // 2)
        draw.rounded_rectangle((pad_x, badge_y, pad_x + badge_w, badge_y + badge_h), radius=max(3, int(badge_h * 0.12)), fill=hex_to_rgba(accent, 255))
        index_x = pad_x + max(0, (badge_w - bbox_w(index_box)) // 2)
        index_y = badge_y + max(0, (badge_h - bbox_h(index_box)) // 2)
        draw_text_top(draw, index_text, index_x, index_y, index_font, (7, 8, 9, 255), faux_bold=True)
        split_at = point.rfind("，")
        if split_at >= 1:
            first, second = point[: split_at + 1], point[split_at + 1 :]
        else:
            tail_len = min(4, max(2, len(point) // 2))
            first, second = point[:-tail_len], point[-tail_len:]
        first_w = bbox_w(text_bbox(draw, first, text_font)) if first else 0
        second_box = text_bbox(draw, second, text_font)
        second_x = text_x + first_w + (highlight_gap if first else 0)
        block_pad_x = max(7, int(row_h * 0.10))
        block_lead = max(2, int(row_h * 0.025))
        block_pad_y = max(4, int(row_h * 0.07))
        draw.rectangle((second_x - block_lead, text_y - block_pad_y, min(width - pad_x, second_x + bbox_w(second_box) + block_pad_x), text_y + text_h + block_pad_y), fill=hex_to_rgba(accent, 255))
        if first:
            draw_text_with_soft_shadow(draw, first, text_x, text_y, text_font, (255, 255, 255, 255), faux_bold=True)
        draw_text_with_soft_shadow(draw, second, second_x, text_y, text_font, (7, 8, 9, 255), faux_bold=True)


def draw_comparison_sticker(draw: Any, size: tuple[int, int], label: str, before: str, after: str, caption: str, accent: str, font_path: str) -> None:
    width, height = size
    pad = max(18, int(width * 0.045))
    label_font = fit_pil_font(label, font_path, int(height * 0.10), width - pad * 2, min_size=18)
    draw_text_with_soft_shadow(draw, label, pad, int(height * 0.075), label_font, (245, 245, 243, 255), faux_bold=True)
    panel_top = int(height * 0.25)
    panel_bottom = int(height * 0.68)
    arrow_w = max(42, int(width * 0.09))
    panel_gap = max(12, int(width * 0.018))
    panel_w = (width - pad * 2 - arrow_w - panel_gap * 2) // 2
    before_x = pad
    after_x = before_x + panel_w + arrow_w + panel_gap * 2
    before_color = "#ff806f"
    value_pad = max(24, int(panel_w * 0.09))
    shared_value_font = fit_shared_pil_font(
        [before, after],
        font_path,
        int(height * 0.17),
        panel_w - value_pad * 2,
        min_size=22,
    )
    for x, title, value, color in ((before_x, "BEFORE", before, before_color), (after_x, "NOW", after, accent)):
        title_font = fit_pil_font(title, font_path, int(height * 0.10), panel_w - 20, min_size=16)
        title_box = text_bbox(draw, title, title_font)
        value_box = text_bbox(draw, value, shared_value_font)
        content_gap = max(10, int(height * 0.045))
        content_h = bbox_h(title_box) + content_gap + bbox_h(value_box)
        content_y = panel_top + max(0, (panel_bottom - panel_top - content_h) // 2)
        draw_text_with_soft_shadow(draw, title, x + (panel_w - bbox_w(title_box)) // 2, content_y, title_font, hex_to_rgba(color, 255), faux_bold=True)
        draw_text_with_soft_shadow(draw, value, x + (panel_w - bbox_w(value_box)) // 2, content_y + bbox_h(title_box) + content_gap, shared_value_font, hex_to_rgba(color, 255), faux_bold=True)
    arrow_y = (panel_top + panel_bottom) // 2
    arrow_x1 = before_x + panel_w + panel_gap
    arrow_x2 = after_x - panel_gap
    draw.line((arrow_x1, arrow_y, arrow_x2, arrow_y), fill=(255, 255, 255, 235), width=max(2, int(height * 0.01)))
    draw.polygon(((arrow_x2, arrow_y), (arrow_x2 - 10, arrow_y - 7), (arrow_x2 - 10, arrow_y + 7)), fill=(255, 255, 255, 235))
    draw.line((pad, int(height * 0.75), width - pad, int(height * 0.75)), fill=(255, 255, 255, 255), width=1)
    caption_font = fit_pil_font(caption, font_path, int(height * 0.115), width - pad * 2, min_size=20)
    caption_box = text_bbox(draw, caption, caption_font)
    draw_text_with_soft_shadow(draw, caption, (width - bbox_w(caption_box)) // 2, int(height * 0.80), caption_font, (255, 255, 255, 255), faux_bold=True)


def draw_process_flow_sticker(draw: Any, size: tuple[int, int], label: str, steps: list[str], caption: str, accent: str, font_path: str) -> None:
    width, height = size
    steps = [compact_caption(step, 8) for step in steps if step][:4]
    if len(steps) < 2:
        steps = (steps + ["完成输出"])[:2]
    pad = max(20, int(width * 0.04))
    label_font = fit_pil_font(label, font_path, int(height * 0.095), width - pad * 2, min_size=18)
    draw_text_with_soft_shadow(draw, label, pad, int(height * 0.07), label_font, (245, 245, 243, 255), faux_bold=True)
    route_y = int(height * 0.35)
    node_radius = max(20, int(height * 0.105))
    cell_w = (width - pad * 2) / len(steps)
    centers = [int(pad + cell_w * (index + 0.5)) for index in range(len(steps))]
    step_gutter = max(16, int(cell_w * 0.13))
    step_lines = [split_cjk_lines(step, 4, max_lines=2) for step in steps]
    all_step_lines = [line for lines in step_lines for line in lines]
    shared_step_font = fit_shared_pil_font(
        all_step_lines,
        font_path,
        int(height * 0.115),
        max(64, int(cell_w) - step_gutter * 2),
        min_size=20,
    )
    for index in range(len(centers) - 1):
        x1 = centers[index] + node_radius
        x2 = centers[index + 1] - node_radius
        draw.line((x1, route_y, x2, route_y), fill=hex_to_rgba(accent, 255), width=max(2, int(height * 0.009)))
        draw.polygon(((x2, route_y), (x2 - 9, route_y - 6), (x2 - 9, route_y + 6)), fill=hex_to_rgba(accent, 255))
    for index, (center_x, lines) in enumerate(zip(centers, step_lines), start=1):
        active = index == len(steps)
        fill = hex_to_rgba(accent, 255)
        draw.ellipse((center_x - node_radius, route_y - node_radius, center_x + node_radius, route_y + node_radius), fill=fill)
        number = f"{index:02d}"
        number_font = fit_pil_font(number, font_path, int(node_radius * 0.82), node_radius * 2 - 8, min_size=18)
        number_box = text_bbox(draw, number, number_font)
        number_color = (8, 9, 10, 255) if active else hex_to_rgba(accent, 255)
        draw_text_top(draw, number, center_x - bbox_w(number_box) // 2, route_y - bbox_h(number_box) // 2, number_font, number_color, faux_bold=True)
        step_y = route_y + node_radius + int(height * 0.045)
        step_line_gap = max(3, int(height * 0.015))
        for line in lines:
            step_box = text_bbox(draw, line, shared_step_font)
            draw_text_with_soft_shadow(draw, line, center_x - bbox_w(step_box) // 2, step_y, shared_step_font, (255, 255, 255, 255), faux_bold=True)
            step_y += bbox_h(step_box) + step_line_gap
    line_y = int(height * 0.79)
    draw.line((pad, line_y, width - pad, line_y), fill=(255, 255, 255, 255), width=1)
    caption_font = fit_pil_font(caption, font_path, int(height * 0.10), width - pad * 2, min_size=18)
    caption_box = text_bbox(draw, caption, caption_font)
    draw_text_with_soft_shadow(draw, caption, (width - bbox_w(caption_box)) // 2, int(height * 0.835), caption_font, (248, 248, 246, 255), faux_bold=True)


def draw_source_quote_sticker(draw: Any, size: tuple[int, int], label: str, quote: str, source: str, accent: str, font_path: str) -> None:
    width, height = size
    pad_x = max(26, int(width * 0.075))
    quote_mark_font = load_font(font_path, max(30, int(height * 0.22)))
    draw_text_top(draw, "“", pad_x, int(height * 0.04), quote_mark_font, hex_to_rgba(accent, 255), faux_bold=True)
    label_font = fit_pil_font(label, font_path, int(height * 0.085), width - pad_x * 2 - 60, min_size=17)
    draw_text_with_soft_shadow(draw, label, pad_x + int(height * 0.20), int(height * 0.12), label_font, (248, 248, 246, 255), faux_bold=True)
    lines = split_cjk_lines(quote, 10, max_lines=2)
    quote_font = fit_pil_font(max(lines, key=len), font_path, int(height * 0.19), width - pad_x * 2, min_size=26)
    quote_y = int(height * 0.34)
    line_gap = max(8, int(height * 0.055))
    for line in lines:
        draw_text_with_soft_shadow(draw, line, pad_x + int(height * 0.08), quote_y, quote_font, (255, 255, 255, 255), faux_bold=True)
        quote_y += bbox_h(text_bbox(draw, line, quote_font)) + line_gap
    source_font = fit_pil_font(source, font_path, int(height * 0.105), width - pad_x * 2, min_size=19)
    source_box = text_bbox(draw, source, source_font)
    source_y = min(
        int(height * 0.80),
        quote_y + max(4, int(height * 0.015)),
        height - bbox_h(source_box) - int(height * 0.09),
    )
    draw_text_with_soft_shadow(draw, source, pad_x + int(height * 0.08), source_y, source_font, hex_to_rgba(accent, 255), faux_bold=True)


def split_cjk_lines(text: str, max_chars: int, max_lines: int = 2) -> list[str]:
    cleaned = clean_text(text)
    if len(cleaned) <= max_chars:
        return [cleaned]
    if max_lines == 2 and len(cleaned) <= max_chars * 2:
        midpoint = math.ceil(len(cleaned) / 2)
        return [cleaned[:midpoint], cleaned[midpoint:]]
    lines = [cleaned[index : index + max_chars] for index in range(0, len(cleaned), max_chars)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(1, max_chars - 1)] + "…"
    return lines


def split_balanced_lines(text: str) -> list[str]:
    cleaned = clean_text(text)
    candidates: list[tuple[float, str, str]] = []
    for index in range(1, len(cleaned)):
        if cleaned[index - 1].isascii() and cleaned[index - 1].isalnum() and cleaned[index].isascii() and cleaned[index].isalnum():
            continue
        left = cleaned[:index].strip()
        right = cleaned[index:].strip()
        if not left or not right:
            continue
        left_width = display_width_units(left)
        right_width = display_width_units(right)
        score = max(left_width, right_width) + abs(left_width - right_width) * 0.2
        candidates.append((score, left, right))
    if not candidates:
        return [cleaned]
    _, left, right = min(candidates, key=lambda item: item[0])
    return [left, right]


def load_font(font_path: str, size: int) -> Any:
    from PIL import ImageFont

    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def fit_pil_font(text: str, font_path: str, base_size: int, max_width: int, min_size: int) -> Any:
    from PIL import Image, ImageDraw

    probe = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(probe)
    for size in range(max(base_size, min_size), min_size - 1, -2):
        font = load_font(font_path, size)
        if bbox_w(text_bbox(draw, text, font)) <= max_width:
            return font
    return load_font(font_path, min_size)


def fit_shared_pil_font(texts: list[str], font_path: str, base_size: int, max_width: int, min_size: int) -> Any:
    from PIL import Image, ImageDraw

    probe = Image.new("RGBA", (10, 10))
    draw = ImageDraw.Draw(probe)
    normalized = [str(text) for text in texts if str(text)] or [""]
    for size in range(max(base_size, min_size), min_size - 1, -2):
        font = load_font(font_path, size)
        if all(bbox_w(text_bbox(draw, text, font)) <= max_width for text in normalized):
            return font
    return load_font(font_path, min_size)


def centered_text_x_by_ink(text: str, font: Any, container_width: int) -> int:
    from PIL import Image, ImageDraw

    probe = Image.new("L", (max(1, container_width), max(1, int(font.size * 1.5))))
    draw = ImageDraw.Draw(probe)
    box = draw.textbbox((0, 0), text, font=font)
    mask = Image.new("L", (max(1, bbox_w(box)), max(1, bbox_h(box))))
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.text((-box[0], -box[1]), text, font=font, fill=255)
    ink_box = mask.getbbox()
    if not ink_box:
        return max(2, int((container_width - bbox_w(box)) / 2))
    ink_width = ink_box[2] - ink_box[0]
    return max(2, int(round((container_width - ink_width) / 2 - ink_box[0])))


def text_bbox(draw: Any, text: str, font: Any) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def bbox_w(box: tuple[int, int, int, int]) -> int:
    return box[2] - box[0]


def bbox_h(box: tuple[int, int, int, int]) -> int:
    return box[3] - box[1]


def draw_text_with_soft_shadow(
    draw: Any,
    text: str,
    x: int,
    y: int,
    font: Any,
    fill: tuple[int, int, int, int],
    faux_bold: bool = False,
) -> None:
    shadow_alpha = min(220, max(130, int(fill[3] * 0.78)))
    shadow_offset = max(2, int(font.size * 0.045))
    draw_text_top(draw, text, x + shadow_offset, y + shadow_offset, font, (0, 0, 0, shadow_alpha), faux_bold=faux_bold)
    draw_text_top(draw, text, x, y, font, fill, faux_bold=faux_bold)


def draw_text_top(
    draw: Any,
    text: str,
    x: int,
    y: int,
    font: Any,
    fill: tuple[int, int, int, int],
    stroke_fill: tuple[int, int, int, int] | None = None,
    stroke_width: int = 0,
    faux_bold: bool = False,
) -> None:
    box = text_bbox(draw, text, font)
    draw_x = x - box[0]
    draw_y = y - box[1]
    draw.text((draw_x, draw_y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
    if faux_bold:
        for offset_x, offset_y in ((1, 0), (0, 1), (1, 1)):
            draw.text((draw_x + offset_x, draw_y + offset_y), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def draw_cjk_compact_top(
    draw: Any,
    text: str,
    x: int,
    y: int,
    font: Any,
    fill: tuple[int, int, int, int],
    faux_bold: bool = False,
) -> None:
    if not text or not all(re.match(r"[\u4e00-\u9fff]", char) for char in text):
        draw_text_top(draw, text, x, y, font, fill, faux_bold=faux_bold)
        return
    box = text_bbox(draw, text, font)
    cursor = x
    advance = max(1, int(font.size * 0.92))
    for char in text:
        char_box = text_bbox(draw, char, font)
        char_x = cursor - char_box[0]
        char_y = y - char_box[1]
        draw.text((char_x, char_y), char, font=font, fill=fill)
        if faux_bold:
            draw.text((char_x + 1, char_y), char, font=font, fill=fill)
        cursor += advance


def hex_to_rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def build_native_filters(stickers_doc: dict[str, Any]) -> str:
    video_meta = stickers_doc.get("video", {})
    default_style = str(stickers_doc.get("options", {}).get("style") or "glass")
    width = int(video_meta.get("width") or 1920)
    height = int(video_meta.get("height") or 1080)
    font = find_font()
    filters: list[str] = []
    for index, sticker in enumerate(stickers_doc.get("stickers", [])):
        geom = native_card_geometry(sticker, width, height)
        start = float(sticker.get("start", 0))
        end = start + float(sticker.get("duration", 3))
        enable = f"between(t\\,{start:.2f}\\,{end:.2f})"
        style = str(sticker.get("style") or default_style)
        display_mode = str(sticker.get("displayMode") or "rectangle")
        accent = native_accent(sticker, style)
        x, y, card_w, card_h = geom["x"], geom["y"], geom["w"], geom["h"]
        pad_x = max(18, int(card_w * (0.09 if display_mode == "square" else 0.06)))
        pad_y = max(14, int(card_h * (0.12 if display_mode == "square" else 0.14)))
        vertical = height > width
        label_size = int(card_h * (0.15 if vertical else 0.16))
        headline_base = int(card_h * (0.31 if vertical else (0.32 if style == "neon-metric" else 0.34)))
        caption_size = int(card_h * (0.16 if vertical else 0.15))
        if display_mode == "square":
            label_size = int(card_h * 0.115)
            headline_base = int(card_h * 0.22)
            caption_size = int(card_h * 0.105)
        elif display_mode == "text-shadow":
            label_size = int(card_h * 0.12)
            headline_base = int(card_h * 0.28)
            caption_size = int(card_h * 0.115)
        label_size = max(15, min(label_size, 24))
        caption_size = max(16, min(caption_size, 24))
        headline = str(sticker.get("headline", ""))
        unit = str(sticker.get("unit", ""))
        if unit:
            headline = f"{headline} {unit}"
        max_text_width = card_w - pad_x * 2
        headline_size = fit_font_size(headline, headline_base, max_text_width, min_size=24)
        label_y, headline_y, caption_y = native_text_layout(y, card_h, pad_y, label_size, headline_size, caption_size, display_mode)
        text_colors = native_text_colors(style, accent)
        filters.extend(native_card_filters(style, display_mode, x, y, card_w, card_h, accent, headline, headline_size, headline_y, pad_x, enable))
        draw_text = native_drawtext_shadow if display_mode == "text-shadow" else native_drawtext
        filters.extend(
            draw_text(
                text=str(sticker.get("label", "KEY POINT")),
                x=x + pad_x,
                y=label_y,
                size=label_size,
                color=text_colors["label"],
                font=font,
                enable=enable,
            )
        )
        filters.extend(
            draw_text(
                text=headline,
                x=x + pad_x,
                y=headline_y,
                size=headline_size,
                color=text_colors["headline"],
                font=font,
                enable=enable,
            )
        )
        filters.extend(
            draw_text(
                text=str(sticker.get("caption", "")),
                x=x + pad_x,
                y=caption_y,
                size=caption_size,
                color=text_colors["caption"],
                font=font,
                enable=enable,
            )
        )
    return ",".join(filters) if filters else "null"


def native_text_layout(
    y: int,
    card_h: int,
    pad_y: int,
    label_size: int,
    headline_size: int,
    caption_size: int,
    display_mode: str,
) -> tuple[int, int, int]:
    if display_mode == "square":
        label_y = y + pad_y
        headline_y = y + int(card_h * 0.36)
        caption_y = y + card_h - pad_y - caption_size - 2
        return label_y, headline_y, caption_y
    if display_mode == "text-shadow":
        label_y = y + int(card_h * 0.08)
        headline_y = y + int(card_h * 0.30)
        caption_y = headline_y + headline_size + int(card_h * 0.13)
        return label_y, headline_y, caption_y
    label_y = y + pad_y
    headline_y = y + int(card_h * 0.39)
    caption_y = y + card_h - pad_y - caption_size
    return label_y, headline_y, caption_y


def native_accent(sticker: dict[str, Any], style: str) -> str:
    theme_color = str(sticker.get("themeColor", ""))
    if style == "glass" and theme_color in ACCENTS:
        return ACCENTS[theme_color]
    return ACCENTS.get(str(sticker.get("accent", theme_color or "gold")), ACCENTS["gold"])


def native_text_colors(style: str, accent: str) -> dict[str, str]:
    if style == "solid-banner":
        return {"label": "black@0.86", "headline": "white", "caption": "white@0.92"}
    if style == "editorial-cutout":
        return {"label": accent, "headline": "black", "caption": "black@0.78"}
    if style == "neon-metric":
        return {"label": accent, "headline": "white", "caption": "white@0.88"}
    return {"label": accent, "headline": "white", "caption": "white@0.88"}


def native_card_filters(
    style: str,
    display_mode: str,
    x: int,
    y: int,
    card_w: int,
    card_h: int,
    accent: str,
    headline: str,
    headline_size: int,
    headline_y: int,
    pad_x: int,
    enable: str,
) -> list[str]:
    filters: list[str] = []
    if display_mode == "text-shadow":
        return filters

    if style == "solid-banner":
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color={accent}@0.88:t=fill:enable='{enable}'"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={max(8, int(card_w * 0.022))}:h={card_h}:"
            f"color=black@0.72:t=fill:enable='{enable}'"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color=white@0.92:t=2:enable='{enable}'"
        )
        return filters

    if style == "editorial-cutout":
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color=white@0.90:t=fill:enable='{enable}'"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color=black@0.80:t=2:enable='{enable}'"
        )
        underline_w = min(card_w - pad_x * 2, int(display_width_units(headline) * headline_size * 0.58))
        filters.append(
            "drawbox="
            f"x={x + pad_x}:y={headline_y + headline_size + 3}:w={max(80, underline_w)}:h={max(5, int(card_h * 0.035))}:"
            f"color={accent}@0.86:t=fill:enable='{enable}'"
        )
        return filters

    if style == "neon-metric":
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color=black@0.78:t=fill:enable='{enable}'"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={card_w}:h={card_h}:"
            f"color={accent}@0.98:t=3:enable='{enable}'"
        )
        filters.append(
            "drawbox="
            f"x={x}:y={y}:w={max(7, int(card_w * 0.018))}:h={card_h}:"
            f"color={accent}@0.95:t=fill:enable='{enable}'"
        )
        fill_w = min(card_w - pad_x * 2, int(display_width_units(headline) * headline_size * 0.64) + 22)
        fill_h = headline_size + max(18, int(card_h * 0.16))
        fill_y = headline_y - max(9, int(card_h * 0.075))
        filters.append(
            "drawbox="
            f"x={x + pad_x - 7}:y={fill_y}:w={max(90, fill_w)}:h={fill_h}:"
            f"color={accent}@0.22:t=fill:enable='{enable}'"
        )
        return filters

    filters.append(
        "drawbox="
        f"x={x}:y={y}:w={card_w}:h={card_h}:"
        f"color=black@0.68:t=fill:enable='{enable}'"
    )
    filters.append(
        "drawbox="
        f"x={x}:y={y}:w={card_w}:h={card_h}:"
        f"color={accent}@0.95:t=3:enable='{enable}'"
    )
    return filters


def native_card_geometry(sticker: dict[str, Any], width: int, height: int) -> dict[str, int]:
    position = str(sticker.get("position", "left-focus"))
    display_mode = str(sticker.get("displayMode") or "rectangle")
    vertical = height > width
    if vertical:
        if display_mode == "square":
            card_w = int(width * 0.30)
            card_h = card_w
        elif display_mode == "text-shadow":
            card_w = int(width * 0.40)
            card_h = int(height * 0.15)
        elif display_mode == "numeric":
            card_w = int(width * 0.42)
            card_h = int(height * 0.16)
        elif display_mode == "opinion":
            card_w = int(width * 0.44)
            card_h = int(height * 0.15)
        elif display_mode == "parallel-points":
            card_w = int(width * 0.50)
            card_h = int(height * 0.25)
        elif display_mode == "comparison":
            card_w = int(width * 0.48)
            card_h = int(height * 0.18)
        elif display_mode == "process-flow":
            card_w = int(width * 0.52)
            card_h = int(height * 0.20)
        elif display_mode == "source-quote":
            card_w = int(width * 0.46)
            card_h = int(height * 0.18)
        else:
            card_w = int(width * 0.39)
            card_h = int(height * 0.13)
        x_left = int(width * 0.055)
        x_right = width - card_w - int(width * 0.055)
        y_top = int(height * 0.04)
        x = x_right if position == "top-right" else x_left
        y = y_top
    else:
        if display_mode == "square":
            card_h = int(height * 0.30)
            card_w = card_h
        elif display_mode == "text-shadow":
            card_w = adaptive_text_sticker_width(sticker, width, height)
            card_h = int(height * 0.23)
        elif display_mode == "numeric":
            card_w = int(width * 0.32)
            card_h = int(height * 0.25)
        elif display_mode == "opinion":
            card_h = int(height * 0.21)
            card_w = adaptive_opinion_width(sticker, width, card_h)
        elif display_mode == "parallel-points":
            card_h = int(height * 0.30)
            card_w = adaptive_parallel_points_width(sticker, width, card_h)
        elif display_mode == "comparison":
            card_w = int(width * 0.31)
            card_h = int(height * 0.27)
        elif display_mode == "process-flow":
            card_w = int(width * 0.34)
            card_h = int(height * 0.28)
        elif display_mode == "source-quote":
            card_w = int(width * 0.30)
            card_h = int(height * 0.27)
        else:
            card_h = int(height * 0.22)
            card_w = adaptive_rectangle_width(sticker, width, card_h)
        x_left = int(width * 0.06)
        x_right = width - card_w - int(width * 0.06)
        y_map = {
            "left-stack-1": int(height * 0.09),
            "left-stack-2": int(height * 0.36),
            "left-stack-3": int(height * 0.60),
            "right-stack-1": int(height * 0.09),
            "right-stack-2": int(height * 0.36),
            "right-stack-3": int(height * 0.60),
            "left-focus": int(height * 0.24),
            "right-focus": int(height * 0.24),
            "right-lower-1": int(height * 0.52),
            "right-lower-2": int(height * 0.67),
            "left-lower-1": int(height * 0.52),
            "left-lower-2": int(height * 0.67),
        }
        x = x_right if position.startswith("right") else x_left
        y = y_map.get(position, int(height * 0.24))
    if sticker.get("layoutX") is not None and sticker.get("layoutY") is not None:
        x = int(sticker["layoutX"])
        y = int(sticker["layoutY"])
    if sticker.get("layoutW") is not None:
        card_w = int(sticker["layoutW"])
    if sticker.get("layoutH") is not None:
        card_h = int(sticker["layoutH"])
    return {"x": x, "y": y, "w": card_w, "h": card_h}


def adaptive_rectangle_width(sticker: dict[str, Any], video_width: int, card_h: int) -> int:
    label = str(sticker.get("label") or "KEY POINT")
    headline = str(sticker.get("headline") or "")
    unit = str(sticker.get("unit") or "")
    caption = str(sticker.get("caption") or "")
    if unit:
        headline = f"{headline} {unit}"
    label_size = int(card_h * 0.16)
    headline_size = int(card_h * 0.34)
    caption_size = int(card_h * 0.18)
    content_w = max(
        estimated_text_px(label, label_size, uppercase_scale=0.55),
        estimated_text_px(headline, headline_size, uppercase_scale=0.58),
        estimated_text_px(caption, caption_size, uppercase_scale=0.70),
    )
    horizontal_padding = int(card_h * 0.62)
    return max(int(video_width * 0.20), min(int(video_width * 0.30), int(content_w + horizontal_padding)))


def adaptive_text_sticker_width(sticker: dict[str, Any], video_width: int, height: int) -> int:
    label = str(sticker.get("label") or "KEY POINT")
    headline = str(sticker.get("headline") or "")
    caption = str(sticker.get("caption") or "")
    card_h = int(height * 0.23)
    content_w = max(
        estimated_text_px(label, int(card_h * 0.16), uppercase_scale=0.56),
        estimated_text_px(headline, int(card_h * 0.36), uppercase_scale=0.62),
        estimated_text_px(caption, int(card_h * 0.18), uppercase_scale=0.72),
    )
    return max(int(video_width * 0.20), min(int(video_width * 0.32), int(content_w + card_h * 0.40)))


def adaptive_opinion_width(sticker: dict[str, Any], video_width: int, card_h: int) -> int:
    label = str(sticker.get("label") or "KEY TAKEAWAY")
    headline = str(sticker.get("headline") or "")
    caption = str(sticker.get("caption") or "")
    content_w = max(
        estimated_text_px(label, int(card_h * 0.13), uppercase_scale=0.56),
        estimated_text_px(headline, int(card_h * 0.36), uppercase_scale=0.62),
        estimated_text_px(caption, int(card_h * 0.17), uppercase_scale=0.68),
    )
    return max(int(video_width * 0.22), min(int(video_width * 0.34), int(content_w + card_h * 0.30)))


def adaptive_parallel_points_width(sticker: dict[str, Any], video_width: int, card_h: int) -> int:
    points = sticker_points(sticker)[:3]
    point_count = max(2, len(points))
    row_gap = max(12, int(card_h * 0.055))
    available_h = card_h - int(card_h * 0.25) - int(card_h * 0.08) - row_gap * (point_count - 1)
    row_h = max(38, int(available_h / point_count))
    text_size = int(row_h * 0.60)
    content_w = max(
        (estimated_text_px(point, text_size, uppercase_scale=0.58) for point in points),
        default=0,
    )
    badge_and_padding = max(42, int(row_h * 0.72)) + max(18, int(card_h * 0.06)) + int(card_h * 0.34)
    return max(int(video_width * 0.21), min(int(video_width * 0.32), int(content_w + badge_and_padding)))


def estimated_text_px(text: str, size: int, uppercase_scale: float = 0.62) -> float:
    width = 0.0
    for char in text:
        if re.match(r"[\u4e00-\u9fff]", char):
            width += size * 1.0
        elif char.isspace():
            width += size * 0.32
        elif char.isupper() or char.isdigit():
            width += size * uppercase_scale
        else:
            width += size * 0.54
    return width


def native_drawtext(text: str, x: int, y: int, size: int, color: str, font: str, enable: str) -> list[str]:
    return [native_drawtext_filter(text, x, y, size, color, font, enable)]


def native_drawtext_shadow(text: str, x: int, y: int, size: int, color: str, font: str, enable: str) -> list[str]:
    return [
        native_drawtext_filter(text, x + 3, y + 3, size, "black@0.72", font, enable),
        native_drawtext_filter(text, x + 1, y + 1, size, "black@0.88", font, enable),
        native_drawtext_filter(text, x, y, size, color, font, enable),
    ]


def native_drawtext_filter(text: str, x: int, y: int, size: int, color: str, font: str, enable: str) -> str:
    return (
        "drawtext="
        f"fontfile='{escape_filter(font)}':"
        f"text='{escape_filter(text)}':"
        f"x={x}:y={y}:"
        f"fontsize={size}:fontcolor={color}:"
        f"enable='{enable}'"
    )


def find_font() -> str:
    candidates = [
        "/Library/Fonts/Microsoft YaHei.ttf",
        "/Library/Fonts/Microsoft YaHei UI.ttf",
        str(Path.home() / "Library/Fonts/Microsoft YaHei.ttf"),
        str(Path.home() / "Library/Fonts/Microsoft YaHei UI.ttf"),
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return ""


def fit_font_size(text: str, base_size: int, max_width: int, min_size: int) -> int:
    estimated_width = display_width_units(text) * base_size * 0.68
    if estimated_width <= max_width:
        return max(min_size, base_size)
    fitted = int(base_size * max_width / max(estimated_width, 1))
    return max(min_size, min(base_size, fitted))


def display_width_units(text: str) -> float:
    units = 0.0
    for char in text:
        if re.match(r"[\u4e00-\u9fff]", char):
            units += 1.75
        elif char.isspace():
            units += 0.45
        elif char.isupper() or char.isdigit():
            units += 1.0
        else:
            units += 1.05
    return max(units, 1.0)


def escape_filter(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("%", "\\%")
    )


def render_with_hyperframes(composition: Path, output: Path, config: dict[str, Any]) -> None:
    render_command = str(config.get("render", {}).get("command") or "").strip()
    if render_command:
        command = (
            render_command.replace("{html}", shlex.quote(str(composition)))
            .replace("{output}", shlex.quote(str(output)))
        )
        subprocess.run(command, shell=True, check=True)
        return

    executable = shutil.which("hyperframes") or shutil.which("hypeframes")
    if not executable:
        raise SystemExit(
            "No HyperFrames/HypeFrames CLI found. composition.html was generated; install the renderer "
            "or set config render.command with {html} and {output} placeholders."
        )
    subprocess.run([executable, "render", str(composition), str(output)], check=True)


def command_init_config(args: argparse.Namespace) -> None:
    config_path = Path(args.config).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not args.force:
        raise SystemExit(f"Config already exists: {config_path}. Use --force to overwrite.")
    config = {
        "jiekou": {
            "endpoint": DEFAULT_JIEKOU_ENDPOINT,
            "api_key": "REPLACE_WITH_ROTATED_JIEKOU_API_KEY",
            "request_defaults": DEFAULT_SCRIBE_PARAMS,
        },
        "upload": {
            "provider": "tmpfiles",
            "command": "",
        },
        "render": {
            "command": "",
        },
        "defaults": {
            "style": "glass",
            "display_mode": "mixed",
            "theme_color": "blue",
            "density": "medium",
            "layout_mode": "auto",
            "motion": "smart",
        },
    }
    write_json(config_path, config)
    config_path.chmod(0o600)
    print(config_path)


def command_run(args: argparse.Namespace) -> None:
    video = Path(args.video).expanduser()
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")

    config = load_config(Path(args.config).expanduser())
    defaults = config.get("defaults", {})
    style = args.style or str(defaults.get("style") or "glass")
    display_mode = args.display_mode or str(defaults.get("display_mode") or "rectangle")
    theme_color = args.theme_color or str(defaults.get("theme_color") or "blue")
    density = args.density or str(defaults.get("density") or "medium")
    layout_mode = args.layout_mode or str(defaults.get("layout_mode") or "auto")
    motion = args.motion or str(defaults.get("motion") or "smart")
    if style not in STYLES:
        raise SystemExit(f"Unknown style: {style}. Choose one of: {', '.join(STYLES)}")
    if display_mode not in DISPLAY_MODES:
        raise SystemExit(f"Unknown display mode: {display_mode}. Choose one of: {', '.join(DISPLAY_MODES)}")
    if theme_color not in THEME_COLORS:
        raise SystemExit(f"Unknown theme color: {theme_color}. Choose one of: {', '.join(THEME_COLORS)}")
    if density not in DENSITY_LEVELS:
        raise SystemExit(f"Unknown density: {density}. Choose one of: {', '.join(DENSITY_LEVELS)}")
    if layout_mode not in LAYOUT_MODES:
        raise SystemExit(f"Unknown layout mode: {layout_mode}. Choose one of: {', '.join(LAYOUT_MODES)}")
    if motion not in MOTION_MODES:
        raise SystemExit(f"Unknown motion mode: {motion}. Choose one of: {', '.join(MOTION_MODES)}")
    metadata = probe_video(video)
    subtitle_source = require_subtitle_source(metadata, args.confirm_burned_in_subtitles)
    metadata["subtitle_source"] = subtitle_source
    metadata["subtitles_required"] = True
    out_dir = Path(args.out_dir).expanduser() if args.out_dir else Path("out/video-stickers") / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    max_stickers = args.max_stickers or density_sticker_limit(metadata, density)
    write_json(out_dir / "metadata.json", metadata)

    transcript_path = out_dir / "transcript.words.json"
    if args.transcript:
        transcript = read_json(Path(args.transcript).expanduser())
        write_json(transcript_path, transcript)
    elif transcript_path.exists() and args.reuse_transcript:
        transcript = read_json(transcript_path)
    elif args.subtitle_file:
        transcript = parse_subtitle_file(Path(args.subtitle_file).expanduser())
        write_json(transcript_path, transcript)
    elif subtitle_source == "embedded":
        extracted_subtitles = out_dir / "subtitles.srt"
        extract_embedded_subtitles(video, extracted_subtitles)
        transcript = parse_subtitle_file(extracted_subtitles)
        write_json(transcript_path, transcript)
    elif not args.allow_audio_transcription and not args.cloud_storage_url:
        raise SystemExit(
            "Burned-in subtitles were confirmed, but the CLI cannot reliably read subtitle pixels. "
            "Provide OCR/subtitle timing with --subtitle-file or --transcript. "
            "Use --allow-audio-transcription only as an explicit timing fallback."
        )
    else:
        audio_path = out_dir / "audio.wav"
        if not args.cloud_storage_url:
            if args.skip_audio:
                raise SystemExit("--skip-audio requires --cloud-storage-url when audio transcription is enabled.")
            extract_audio(video, audio_path)
        cloud_url = args.cloud_storage_url or upload_audio(audio_path, config)
        transcript = call_scribe(cloud_url, config, out_dir)

    stickers_doc = make_stickers(
        transcript,
        metadata,
        max_stickers,
        person_side=args.person_side,
        style=style,
        display_mode=display_mode,
        theme_color=theme_color,
        density=density,
        layout_mode=layout_mode,
        motion=motion,
    )
    stickers_path = out_dir / "stickers.json"
    write_json(stickers_path, stickers_doc)
    write_review(stickers_doc, out_dir / "stickers-review.md")

    composition_path = out_dir / "composition.html"
    render_composition(video, stickers_doc, composition_path)

    if args.render_native:
        output = Path(args.output).expanduser() if args.output else out_dir / f"{video.stem}-stickers.mp4"
        render_native_video(video, stickers_doc, output)
        print(output)
    elif args.render:
        output = Path(args.output).expanduser() if args.output else out_dir / f"{video.stem}-stickers.mp4"
        render_with_hyperframes(composition_path, output, config)
        print(output)
    else:
        print(composition_path)


def command_from_transcript(args: argparse.Namespace) -> None:
    transcript = read_json(Path(args.transcript).expanduser())
    metadata = read_json(Path(args.metadata).expanduser())
    max_stickers = args.max_stickers or density_sticker_limit(metadata, args.density)
    stickers_doc = make_stickers(
        transcript,
        metadata,
        max_stickers,
        person_side=args.person_side,
        style=args.style,
        display_mode=args.display_mode,
        theme_color=args.theme_color,
        density=args.density,
        layout_mode=args.layout_mode,
        motion=args.motion,
    )
    write_json(Path(args.output).expanduser(), stickers_doc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate transcript-driven video stickers.")
    parser.set_defaults(func=None)
    sub = parser.add_subparsers(dest="command")

    init_config = sub.add_parser("init-config", help="Create a private config template.")
    init_config.add_argument("--config", default=str(DEFAULT_CONFIG))
    init_config.add_argument("--force", action="store_true")
    init_config.set_defaults(func=command_init_config)

    run = sub.add_parser("run", help="Run the full video sticker pipeline.")
    run.add_argument("video")
    run.add_argument("--out-dir")
    run.add_argument("--config", default=str(DEFAULT_CONFIG))
    run.add_argument("--cloud-storage-url")
    run.add_argument("--transcript", help="Use an existing normalized transcript JSON.")
    run.add_argument("--subtitle-file", help="Use an SRT or VTT file as the timed text source.")
    run.add_argument(
        "--confirm-burned-in-subtitles",
        action="store_true",
        help="Confirm that subtitles are visibly burned into the video when no subtitle stream exists.",
    )
    run.add_argument(
        "--allow-audio-transcription",
        action="store_true",
        help="Explicitly allow audio transcription as a fallback after the subtitle requirement is satisfied.",
    )
    run.add_argument("--reuse-transcript", action="store_true")
    run.add_argument("--skip-audio", action="store_true")
    run.add_argument("--max-stickers", type=int, help="Override density-based sticker count.")
    run.add_argument("--person-side", choices=["center", "left", "right"], default="center", help="Where the speaker/main subject is; used to avoid covering the person.")
    run.add_argument("--layout-mode", choices=LAYOUT_MODES, help="How to distribute sticker positions.")
    run.add_argument("--style", choices=STYLES, help="Sticker visual style.")
    run.add_argument("--display-mode", choices=DISPLAY_MODES, help="Sticker shape/display mode.")
    run.add_argument("--theme-color", choices=THEME_COLORS, help="Theme color, especially for the glass style.")
    run.add_argument("--density", choices=DENSITY_LEVELS, help="Sticker information frequency.")
    run.add_argument("--motion", choices=MOTION_MODES, help="Sticker motion: smart animation or off.")
    run.add_argument("--render", action="store_true")
    run.add_argument("--render-native", action="store_true", help="Render a final MP4 by overlaying Pillow-rendered PNG stickers with ffmpeg.")
    run.add_argument("--output")
    run.set_defaults(func=command_run)

    from_transcript = sub.add_parser("from-transcript", help="Generate stickers.json from an existing transcript.")
    from_transcript.add_argument("transcript")
    from_transcript.add_argument("metadata")
    from_transcript.add_argument("--output", required=True)
    from_transcript.add_argument("--max-stickers", type=int)
    from_transcript.add_argument("--person-side", choices=["center", "left", "right"], default="center")
    from_transcript.add_argument("--layout-mode", choices=LAYOUT_MODES, default="auto")
    from_transcript.add_argument("--style", choices=STYLES, default="glass")
    from_transcript.add_argument("--display-mode", choices=DISPLAY_MODES, default="mixed")
    from_transcript.add_argument("--theme-color", choices=THEME_COLORS, default="blue")
    from_transcript.add_argument("--density", choices=DENSITY_LEVELS, default="medium")
    from_transcript.add_argument("--motion", choices=MOTION_MODES, default="smart")
    from_transcript.set_defaults(func=command_from_transcript)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.func is None:
        parser.print_help()
        return 2
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
