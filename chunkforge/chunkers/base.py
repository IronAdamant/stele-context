"""
Base chunker interface for ChunkForge.

All modality-specific chunkers inherit from BaseChunker and implement
the chunk() method to split content into semantically coherent units.
"""

import hashlib
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from chunkforge.chunkers.numpy_compat import (
    np,
    HAS_NUMPY,
    cosine_similarity,
)

# Regex tokenizer that approximates BPE segmentation (~10-15% accuracy)
# Handles camelCase, snake_case, punctuation, numbers as separate tokens
_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|[0-9]+|[^\w\s]|\s+")


@dataclass
class Chunk:
    """
    Represents a chunk of content with metadata.

    A chunk is a semantically coherent unit that can be independently
    indexed, cached, and restored. Works for any modality (text, image, etc.)
    """

    # Content
    content: Any  # str for text, bytes for binary
    modality: str  # "text", "image", "audio", "video", "pdf"

    # Position in source
    start_pos: int = 0
    end_pos: int = 0

    # Source info
    document_path: str = ""
    chunk_index: int = 0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Computed properties (lazy)
    _content_hash: Optional[str] = field(default=None, repr=False)
    _semantic_signature: Optional[Any] = field(default=None, repr=False)
    _token_count: Optional[int] = field(default=None, repr=False)
    _chunk_id: Optional[str] = field(default=None, repr=False)

    @property
    def content_hash(self) -> str:
        """SHA-256 hash of chunk content."""
        if self._content_hash is None:
            if isinstance(self.content, str):
                self._content_hash = hashlib.sha256(
                    self.content.encode("utf-8")
                ).hexdigest()
            elif isinstance(self.content, bytes):
                self._content_hash = hashlib.sha256(self.content).hexdigest()
            else:
                self._content_hash = hashlib.sha256(
                    str(self.content).encode("utf-8")
                ).hexdigest()
        return self._content_hash

    @property
    def semantic_signature(self) -> Any:
        """
        Semantic signature for similarity comparison.

        Returns a 128-dim vector using character trigrams, word frequencies,
        and structural features.
        """
        if self._semantic_signature is None:
            self._semantic_signature = self._compute_semantic_signature()
        return self._semantic_signature

    @property
    def token_count(self) -> int:
        """Estimated token count."""
        if self._token_count is None:
            self._token_count = self._estimate_token_count()
        return self._token_count

    @property
    def chunk_id(self) -> str:
        """Unique identifier for this chunk."""
        if self._chunk_id is None:
            id_string = f"{self.document_path}:{self.start_pos}:{self.end_pos}:{self.content_hash[:16]}"
            self._chunk_id = hashlib.sha256(id_string.encode("utf-8")).hexdigest()[:32]
        return self._chunk_id

    def _compute_semantic_signature(self, signature_dim: int = 128) -> Any:
        """
        Compute a rich 128-dim semantic signature.

        Layout:
          0-63:   Character trigram frequencies
          64-95:  Word frequencies (unigrams 64-79, bigrams 80-95)
          96-103: Structural features (line counts, brackets, etc.)
          104-115: Positional features (first-line keywords, indentation)
          116-127: Reserved (zero)
        """
        if not isinstance(self.content, str):
            # For binary content, pad hash-based signature to full dimension
            hash_vals = [float(ord(c)) / 255.0 for c in self.content_hash[:64]]
            return hash_vals + [0.0] * (signature_dim - len(hash_vals))

        signature = np.zeros(signature_dim, dtype=np.float32)

        # Feature 1: Character trigram frequencies (dims 0-63)
        trigrams = self._extract_trigrams()
        for i, (_, count) in enumerate(trigrams.most_common(64)):
            signature[i] = count / max(len(self.content), 1)

        # Feature 2a: Word unigram frequencies (dims 64-79)
        words = self._extract_words()
        for i, (_, count) in enumerate(words.most_common(16)):
            signature[64 + i] = count / max(len(words), 1)

        # Feature 2b: Word bigram frequencies (dims 80-95)
        bigrams = self._extract_bigrams()
        for i, (_, count) in enumerate(bigrams.most_common(16)):
            signature[80 + i] = count / max(len(bigrams), 1)

        # Feature 3: Structural features (dims 96-103)
        lines = self.content.split("\n")
        num_lines = max(len(lines), 1)
        signature[96] = len(lines) / 100.0
        signature[97] = sum(len(line) for line in lines) / max(len(self.content), 1)
        signature[98] = (
            sum(1 for line in lines if line.strip().startswith("#")) / num_lines
        )
        signature[99] = (
            sum(1 for line in lines if line.strip().startswith("def ")) / num_lines
        )
        signature[100] = (
            sum(1 for line in lines if line.strip().startswith("class ")) / num_lines
        )
        signature[101] = self.content.count("(") / max(len(self.content), 1)
        signature[102] = self.content.count("{") / max(len(self.content), 1)
        signature[103] = self.content.count("[") / max(len(self.content), 1)

        # Feature 4: Positional features (dims 104-115)
        first_line = lines[0].strip().lower() if lines else ""
        signature[104] = (
            1.0 if any(kw in first_line for kw in ("def ", "function ")) else 0.0
        )
        signature[105] = 1.0 if "class " in first_line else 0.0
        signature[106] = (
            1.0
            if any(
                kw in first_line for kw in ("import ", "from ", "#include", "require")
            )
            else 0.0
        )
        # Indentation depth distribution
        indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
        if indents:
            avg_indent = sum(indents) / len(indents)
            max_indent = max(indents)
            signature[107] = min(avg_indent / 16.0, 1.0)
            signature[108] = min(max_indent / 32.0, 1.0)
            variance = sum((d - avg_indent) ** 2 for d in indents) / len(indents)
            signature[109] = min(variance / 64.0, 1.0)
        # Content density features
        non_empty = sum(1 for line in lines if line.strip())
        signature[110] = non_empty / num_lines  # non-empty line ratio
        comment_chars = sum(1 for c in self.content if c == "#")
        signature[111] = min(comment_chars / max(len(self.content), 1) * 10, 1.0)
        # Unique word ratio (lexical diversity)
        word_list = re.findall(r"\b\w+\b", self.content.lower())
        if word_list:
            signature[112] = len(set(word_list)) / len(word_list)
        # Average word length
        if word_list:
            signature[113] = min(
                sum(len(w) for w in word_list) / len(word_list) / 10.0, 1.0
            )
        # Punctuation density
        punct_count = sum(1 for c in self.content if c in ".,;:!?")
        signature[114] = min(punct_count / max(len(self.content), 1) * 10, 1.0)
        # Numeric content ratio
        digit_count = sum(1 for c in self.content if c.isdigit())
        signature[115] = min(digit_count / max(len(self.content), 1) * 5, 1.0)

        # Normalize to unit vector
        norm = np.linalg.norm(signature)
        if norm > 0:
            if HAS_NUMPY:
                signature = signature / norm
            else:
                signature = [x / norm for x in signature]

        return signature

    def _extract_trigrams(self) -> Counter:
        """Extract character trigrams from content."""
        text = self.content.lower()
        trigrams: Counter = Counter()
        for i in range(len(text) - 2):
            trigrams[text[i : i + 3]] += 1
        return trigrams

    def _extract_words(self) -> Counter:
        """Extract word frequencies from content."""
        word_list = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", self.content.lower())
        return Counter(w for w in word_list if len(w) > 2)

    def _extract_bigrams(self) -> Counter:
        """Extract word bigram frequencies from content."""
        word_list = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", self.content.lower())
        filtered = [w for w in word_list if len(w) > 2]
        return Counter(
            f"{filtered[i]}_{filtered[i + 1]}" for i in range(len(filtered) - 1)
        )

    def _estimate_token_count(self) -> int:
        """
        Estimate token count using regex-based splitting.

        Approximates BPE tokenization by splitting on camelCase boundaries,
        snake_case parts, numbers, and punctuation. Within ~10-15% of actual
        BPE token counts for typical code and prose.
        """
        if isinstance(self.content, str):
            return max(1, len(_TOKEN_RE.findall(self.content)))
        if isinstance(self.content, bytes):
            return max(1, len(self.content) // 4)
        return 1

    def similarity(self, other: "Chunk") -> float:
        """Compute cosine similarity with another chunk."""
        return cosine_similarity(self.semantic_signature, other.semantic_signature)


class BaseChunker(ABC):
    """
    Abstract base class for modality-specific chunkers.

    All chunkers must implement:
    - chunk(): Split content into chunks
    - supported_extensions(): Return list of supported file extensions
    """

    @abstractmethod
    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """Split content into chunks."""
        pass

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """Return list of supported file extensions."""
        pass

    def can_handle(self, file_path: str) -> bool:
        """Check if this chunker can handle a file."""
        ext = Path(file_path).suffix.lower()
        return ext in self.supported_extensions()
