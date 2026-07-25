#!/usr/bin/env python3
"""Generate small synthetic test-media fixtures with FFmpeg.

Test media is a shared-team dependency (spec section 20, Phase 0: "Confirm
test-media fixtures"), owned by Member C. Generating fixtures rather than
committing binaries keeps the repository small and lets every developer
reproduce the same inputs.

Fixtures deliberately cover the pipeline's branch points:
  - a normal horizontal video with audio (the golden path)
  - a vertical video (orientation handling)
  - a silent video (transcription is skipped)
  - a very short video (below the minimum moment length)
  - an unsupported container (must fail cleanly, spec 12.1)

Usage:
    python scripts/fixtures/media/generate_fixtures.py [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path(__file__).parent / "generated"


@dataclass(frozen=True)
class Fixture:
    filename: str
    description: str
    args: list[str]


def _video_args(
    *,
    width: int,
    height: int,
    duration_seconds: int,
    with_audio: bool,
    output: str,
    container_args: list[str] | None = None,
) -> list[str]:
    args = [
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=30:duration={duration_seconds}",
    ]
    if with_audio:
        args += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_seconds}",
            "-c:a",
            "aac",
            "-shortest",
        ]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(container_args or []), output]
    return args


FIXTURES: list[Fixture] = [
    Fixture(
        filename="horizontal_with_audio.mp4",
        description="1280x720, 20s, with audio — the golden-path ingestion input",
        args=_video_args(
            width=1280,
            height=720,
            duration_seconds=20,
            with_audio=True,
            output="horizontal_with_audio.mp4",
        ),
    ),
    Fixture(
        filename="vertical_with_audio.mp4",
        description="720x1280, 15s, with audio — exercises vertical orientation",
        args=_video_args(
            width=720,
            height=1280,
            duration_seconds=15,
            with_audio=True,
            output="vertical_with_audio.mp4",
        ),
    ),
    Fixture(
        filename="silent.mp4",
        description="640x360, 10s, no audio track — transcription must be skipped",
        args=_video_args(
            width=640,
            height=360,
            duration_seconds=10,
            with_audio=False,
            output="silent.mp4",
        ),
    ),
    Fixture(
        filename="very_short.mp4",
        description="640x360, 2s — shorter than the minimum moment length",
        args=_video_args(
            width=640,
            height=360,
            duration_seconds=2,
            with_audio=True,
            output="very_short.mp4",
        ),
    ),
    Fixture(
        filename="unsupported.mkv",
        description="Matroska container — must be rejected cleanly by validation",
        args=_video_args(
            width=640,
            height=360,
            duration_seconds=5,
            with_audio=False,
            output="unsupported.mkv",
        ),
    ),
]


def generate(output_dir: Path, *, force: bool = False) -> int:
    if shutil.which("ffmpeg") is None:
        print("error: ffmpeg not found on PATH.", file=sys.stderr)
        print("Install FFmpeg, or run this inside the worker container:", file=sys.stderr)
        print(
            "  docker compose -f infrastructure/docker/media/docker-compose.media.yml "
            "run --rm worker python scripts/fixtures/media/generate_fixtures.py",
            file=sys.stderr,
        )
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    for fixture in FIXTURES:
        target = output_dir / fixture.filename
        if target.exists() and not force:
            print(f"skip   {fixture.filename} (already exists; use --force to regenerate)")
            continue

        command = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", *fixture.args[:-1], str(target)]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"failed {fixture.filename}: {result.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"wrote  {fixture.filename} — {fixture.description}")

    print(f"\nFixtures written to {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--force", action="store_true", help="regenerate existing fixtures")
    args = parser.parse_args()
    return generate(args.output_dir, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
