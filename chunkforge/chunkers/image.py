"""
Image chunker for ChunkForge.

Splits images into chunks for indexing and similarity comparison.
Requires Pillow (PIL) for image processing.

Install: pip install chunkforge[image]
"""

import io
from typing import Any, List, Optional

from chunkforge.chunkers.base import BaseChunker, Chunk

# Check for Pillow
try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None  # type: ignore


class ImageChunker(BaseChunker):
    """
    Chunker for image files.

    Supports:
    - Whole image as single chunk
    - Grid-based tiling for large images
    - Perceptual hashing for similarity
    - Color histogram features

    Requires: Pillow (pip install chunkforge[image])
    """

    def __init__(
        self,
        tile_size: Optional[int] = None,
        max_dimension: int = 2048,
    ):
        """
        Initialize image chunker.

        Args:
            tile_size: Size of tiles for large images (None = whole image)
            max_dimension: Maximum dimension for processing (resize if larger)
        """
        if not HAS_PIL:
            raise ImportError(
                "Pillow is required for image support. "
                "Install with: pip install chunkforge[image]"
            )

        self.tile_size = tile_size
        self.max_dimension = max_dimension

    def supported_extensions(self) -> List[str]:
        """Return supported image file extensions."""
        return [
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
            ".bmp",
            ".tiff",
            ".tif",
            ".ico",
        ]

    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """
        Split image into chunks.

        Args:
            content: Image content (bytes or file path)
            document_path: Path to source document
            **kwargs: Additional options

        Returns:
            List of Chunk objects
        """
        # Load image
        if isinstance(content, bytes):
            img = Image.open(io.BytesIO(content))
        elif isinstance(content, str):
            # Assume it's a file path
            img = Image.open(content)
        else:
            raise ValueError(f"Unsupported content type: {type(content)}")

        # Convert to RGB if necessary
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Resize if too large
        img = self._resize_if_needed(img)

        # Generate chunks
        if self.tile_size is None:
            # Single chunk for whole image
            return self._chunk_whole_image(img, document_path)
        else:
            # Tile-based chunking
            return self._chunk_tiled(img, document_path)

    def _resize_if_needed(self, img: Any) -> Any:
        """Resize image if it exceeds max_dimension."""
        width, height = img.size

        if width <= self.max_dimension and height <= self.max_dimension:
            return img

        # Calculate new size maintaining aspect ratio
        ratio = min(self.max_dimension / width, self.max_dimension / height)
        new_size = (int(width * ratio), int(height * ratio))

        return img.resize(new_size, Image.LANCZOS)

    def _chunk_whole_image(self, img: Any, document_path: str) -> List[Chunk]:
        """Create a single chunk for the whole image."""
        # Convert image to bytes for storage
        img_bytes = io.BytesIO()
        img.save(img_bytes, format="PNG")
        img_data = img_bytes.getvalue()

        # Compute perceptual hash
        phash = self._perceptual_hash(img)

        # Compute color histogram
        histogram = self._color_histogram(img)

        chunk = Chunk(
            content=img_data,
            modality="image",
            start_pos=0,
            end_pos=len(img_data),
            document_path=document_path,
            chunk_index=0,
            metadata={
                "width": img.size[0],
                "height": img.size[1],
                "mode": img.mode,
                "perceptual_hash": phash,
                "histogram": histogram,
                "format": "PNG",
            },
        )

        return [chunk]

    def _chunk_tiled(self, img: Any, document_path: str) -> List[Chunk]:
        """Create chunks from image tiles."""
        width, height = img.size
        assert self.tile_size is not None
        tile_size: int = self.tile_size

        chunks: List[Chunk] = []
        chunk_index = 0

        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                # Extract tile
                box = (x, y, min(x + tile_size, width), min(y + tile_size, height))
                tile = img.crop(box)

                # Convert tile to bytes
                tile_bytes = io.BytesIO()
                tile.save(tile_bytes, format="PNG")
                tile_data = tile_bytes.getvalue()

                # Compute features
                phash = self._perceptual_hash(tile)
                histogram = self._color_histogram(tile)

                chunk = Chunk(
                    content=tile_data,
                    modality="image",
                    start_pos=x + y * width,
                    end_pos=(x + tile_size) + (y + tile_size) * width,
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={
                        "tile_x": x,
                        "tile_y": y,
                        "width": tile.size[0],
                        "height": tile.size[1],
                        "mode": tile.mode,
                        "perceptual_hash": phash,
                        "histogram": histogram,
                        "format": "PNG",
                    },
                )
                chunks.append(chunk)
                chunk_index += 1

        return chunks

    def _perceptual_hash(self, img: Any, hash_size: int = 8) -> str:
        """
        Compute perceptual hash of image.

        Simple average hash algorithm - works offline, no dependencies.
        """
        # Convert to grayscale and resize
        if img.mode != "L":
            gray = img.convert("L")
        else:
            gray = img

        # Resize to hash_size x hash_size
        small = gray.resize((hash_size, hash_size), Image.LANCZOS)

        # Get pixel values
        pixels = list(small.getdata())

        # Compute average
        avg = sum(pixels) / len(pixels)

        # Create hash bits
        bits = "".join("1" if p > avg else "0" for p in pixels)

        # Convert to hex
        return hex(int(bits, 2))[2:].zfill(hash_size * hash_size // 4)

    def _color_histogram(self, img: Any, bins: int = 8) -> List[float]:
        """
        Compute color histogram of image.

        Returns normalized histogram as list of floats.
        """
        if img.mode == "L":
            # Grayscale
            hist = img.histogram()
            # Normalize
            total = sum(hist)
            if total > 0:
                hist = [h / total for h in hist]
            return hist[:bins]

        elif img.mode == "RGB":
            # RGB - compute per-channel histograms
            r, g, b = img.split()
            r_hist = r.histogram()
            g_hist = g.histogram()
            b_hist = b.histogram()

            # Normalize each channel
            def normalize(h: List[int]) -> List[float]:
                total = sum(h)
                if total > 0:
                    return [x / total for x in h]
                return [float(x) for x in h]

            r_norm = normalize(r_hist)
            g_norm = normalize(g_hist)
            b_norm = normalize(b_hist)

            # Combine (sample to reduce dimensionality)
            combined = []
            step = len(r_norm) // bins
            for i in range(0, len(r_norm), step):
                combined.append(r_norm[i])
                combined.append(g_norm[i])
                combined.append(b_norm[i])

            return combined[: bins * 3]

        # Fallback for unexpected modes (images are converted to RGB/L above)
        return [0.0] * bins
