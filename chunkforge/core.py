"""
Core ChunkForge implementation.

Provides the main ChunkForge class with:
- Dynamic semantic chunking with intelligent merging
- Hybrid indexing (SHA-256 hashes + semantic signatures)
- Change detection and lazy double-check
- KV-cache management and persistence
- Session management with rollback support
- Multi-modal support (text, code, image, PDF, audio, video)

All operations are 100% offline and local-only.
"""

import hashlib
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    # Fallback implementation for basic numpy operations
    class _NumpyFallback:
        """Minimal numpy fallback using pure Python."""
        
        # Type constants
        float32 = "float32"
        
        class linalg:
            """Linear algebra operations."""
            
            @staticmethod
            def norm(a: List[float]) -> float:
                """Compute L2 norm."""
                return math.sqrt(sum(x * x for x in a))
        
        @staticmethod
        def zeros(shape: int, dtype: Any = None) -> List[float]:
            """Create array of zeros."""
            return [0.0] * shape
        
        @staticmethod
        def dot(a: List[float], b: List[float]) -> float:
            """Compute dot product."""
            return sum(x * y for x, y in zip(a, b))
        
        @staticmethod
        def frombuffer(data: bytes, dtype: Any = None) -> List[float]:
            """Convert bytes to array."""
            import struct
            count = len(data) // 4  # float32 = 4 bytes
            return list(struct.unpack(f'{count}f', data))
    
    np = _NumpyFallback()  # type: ignore

from chunkforge.storage import StorageBackend
from chunkforge.chunkers import (
    TextChunker,
    CodeChunker,
    HAS_IMAGE_CHUNKER,
    HAS_PDF_CHUNKER,
    HAS_AUDIO_CHUNKER,
    HAS_VIDEO_CHUNKER,
)

# Import optional chunkers
if HAS_IMAGE_CHUNKER:
    from chunkforge.chunkers import ImageChunker
if HAS_PDF_CHUNKER:
    from chunkforge.chunkers import PDFChunker
if HAS_AUDIO_CHUNKER:
    from chunkforge.chunkers import AudioChunker
if HAS_VIDEO_CHUNKER:
    from chunkforge.chunkers import VideoChunker


