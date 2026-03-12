"""
PDF chunker for ChunkForge.

Splits PDF files into chunks by page or section.
Requires pymupdf for PDF parsing.

Install: pip install chunkforge[pdf]
"""

from typing import Any, Dict, List, Optional

from chunkforge.chunkers.base import BaseChunker, Chunk

# Check for pymupdf
try:
    import fitz  # pymupdf
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    fitz = None  # type: ignore


class PDFChunker(BaseChunker):
    """
    Chunker for PDF files.
    
    Supports:
    - Page-based chunking
    - Text extraction
    - Metadata extraction (title, author, etc.)
    
    Requires: pymupdf (pip install chunkforge[pdf])
    """
    
    def __init__(
        self,
        chunk_size: int = 256,
        max_chunk_size: int = 4096,
        pages_per_chunk: int = 1,
    ):
        """
        Initialize PDF chunker.
        
        Args:
            chunk_size: Target tokens per chunk
            max_chunk_size: Maximum tokens per chunk
            pages_per_chunk: Number of pages per chunk
        """
        if not HAS_PYMUPDF:
            raise ImportError(
                "pymupdf is required for PDF support. "
                "Install with: pip install chunkforge[pdf]"
            )
        
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.pages_per_chunk = pages_per_chunk
    
    def supported_extensions(self) -> List[str]:
        """Return supported PDF file extensions."""
        return [".pdf"]
    
    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """
        Split PDF into chunks.
        
        Args:
            content: PDF content (bytes or file path)
            document_path: Path to source document
            **kwargs: Additional options
            
        Returns:
            List of Chunk objects
        """
        # Open PDF
        if isinstance(content, bytes):
            doc = fitz.open(stream=content, filetype="pdf")
        elif isinstance(content, str):
            doc = fitz.open(content)
        else:
            raise ValueError(f"Unsupported content type: {type(content)}")
        
        try:
            # Extract metadata
            metadata = self._extract_metadata(doc)
            
            # Chunk by pages
            chunks = self._chunk_by_pages(doc, document_path, metadata)
            
            return chunks
        finally:
            doc.close()
    
    def _extract_metadata(self, doc: Any) -> Dict[str, Any]:
        """Extract PDF metadata."""
        meta = doc.metadata or {}
        
        return {
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "subject": meta.get("subject", ""),
            "keywords": meta.get("keywords", ""),
            "page_count": doc.page_count,
        }
    
    def _chunk_by_pages(
        self,
        doc: Any,
        document_path: str,
        metadata: Dict[str, Any],
    ) -> List[Chunk]:
        """Create chunks from PDF pages."""
        chunks: List[Chunk] = []
        chunk_index = 0
        
        for page_start in range(0, doc.page_count, self.pages_per_chunk):
            page_end = min(page_start + self.pages_per_chunk, doc.page_count)
            
            # Extract text from pages
            text_parts = []
            for page_num in range(page_start, page_end):
                page = doc[page_num]
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"[Page {page_num + 1}]\n{text}")
            
            if not text_parts:
                continue
            
            chunk_text = "\n\n".join(text_parts)
            
            # Compute position (approximate)
            start_pos = page_start * 1000  # Approximate
            end_pos = page_end * 1000
            
            chunk = Chunk(
                content=chunk_text,
                modality="pdf",
                start_pos=start_pos,
                end_pos=end_pos,
                document_path=document_path,
                chunk_index=chunk_index,
                metadata={
                    "page_start": page_start,
                    "page_end": page_end - 1,
                    "page_count": page_end - page_start,
                    "pdf_metadata": metadata,
                },
            )
            chunks.append(chunk)
            chunk_index += 1
        
        # Handle empty PDF
        if not chunks:
            chunks.append(Chunk(
                content="",
                modality="pdf",
                start_pos=0,
                end_pos=0,
                document_path=document_path,
                chunk_index=0,
                metadata={"pdf_metadata": metadata},
            ))
        
        return chunks


class PDFChunk(Chunk):
    """PDF-specific chunk with enhanced features."""
    
    def _compute_semantic_signature(self, signature_dim: int = 128) -> List[float]:
        """
        Compute semantic signature for PDF.
        
        Uses text-based features (same as text chunker).
        """
        signature = [0.0] * signature_dim
        
        if not isinstance(self.content, str):
            return signature
        
        # Use same approach as text chunker
        import re
        from collections import Counter
        
        text = self.content.lower()
        
        # Character trigrams (first 64 dimensions)
        trigrams: Counter = Counter()
        for i in range(len(text) - 2):
            trigrams[text[i:i+3]] += 1
        
        for i, (trigram, count) in enumerate(trigrams.most_common(64)):
            if i >= 64:
                break
            signature[i] = count / max(len(text), 1)
        
        # Word frequencies (next 32 dimensions)
        words: Counter = Counter()
        word_list = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text)
        for word in word_list:
            if len(word) > 2:
                words[word] += 1
        
        for i, (word, count) in enumerate(words.most_common(32)):
            if i >= 32:
                break
            signature[64 + i] = count / max(len(words), 1)
        
        # Structural features (next 32 dimensions)
        lines = self.content.split("\n")
        signature[96] = len(lines) / 100.0
        signature[97] = sum(len(line) for line in lines) / max(len(self.content), 1)
        
        # Page info from metadata
        page_count = self.metadata.get("page_count", 1)
        signature[98] = page_count / 100.0
        
        # Normalize
        norm = sum(x * x for x in signature) ** 0.5
        if norm > 0:
            signature = [x / norm for x in signature]
        
        return signature
    
    def _estimate_token_count(self) -> int:
        """Estimate token count for PDF text."""
        if isinstance(self.content, str):
            return max(1, len(self.content) // 4)
        return 1
