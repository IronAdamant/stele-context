#!/usr/bin/env python3
"""Write the media fixture as files decoders accept.

Pillow writes the PNG, JPEG, and GIF. The stdlib ``wave`` module writes the
WAV, then ffmpeg encodes a real MP3 and an MP4 that contains a moov atom.
PyMuPDF writes the one-page PDF. Run this from the repository root:

    python3 parity/fixtures/media/generate.py
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from PIL import Image
import pymupdf

ROOT = Path(__file__).resolve().parent / "fixtures" / "media"


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (220, 30, 30)).save(ROOT / "dot.png")
    Image.new("RGB", (8, 8), (30, 180, 40)).save(ROOT / "dot.jpg", format="JPEG")
    frames = [Image.new("P", (8, 8), color=index) for index in (1, 2)]
    frames[0].save(
        ROOT / "dot.gif",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    wav_path = ROOT / "tone.wav"
    with wave.open(str(wav_path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10" * 8000)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "9",
            str(ROOT / "tone.mp3"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=16x16:d=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ROOT / "clip.mp4"),
        ],
        check=True,
        capture_output=True,
    )
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF symbols")
    doc.save(ROOT / "note.pdf")
    doc.close()


if __name__ == "__main__":
    main()
