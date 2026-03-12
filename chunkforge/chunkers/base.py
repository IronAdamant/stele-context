"""
Base chunker interface for ChunkForge.

All modality-specific chunkers inherit from BaseChunker and implement
the chunk() method to split content into semantically coherent units.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


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
                self._content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            elif isinstance(self.content, bytes):
                self._content_hash = hashlib.sha256(self.content).hexdigest()
            else:
                # For other types, use string representation
                self._content_hash = hashlib.sha256(str(self.content).encode("utf-8")).hexdigest()
        return self._content_hash
    
    @property
    def semantic_signature(self) -> Any:
        """
        Semantic signature for similarity comparison.
        
        Returns a vector (list or numpy array) that represents the semantic
        content of this chunk. Implementation depends on modality.
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
    
    def _compute_semantic_signature(self) -> Any:
        """Compute semantic signature. Override in subclasses."""
        # Default: simple hash-based signature
        return [float(ord(c)) / 255.0 for c in self.content_hash[:32]]
    
    def _estimate_token_count(self) -> int:
        """Estimate token count. Override in subclasses."""
        if isinstance(self.content, str):
            return max(1, len(self.content) // 4)
        elif isinstance(self.content, bytes):
            return max(1, len(self.content) // 4)
        else:
            return 1
    
    def similarity(self, other: "Chunk") -> float:
        """
        Compute cosine similarity with another chunk.
        
        Args:
            other: Another chunk to compare with
            
        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        sig1 = self.semantic_signature
        sig2 = other.semantic_signature
        
        # Convert to lists if needed
        if hasattr(sig1, 'tolist'):
            sig1 = sig1.tolist()
        if hasattr(sig2, 'tolist'):
            sig2 = sig2.tolist()
        
        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(sig1, sig2))
        norm1 = sum(a * a for a in sig1) ** 0.5
        norm2 = sum(b * b for b in sig2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(dot_product / (norm1 * norm2))


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
        """
        Split content into chunks.
        
        Args:
            content: Content to chunk (str for text, bytes for binary)
            document_path: Path to source document
            **kwargs: Additional chunker-specific options
            
        Returns:
            List of Chunk objects
        """
        pass
    
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """
        Return list of supported file extensions.
        
        Returns:
            List of extensions (e.g., ['.txt', '.md'])
        """
        pass
    
    def can_handle(self, file_path: str) -> bool:
        """
        Check if this chunker can handle a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            True if this chunker can handle the file
        """
        ext = Path(file_path).suffix.lower()
        return ext in self.supported_extensions()
    
    def read_file(self, file_path: str) -> Any:
        """
        Read file content.
        
        Args:
            file_path: Path to file
            
        Returns:
            File content (str for text, bytes for binary)
        """
        path = Path(file_path)
        
        # Try text first
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (UnicodeDecodeError, ValueError):
            # Fall back to binary
            return path.read_bytes()
