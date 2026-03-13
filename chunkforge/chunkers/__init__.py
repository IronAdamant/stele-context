"""
ChunkForge chunkers module.

Provides modality-specific chunkers for different file types:
- TextChunker: Plain text files (zero dependencies)
- CodeChunker: Code files with AST awareness (zero dependencies)
- ImageChunker: Image files (requires Pillow)
- PDFChunker: PDF files (requires pymupdf)
- AudioChunker: Audio files (requires librosa)
- VideoChunker: Video files (requires opencv)

All chunkers follow the same interface and can be registered with ChunkForge.
"""

from chunkforge.chunkers.base import BaseChunker, Chunk
from chunkforge.chunkers.text import TextChunker
from chunkforge.chunkers.code import CodeChunker

# Optional chunkers (require additional dependencies)
# Each chunker module imports successfully even without its optional dependency,
# but the constructor raises ImportError. Check the inner availability flag.
try:
    from chunkforge.chunkers.image import ImageChunker, HAS_PIL

    HAS_IMAGE_CHUNKER = HAS_PIL
except ImportError:
    HAS_IMAGE_CHUNKER = False
    ImageChunker = None  # type: ignore

try:
    from chunkforge.chunkers.pdf import PDFChunker, HAS_PYMUPDF

    HAS_PDF_CHUNKER = HAS_PYMUPDF
except ImportError:
    HAS_PDF_CHUNKER = False
    PDFChunker = None  # type: ignore

try:
    from chunkforge.chunkers.audio import AudioChunker, HAS_LIBROSA

    HAS_AUDIO_CHUNKER = HAS_LIBROSA
except ImportError:
    HAS_AUDIO_CHUNKER = False
    AudioChunker = None  # type: ignore

try:
    from chunkforge.chunkers.video import VideoChunker, HAS_OPENCV

    HAS_VIDEO_CHUNKER = HAS_OPENCV
except ImportError:
    HAS_VIDEO_CHUNKER = False
    VideoChunker = None  # type: ignore

__all__ = [
    "BaseChunker",
    "Chunk",
    "TextChunker",
    "CodeChunker",
    "ImageChunker",
    "PDFChunker",
    "AudioChunker",
    "VideoChunker",
    "HAS_IMAGE_CHUNKER",
    "HAS_PDF_CHUNKER",
    "HAS_AUDIO_CHUNKER",
    "HAS_VIDEO_CHUNKER",
]
