"""
Text chunker for ChunkForge.

Splits plain text files into semantically coherent chunks using
paragraph boundaries and token-based splitting. Supports adaptive
chunk sizing and sliding window for overlapping chunks. Zero dependencies.
"""

import re
from typing import Any, Dict, List

from chunkforge.chunkers.base import BaseChunker, Chunk, estimate_tokens


class TextChunker(BaseChunker):
    """
    Chunker for plain text files.

    Uses paragraph boundaries and token-based splitting to create
    semantically coherent chunks. Supports adaptive chunk sizing based
    on content density and sliding window for overlapping chunks.
    Zero external dependencies.
    """

    def __init__(
        self,
        chunk_size: int = 256,
        max_chunk_size: int = 4096,
        overlap: int = 0,
        adaptive: bool = True,
    ):
        """
        Initialize text chunker.

        Args:
            chunk_size: Target tokens per chunk
            max_chunk_size: Maximum tokens per chunk
            overlap: Number of tokens to overlap between chunks (0 = no overlap)
            adaptive: Whether to adapt chunk size based on content density
        """
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap
        self.adaptive = adaptive

    def supported_extensions(self) -> List[str]:
        """Return supported text file extensions."""
        return [
            ".txt",
            ".md",
            ".markdown",
            ".rst",
            ".adoc",
            ".log",
            ".csv",
            ".tsv",
        ]

    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """
        Split text content into chunks.

        Args:
            content: Text content to chunk
            document_path: Path to source document
            **kwargs: Additional options (ignored)

        Returns:
            List of Chunk objects
        """
        if not isinstance(content, str):
            content = str(content)

        # Use sliding window if overlap > 0
        if self.overlap > 0:
            return self._chunk_sliding_window(content, document_path)

        # Use adaptive chunking if enabled
        if self.adaptive:
            return self._chunk_adaptive(content, document_path)

        # Standard paragraph-based chunking
        return self._chunk_paragraphs(content, document_path)

    def _chunk_paragraphs(self, content: str, document_path: str) -> List[Chunk]:
        """Standard paragraph-based chunking."""
        return self._chunk_by_paragraphs(content, document_path, adaptive=False)

    def _chunk_adaptive(self, content: str, document_path: str) -> List[Chunk]:
        """Adaptive chunking that adjusts size based on content density."""
        return self._chunk_by_paragraphs(content, document_path, adaptive=True)

    def _chunk_by_paragraphs(
        self, content: str, document_path: str, adaptive: bool
    ) -> List[Chunk]:
        """
        Paragraph-based chunking with optional adaptive sizing.

        When adaptive=True, chunk size is adjusted based on content density:
        dense content (code, lists) gets smaller chunks, sparse content (prose)
        gets larger chunks.
        """
        paragraphs = re.split(r"\n\s*\n", content)

        chunks: List[Chunk] = []
        current_text = ""
        current_tokens = 0
        current_start = 0
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Determine target size for this paragraph
            metadata: Dict[str, Any] = {}
            if adaptive:
                density = self._content_density(para)
                target_size = int(self.chunk_size * (1.0 - density * 0.5))
                target_size = max(
                    self.chunk_size // 2, min(target_size, self.max_chunk_size)
                )
                metadata = {"density": density, "adjusted_size": target_size}
            else:
                target_size = self.chunk_size

            para_tokens = estimate_tokens(para)

            if current_tokens + para_tokens > target_size and current_text:
                chunk = Chunk(
                    content=current_text.strip(),
                    modality="text",
                    start_pos=current_start,
                    end_pos=current_start + len(current_text),
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata=metadata,
                )
                chunks.append(chunk)
                chunk_index += 1

                current_start = current_start + len(current_text)
                current_text = para + "\n\n"
                current_tokens = para_tokens
            else:
                if current_text:
                    current_text += para + "\n\n"
                else:
                    current_text = para + "\n\n"
                current_tokens += para_tokens

        if current_text.strip():
            chunk = Chunk(
                content=current_text.strip(),
                modality="text",
                start_pos=current_start,
                end_pos=current_start + len(current_text),
                document_path=document_path,
                chunk_index=chunk_index,
                metadata=metadata,
            )
            chunks.append(chunk)

        if not chunks:
            chunks.append(
                Chunk(
                    content="",
                    modality="text",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                )
            )

        return chunks

    def _chunk_sliding_window(self, content: str, document_path: str) -> List[Chunk]:
        """
        Sliding window chunking with overlap.

        Creates overlapping chunks to ensure context continuity.
        """
        # Split into sentences for better boundaries
        sentences = re.split(r"(?<=[.!?])\s+", content)

        chunks: List[Chunk] = []
        chunk_index = 0

        # Build chunks with sliding window
        i = 0
        while i < len(sentences):
            # Collect sentences for this chunk
            chunk_sentences = []
            token_count = 0

            while i < len(sentences) and token_count < self.chunk_size:
                sentence = sentences[i]
                sentence_tokens = estimate_tokens(sentence)

                if token_count + sentence_tokens > self.max_chunk_size:
                    break

                chunk_sentences.append(sentence)
                token_count += sentence_tokens
                i += 1

            if not chunk_sentences:
                break

            # Create chunk
            chunk_content = " ".join(chunk_sentences)
            start_pos = content.find(chunk_sentences[0])
            end_pos = start_pos + len(chunk_content)

            chunk = Chunk(
                content=chunk_content,
                modality="text",
                start_pos=start_pos,
                end_pos=end_pos,
                document_path=document_path,
                chunk_index=chunk_index,
                metadata={
                    "overlap": self.overlap,
                    "sentence_count": len(chunk_sentences),
                },
            )
            chunks.append(chunk)
            chunk_index += 1

            # Move back for overlap, but always advance at least 1 sentence
            if self.overlap > 0 and i < len(sentences):
                overlap_tokens = 0
                overlap_count = 0

                for j in range(len(chunk_sentences) - 1, -1, -1):
                    sentence_tokens = estimate_tokens(chunk_sentences[j])
                    if overlap_tokens + sentence_tokens > self.overlap:
                        break
                    overlap_tokens += sentence_tokens
                    overlap_count += 1

                # Ensure at least 1 sentence of forward progress
                overlap_count = min(overlap_count, len(chunk_sentences) - 1)
                i -= overlap_count

        # Handle empty content
        if not chunks:
            chunks.append(
                Chunk(
                    content="",
                    modality="text",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                )
            )

        return chunks

    def _content_density(self, text: str) -> float:
        """
        Calculate content density (0.0 = sparse prose, 1.0 = dense code/lists).

        High density indicators:
        - Short lines
        - Many special characters
        - Indentation
        - Bullet points or numbers
        """
        if not text:
            return 0.0

        lines = text.split("\n")
        if not lines:
            return 0.0

        # Average line length (shorter = denser)
        avg_line_length = sum(len(line) for line in lines) / len(lines)
        line_score = max(0, 1.0 - avg_line_length / 80.0)

        # Special character ratio
        special_chars = sum(1 for c in text if c in "{}[]()<>:=|&%$#@!~`")
        special_score = min(1.0, special_chars / max(len(text), 1) * 10)

        # Indentation ratio
        indented_lines = sum(1 for line in lines if line.startswith((" ", "\t")))
        indent_score = indented_lines / max(len(lines), 1)

        # Bullet/number ratio
        bullet_lines = sum(1 for line in lines if re.match(r"^\s*[-*•]\s", line))
        number_lines = sum(1 for line in lines if re.match(r"^\s*\d+[.)]\s", line))
        list_score = (bullet_lines + number_lines) / max(len(lines), 1)

        # Combine scores
        density = (
            line_score * 0.3
            + special_score * 0.3
            + indent_score * 0.2
            + list_score * 0.2
        )

        return min(1.0, max(0.0, density))