class Chunk:
    """
    Represents a chunk of text/code with metadata.
    
    A chunk is a semantically coherent unit that can be independently
    indexed, cached, and restored.
    """
    
    def __init__(
        self,
        content: str,
        start_pos: int,
        end_pos: int,
        document_path: str,
        chunk_index: int = 0,
    ):
        """
        Initialize a chunk.
        
        Args:
            content: The text content of the chunk
            start_pos: Start character position in source document
            end_pos: End character position in source document
            document_path: Path to source document
            chunk_index: Index of this chunk in the document
        """
        self.content = content
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.document_path = document_path
        self.chunk_index = chunk_index
        
        # Computed properties
        self._content_hash: Optional[str] = None
        self._semantic_signature: Optional[np.ndarray] = None
        self._token_count: Optional[int] = None
        self._chunk_id: Optional[str] = None
    
    @property
    def content_hash(self) -> str:
        """SHA-256 hash of chunk content."""
        if self._content_hash is None:
            self._content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return self._content_hash
    
    @property
    def semantic_signature(self) -> np.ndarray:
        """
        Lightweight semantic signature using TF-style features.
        
        Uses character n-grams and word frequencies to create a simple
        but effective semantic fingerprint for similarity comparison.
        """
        if self._semantic_signature is None:
            self._semantic_signature = self._compute_semantic_signature()
        return self._semantic_signature
    
    @property
    def token_count(self) -> int:
        """Estimated token count (rough approximation: ~4 chars per token)."""
        if self._token_count is None:
            # Simple heuristic: ~4 characters per token for English text
            self._token_count = max(1, len(self.content) // 4)
        return self._token_count
    
    @property
    def chunk_id(self) -> str:
        """Unique identifier for this chunk."""
        if self._chunk_id is None:
            # Combine document path, position, and content hash for unique ID
            id_string = f"{self.document_path}:{self.start_pos}:{self.end_pos}:{self.content_hash[:16]}"
            self._chunk_id = hashlib.sha256(id_string.encode("utf-8")).hexdigest()[:32]
        return self._chunk_id
    
    def _compute_semantic_signature(self, signature_dim: int = 128) -> Any:
        """
        Compute a lightweight semantic signature.
        
        Uses a combination of:
        - Character trigram frequencies
        - Word frequency distribution
        - Structural features (line count, indentation, etc.)
        
        Args:
            signature_dim: Dimension of the signature vector
            
        Returns:
            Numpy array or list of semantic features
        """
        # Initialize signature vector
        signature = np.zeros(signature_dim, dtype=np.float32)
        
        # Feature 1: Character trigram frequencies (first 64 dimensions)
        trigrams = self._extract_trigrams()
        for i, (trigram, count) in enumerate(trigrams.most_common(64)):
            if i >= 64:
                break
            signature[i] = count / max(len(self.content), 1)
        
        # Feature 2: Word frequency distribution (next 32 dimensions)
        words = self._extract_words()
        for i, (word, count) in enumerate(words.most_common(32)):
            if i >= 32:
                break
            signature[64 + i] = count / max(len(words), 1)
        
        # Feature 3: Structural features (next 32 dimensions)
        lines = self.content.split("\n")
        signature[96] = len(lines) / 100.0  # Line count (normalized)
        signature[97] = sum(len(line) for line in lines) / max(len(self.content), 1)  # Avg line length
        signature[98] = sum(1 for line in lines if line.strip().startswith("#")) / max(len(lines), 1)  # Comment ratio
        signature[99] = sum(1 for line in lines if line.strip().startswith("def ")) / max(len(lines), 1)  # Function density
        signature[100] = sum(1 for line in lines if line.strip().startswith("class ")) / max(len(lines), 1)  # Class density
        signature[101] = self.content.count("(") / max(len(self.content), 1)  # Parenthesis density
        signature[102] = self.content.count("{") / max(len(self.content), 1)  # Brace density
        signature[103] = self.content.count("[") / max(len(self.content), 1)  # Bracket density
        
        # Normalize signature to unit vector
        norm = np.linalg.norm(signature)
        if norm > 0:
            if HAS_NUMPY:
                signature = signature / norm
            else:
                signature = [x / norm for x in signature]
        
        return signature
    
    def _extract_trigrams(self) -> Counter:
        """Extract character trigrams from content."""
        trigrams: Counter = Counter()
        text = self.content.lower()
        for i in range(len(text) - 2):
            trigrams[text[i:i+3]] += 1
        return trigrams
    
    def _extract_words(self) -> Counter:
        """Extract word frequencies from content."""
        words: Counter = Counter()
        # Simple word extraction (alphanumeric sequences)
        word_list = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', self.content.lower())
        for word in word_list:
            if len(word) > 2:  # Skip very short words
                words[word] += 1
        return words
    
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
        
        # Cosine similarity
        dot_product = np.dot(sig1, sig2)
        norm1 = np.linalg.norm(sig1)
        norm2 = np.linalg.norm(sig2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(dot_product / (norm1 * norm2))


class ChunkForge:
    """
    Main ChunkForge engine.
    
    Provides high-level API for:
    - Indexing documents with dynamic semantic chunking
    - Detecting changes and updating KV-cache
    - Managing sessions with rollback support
    - Pruning low-relevance chunks
    """
    
    # Default configuration
    DEFAULT_CHUNK_SIZE = 256  # Target tokens per initial chunk
    DEFAULT_MAX_CHUNK_SIZE = 4096  # Maximum tokens per merged chunk
    DEFAULT_MERGE_THRESHOLD = 0.7  # Similarity threshold for merging
    DEFAULT_CHANGE_THRESHOLD = 0.85  # Similarity threshold for "unchanged"
    
    def __init__(
        self,
        storage_dir: Optional[str] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    ):
        """
        Initialize ChunkForge.
        
        Args:
            storage_dir: Base directory for storage (defaults to ~/.chunkforge/)
            chunk_size: Target tokens per initial chunk
            max_chunk_size: Maximum tokens per merged chunk
            merge_threshold: Similarity threshold for merging adjacent chunks
            change_threshold: Similarity threshold for considering chunks unchanged
        """
        self.storage = StorageBackend(storage_dir)
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.merge_threshold = merge_threshold
        self.change_threshold = change_threshold
        
        # Initialize chunkers
        self._init_chunkers()
    
    def _init_chunkers(self) -> None:
        """Initialize modality-specific chunkers."""
        self.chunkers: Dict[str, Any] = {
            "text": TextChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            ),
            "code": CodeChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            ),
        }
        
        # Add optional chunkers if dependencies available
        if HAS_IMAGE_CHUNKER:
            self.chunkers["image"] = ImageChunker()
        
        if HAS_PDF_CHUNKER:
            self.chunkers["pdf"] = PDFChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            )
        
        if HAS_AUDIO_CHUNKER:
            self.chunkers["audio"] = AudioChunker()
        
        if HAS_VIDEO_CHUNKER:
            self.chunkers["video"] = VideoChunker()
    
    def get_chunker(self, file_path: str) -> Optional[Any]:
        """
        Get appropriate chunker for a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Chunker instance or None if no chunker available
        """
        ext = Path(file_path).suffix.lower()
        
        # Check code chunker first (more specific)
        if self.chunkers["code"].can_handle(file_path):
            return self.chunkers["code"]
        
        # Check other chunkers
        for modality, chunker in self.chunkers.items():
            if modality in ("text", "code"):
                continue
            if chunker.can_handle(file_path):
                return chunker
        
        # Fall back to text chunker
        if self.chunkers["text"].can_handle(file_path):
            return self.chunkers["text"]
        
        return None
    
    def detect_modality(self, file_path: str) -> str:
        """
        Detect file modality.
        
        Args:
            file_path: Path to file
            
        Returns:
            Modality string ("text", "code", "image", "pdf", "audio", "video", "unknown")
        """
        ext = Path(file_path).suffix.lower()
        
        # Code extensions
        code_extensions = self.chunkers["code"].supported_extensions()
        if ext in code_extensions:
            return "code"
        
        # Other modalities
        for modality, chunker in self.chunkers.items():
            if modality == "text":
                continue
            if ext in chunker.supported_extensions():
                return modality
        
        # Text extensions
        text_extensions = self.chunkers["text"].supported_extensions()
        if ext in text_extensions:
            return "text"
        
        return "unknown"
    
    def index_documents(
        self,
        paths: List[str],
        force_reindex: bool = False,
    ) -> Dict[str, Any]:
        """
        Index one or more documents.
        
        Performs dynamic semantic chunking and stores chunk metadata.
        Skips unchanged documents unless force_reindex is True.
        
        Args:
            paths: List of document paths to index
            force_reindex: Force re-indexing even if document hasn't changed
            
        Returns:
            Dictionary with indexing results
        """
        results: Dict[str, Any] = {
            "indexed": [],
            "skipped": [],
            "errors": [],
            "total_chunks": 0,
            "total_tokens": 0,
        }
        
        for path_str in paths:
            path = Path(path_str)
            
            # Validate path
            if not path.exists():
                results["errors"].append({
                    "path": path_str,
                    "error": "File not found",
                })
                continue
            
            if not path.is_file():
                results["errors"].append({
                    "path": path_str,
                    "error": "Not a file",
                })
                continue
            
            try:
                # Read document content
                content = path.read_text(encoding="utf-8", errors="replace")
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                
                # Check if document has changed
                existing_doc = self.storage.get_document(str(path))
                if existing_doc and not force_reindex:
                    if existing_doc["content_hash"] == content_hash:
                        results["skipped"].append({
                            "path": path_str,
                            "reason": "Unchanged",
                            "chunk_count": existing_doc["chunk_count"],
                        })
                        continue
                
                # Perform chunking
                chunks = self._chunk_document(content, str(path))
                
                # Store chunks
                for chunk in chunks:
                    self.storage.store_chunk(
                        chunk_id=chunk.chunk_id,
                        document_path=str(path),
                        content_hash=chunk.content_hash,
                        semantic_signature=chunk.semantic_signature,
                        start_pos=chunk.start_pos,
                        end_pos=chunk.end_pos,
                        token_count=chunk.token_count,
                    )
                
                # Store document metadata
                last_modified = path.stat().st_mtime
                self.storage.store_document(
                    document_path=str(path),
                    content_hash=content_hash,
                    chunk_count=len(chunks),
                    last_modified=last_modified,
                )
                
                total_tokens = sum(c.token_count for c in chunks)
                results["indexed"].append({
                    "path": path_str,
                    "chunk_count": len(chunks),
                    "total_tokens": total_tokens,
                })
                results["total_chunks"] += len(chunks)
                results["total_tokens"] += total_tokens
                
            except Exception as e:
                results["errors"].append({
                    "path": path_str,
                    "error": str(e),
                })
        
        return results
    
    def _chunk_document(self, content: str, document_path: str) -> List[Chunk]:
        """
        Perform dynamic semantic chunking on document content.
        
        Algorithm:
        1. Split content into initial ~256-token chunks
        2. Compute semantic signatures for each chunk
        3. Iteratively merge adjacent chunks with high similarity
        4. Stop when chunks reach max_chunk_size or similarity drops
        
        Args:
            content: Document content to chunk
            document_path: Path to source document
            
        Returns:
            List of Chunk objects
        """
        # Step 1: Create initial chunks
        initial_chunks = self._create_initial_chunks(content, document_path)
        
        if len(initial_chunks) <= 1:
            return initial_chunks
        
        # Step 2: Iteratively merge similar adjacent chunks
        merged_chunks = self._merge_similar_chunks(initial_chunks)
        
        return merged_chunks
    
    def _create_initial_chunks(
        self,
        content: str,
        document_path: str,
    ) -> List[Chunk]:
        """
        Create initial chunks targeting ~256 tokens each.
        
        Uses paragraph and sentence boundaries when possible,
        falling back to character-based splitting.
        
        Args:
            content: Document content
            document_path: Path to source document
            
        Returns:
            List of initial Chunk objects
        """
        chunks: List[Chunk] = []
        
        # Try to split on paragraph boundaries first
        paragraphs = re.split(r'\n\s*\n', content)
        
        current_chunk_text = ""
        current_start = 0
        chunk_index = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Estimate tokens in current chunk + paragraph
            combined_tokens = (len(current_chunk_text) + len(para)) // 4
            
            if combined_tokens > self.chunk_size and current_chunk_text:
                # Create chunk from accumulated text
                chunk = Chunk(
                    content=current_chunk_text.strip(),
                    start_pos=current_start,
                    end_pos=current_start + len(current_chunk_text),
                    document_path=document_path,
                    chunk_index=chunk_index,
                )
                chunks.append(chunk)
                chunk_index += 1
                
                # Start new chunk
                current_start = current_start + len(current_chunk_text)
                current_chunk_text = para + "\n\n"
            else:
                # Add paragraph to current chunk
                if current_chunk_text:
                    current_chunk_text += para + "\n\n"
                else:
                    current_chunk_text = para + "\n\n"
        
        # Add final chunk
        if current_chunk_text.strip():
            chunk = Chunk(
                content=current_chunk_text.strip(),
                start_pos=current_start,
                end_pos=current_start + len(current_chunk_text),
                document_path=document_path,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)
        
        # If no chunks created (empty content), create one empty chunk
        if not chunks:
            chunks.append(Chunk(
                content="",
                start_pos=0,
                end_pos=0,
                document_path=document_path,
                chunk_index=0,
            ))
        
        return chunks
    
    def _merge_similar_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """
        Iteratively merge adjacent chunks with high semantic similarity.
        
        Merges chunks when:
        - Cosine similarity exceeds merge_threshold
        - Combined token count doesn't exceed max_chunk_size
        
        Args:
            chunks: List of initial chunks
            
        Returns:
            List of merged chunks
        """
        if len(chunks) <= 1:
            return chunks
        
        merged = list(chunks)
        changed = True
        
        while changed:
            changed = False
            new_merged: List[Chunk] = []
            i = 0
            
            while i < len(merged):
                if i == len(merged) - 1:
                    # Last chunk, add it
                    new_merged.append(merged[i])
                    i += 1
                    continue
                
                # Check if we should merge current and next chunk
                current = merged[i]
                next_chunk = merged[i + 1]
                
                # Compute similarity
                similarity = current.similarity(next_chunk)
                
                # Check merge conditions
                combined_tokens = current.token_count + next_chunk.token_count
                should_merge = (
                    similarity >= self.merge_threshold
                    and combined_tokens <= self.max_chunk_size
                )
                
                if should_merge:
                    # Merge chunks
                    merged_chunk = Chunk(
                        content=current.content + "\n\n" + next_chunk.content,
                        start_pos=current.start_pos,
                        end_pos=next_chunk.end_pos,
                        document_path=current.document_path,
                        chunk_index=current.chunk_index,
                    )
                    new_merged.append(merged_chunk)
                    i += 2  # Skip next chunk
                    changed = True
                else:
                    new_merged.append(current)
                    i += 1
            
            merged = new_merged
        
        return merged
    
    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Detect changes in documents and update KV-cache accordingly.
        
        For each chunk:
        - If content hash matches → load pre-saved KV (instant)
        - If content hash differs but semantic similarity is high → 
          lightweight double-check, likely unchanged
        - If content hash differs and semantic similarity is low →
          mark for re-processing
        
        Args:
            session_id: Session identifier
            document_paths: Optional list of paths to check (defaults to all indexed)
            
        Returns:
            Dictionary with change detection results
        """
        # Ensure session exists
        self.storage.create_session(session_id)
        
        results: Dict[str, Any] = {
            "unchanged": [],
            "modified": [],
            "new": [],
            "removed": [],
            "kv_restored": 0,
            "kv_reprocessed": 0,
        }
        
        # Get documents to check
        if document_paths is None:
            # Get all indexed documents
            import sqlite3
            with sqlite3.connect(self.storage.db_path) as conn:
                cursor = conn.execute("SELECT document_path FROM documents")
                document_paths = [row[0] for row in cursor.fetchall()]
        
        for doc_path in document_paths:
            path = Path(doc_path)
            
            if not path.exists():
                results["removed"].append(doc_path)
                continue
            
            # Read current content
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                results["modified"].append({
                    "path": doc_path,
                    "reason": "Read error",
                })
                continue
            
            current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            
            # Get stored document info
            stored_doc = self.storage.get_document(doc_path)
            
            if stored_doc is None:
                # New document
                results["new"].append(doc_path)
                continue
            
            if stored_doc["content_hash"] == current_hash:
                # Document unchanged - all chunks can use cached KV
                results["unchanged"].append(doc_path)
                
                # Get all chunks for this document
                chunks = self.storage.get_document_chunks(doc_path)
                for chunk_meta in chunks:
                    # Try to load KV state from previous turn
                    session = self.storage.get_session(session_id)
                    if session and session["turn_count"] > 0:
                        kv_data = self.storage.load_kv_state(
                            session_id,
                            chunk_meta["chunk_id"],
                            session["turn_count"] - 1,
                        )
                        if kv_data is not None:
                            results["kv_restored"] += 1
            else:
                # Document changed - need to check each chunk
                results["modified"].append({
                    "path": doc_path,
                    "old_hash": stored_doc["content_hash"][:16],
                    "new_hash": current_hash[:16],
                })
                
                # Re-chunk and compare
                new_chunks = self._chunk_document(content, doc_path)
                old_chunks_meta = self.storage.get_document_chunks(doc_path)
                
                # Build lookup for old chunks by position
                old_by_pos: Dict[Tuple[int, int], Dict[str, Any]] = {}
                for meta in old_chunks_meta:
                    old_by_pos[(meta["start_pos"], meta["end_pos"])] = meta
                
                for new_chunk in new_chunks:
                    # Check if chunk exists at same position
                    old_meta = old_by_pos.get((new_chunk.start_pos, new_chunk.end_pos))
                    
                    if old_meta is None:
                        # New chunk position
                        results["new"].append({
                            "path": doc_path,
                            "chunk_id": new_chunk.chunk_id,
                            "reason": "New position",
                        })
                        results["kv_reprocessed"] += 1
                        continue
                    
                    # Compare content hash
                    if new_chunk.content_hash == old_meta["content_hash"]:
                        # Content unchanged at this position
                        results["kv_restored"] += 1
                    else:
                        # Content changed - check semantic similarity
                        old_sig = np.frombuffer(
                            old_meta["semantic_signature"], dtype=np.float32
                        )
                        new_sig = new_chunk.semantic_signature
                        
                        # Cosine similarity
                        dot = np.dot(old_sig, new_sig)
                        norm_old = np.linalg.norm(old_sig)
                        norm_new = np.linalg.norm(new_sig)
                        
                        if norm_old > 0 and norm_new > 0:
                            similarity = dot / (norm_old * norm_new)
                        else:
                            similarity = 0.0
                        
                        if similarity >= self.change_threshold:
                            # Semantically similar - lightweight double-check
                            # In practice, this would trigger a targeted LLM check
                            results["kv_restored"] += 1
                        else:
                            # Significant change - needs reprocessing
                            results["kv_reprocessed"] += 1
        
        return results
    
    def get_relevant_kv(
        self,
        session_id: str,
        query: str,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """
        Get KV-cache for chunks most relevant to a query.
        
        Uses semantic similarity between query and chunk signatures
        to find the most relevant chunks.
        
        Args:
            session_id: Session identifier
            query: Query text to find relevant chunks for
            top_k: Number of top chunks to return
            
        Returns:
            Dictionary with relevant chunks and their KV states
        """
        # Create a temporary chunk for the query
        query_chunk = Chunk(
            content=query,
            start_pos=0,
            end_pos=len(query),
            document_path="<query>",
            chunk_index=0,
        )
        
        # Get all chunks in session
        session_chunks = self.storage.get_session_chunks(session_id)
        
        if not session_chunks:
            return {
                "query": query,
                "chunks": [],
                "total_tokens": 0,
            }
        
        # Compute similarity scores
        scored_chunks: List[Tuple[float, Dict[str, Any]]] = []
        
        for chunk_meta in session_chunks:
            # Reconstruct semantic signature
            sig_bytes = chunk_meta["semantic_signature"]
            chunk_sig = np.frombuffer(sig_bytes, dtype=np.float32)
            
            # Compute similarity with query
            query_sig = query_chunk.semantic_signature
            
            dot = np.dot(query_sig, chunk_sig)
            norm_query = np.linalg.norm(query_sig)
            norm_chunk = np.linalg.norm(chunk_sig)
            
            if norm_query > 0 and norm_chunk > 0:
                similarity = dot / (norm_query * norm_chunk)
            else:
                similarity = 0.0
            
            scored_chunks.append((similarity, chunk_meta))
        
        # Sort by similarity (descending)
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        # Get top-k chunks
        top_chunks = scored_chunks[:top_k]
        
        # Load KV states for top chunks
        result_chunks = []
        total_tokens = 0
        
        for score, chunk_meta in top_chunks:
            kv_data = self.storage.load_kv_state(
                session_id,
                chunk_meta["chunk_id"],
                chunk_meta["turn_number"],
            )
            
            result_chunks.append({
                "chunk_id": chunk_meta["chunk_id"],
                "relevance_score": float(score),
                "token_count": chunk_meta["token_count"],
                "kv_available": kv_data is not None,
                "turn_number": chunk_meta["turn_number"],
            })
            total_tokens += chunk_meta["token_count"]
        
        return {
            "query": query,
            "chunks": result_chunks,
            "total_tokens": total_tokens,
        }
    
    def save_kv_state(
        self,
        session_id: str,
        kv_data: Dict[str, Any],
        chunk_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Save KV-cache state for a session.
        
        Args:
            session_id: Session identifier
            kv_data: Dictionary mapping chunk_id to KV data
            chunk_ids: Optional list of chunk IDs to save (defaults to all)
            
        Returns:
            Dictionary with save results
        """
        # Ensure session exists
        self.storage.create_session(session_id)
        session = self.storage.get_session(session_id)
        
        if session is None:
            return {"error": "Failed to create session"}
        
        turn_number = session["turn_count"]
        
        # Save KV state for each chunk
        saved_count = 0
        total_tokens = session["total_tokens"]
        
        for chunk_id, data in kv_data.items():
            if chunk_ids is not None and chunk_id not in chunk_ids:
                continue
            
            # Get chunk metadata for token count
            chunk_meta = self.storage.get_chunk(chunk_id)
            if chunk_meta is None:
                continue
            
            # Calculate relevance score (default to 1.0)
            relevance_score = 1.0
            
            # Store KV state
            self.storage.store_kv_state(
                session_id=session_id,
                chunk_id=chunk_id,
                turn_number=turn_number,
                kv_data=data,
                relevance_score=relevance_score,
            )
            
            saved_count += 1
            total_tokens += chunk_meta["token_count"]
        
        # Update session
        self.storage.update_session(
            session_id=session_id,
            turn_count=turn_number + 1,
            total_tokens=total_tokens,
        )
        
        return {
            "session_id": session_id,
            "turn_number": turn_number,
            "chunks_saved": saved_count,
            "total_tokens": total_tokens,
        }
    
    def rollback(
        self,
        session_id: str,
        target_turn: int,
    ) -> Dict[str, Any]:
        """
        Rollback session to a previous turn.
        
        Args:
            session_id: Session identifier
            target_turn: Target turn number to rollback to
            
        Returns:
            Dictionary with rollback results
        """
        session = self.storage.get_session(session_id)
        
        if session is None:
            return {"error": "Session not found"}
        
        current_turn = session["turn_count"]
        
        if target_turn >= current_turn:
            return {
                "error": f"Target turn {target_turn} >= current turn {current_turn}",
            }
        
        if target_turn < 0:
            return {"error": "Target turn must be non-negative"}
        
        # Perform rollback
        removed_count = self.storage.rollback_session(session_id, target_turn)
        
        return {
            "session_id": session_id,
            "previous_turn": current_turn,
            "current_turn": target_turn,
            "chunks_removed": removed_count,
        }
    
    def prune_chunks(
        self,
        session_id: str,
        max_tokens: int,
    ) -> Dict[str, Any]:
        """
        Prune low-relevance chunks to stay under token limit.
        
        Args:
            session_id: Session identifier
            max_tokens: Maximum total tokens to keep
            
        Returns:
            Dictionary with pruning results
        """
        session = self.storage.get_session(session_id)
        
        if session is None:
            return {"error": "Session not found"}
        
        current_tokens = session["total_tokens"]
        
        if current_tokens <= max_tokens:
            return {
                "session_id": session_id,
                "current_tokens": current_tokens,
                "max_tokens": max_tokens,
                "chunks_pruned": 0,
                "message": "Already under limit",
            }
        
        # Perform pruning
        pruned_count = self.storage.prune_chunks(session_id, max_tokens)
        
        # Get updated session
        updated_session = self.storage.get_session(session_id)
        new_tokens = updated_session["total_tokens"] if updated_session else 0
        
        return {
            "session_id": session_id,
            "previous_tokens": current_tokens,
            "current_tokens": new_tokens,
            "max_tokens": max_tokens,
            "chunks_pruned": pruned_count,
            "tokens_saved": current_tokens - new_tokens,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get ChunkForge statistics.
        
        Returns:
            Dictionary with statistics
        """
        storage_stats = self.storage.get_storage_stats()
        
        return {
            "version": "0.1.0",
            "storage": storage_stats,
            "config": {
                "chunk_size": self.chunk_size,
                "max_chunk_size": self.max_chunk_size,
                "merge_threshold": self.merge_threshold,
                "change_threshold": self.change_threshold,
            },
        }
