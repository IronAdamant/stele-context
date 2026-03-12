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
try:
    from chunkforge.chunkers.image import ImageChunker
    HAS_IMAGE_CHUNKER = True
except ImportError:
    HAS_IMAGE_CHUNKER = False
    ImageChunker = None  # type: ignore

try:
    from chunkforge.chunkers.pdf import PDFChunker
    HAS_PDF_CHUNKER = True
except ImportError:
    HAS_PDF_CHUNKER = False
    PDFChunker = None  # type: ignore

try:
    from chunkforge.chunkers.audio import AudioChunker
    HAS_AUDIO_CHUNKER = True
except ImportError:
    HAS_AUDIO_CHUNKER = False
    AudioChunker = None  # type: ignore

try:
    from chunkforge.chunkers.video import VideoChunker
    HAS_VIDEO_CHUNKER = True
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
