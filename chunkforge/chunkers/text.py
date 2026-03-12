"""
Text chunker for ChunkForge.

Splits plain text files into semantically coherent chunks using
paragraph boundaries and token-based splitting. Zero dependencies.
"""

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from chunkforge.chunkers.base import BaseChunker, Chunk


class TextChunker(BaseChunker):
    """
    Chunker for plain text files.
    
    Uses paragraph boundaries and token-based splitting to create
    semantically coherent chunks. Zero external dependencies.
    """
    
    def __init__(
        self,
        chunk_size: int = 256,
        max_chunk_size: int = 4096,
    ):
        """
        Initialize text chunker.
        
        Args:
            chunk_size: Target tokens per chunk
            max_chunk_size: Maximum tokens per chunk
        """
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
    
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
        
        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', content)
        
        chunks: List[Chunk] = []
        current_text = ""
        current_start = 0
        chunk_index = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # Estimate tokens
            combined_tokens = (len(current_text) + len(para)) // 4
            
            if combined_tokens > self.chunk_size and current_text:
                # Create chunk
                chunk = Chunk(
                    content=current_text.strip(),
                    modality="text",
                    start_pos=current_start,
                    end_pos=current_start + len(current_text),
                    document_path=document_path,
                    chunk_index=chunk_index,
                )
                chunks.append(chunk)
                chunk_index += 1
                
                # Start new chunk
                current_start = current_start + len(current_text)
                current_text = para + "\n\n"
            else:
                # Add to current chunk
                if current_text:
                    current_text += para + "\n\n"
                else:
                    current_text = para + "\n\n"
        
        # Add final chunk
        if current_text.strip():
            chunk = Chunk(
                content=current_text.strip(),
                modality="text",
                start_pos=current_start,
                end_pos=current_start + len(current_text),
                document_path=document_path,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)
        
        # Handle empty content
        if not chunks:
            chunks.append(Chunk(
                content="",
                modality="text",
                start_pos=0,
                end_pos=0,
                document_path=document_path,
                chunk_index=0,
            ))
        
        return chunks


class TextChunk(Chunk):
    """Text-specific chunk with enhanced semantic signature."""
    
    def _compute_semantic_signature(self, signature_dim: int = 128) -> List[float]:
        """
        Compute semantic signature for text.
        
        Uses character trigrams, word frequencies, and structural features.
        """
        signature = [0.0] * signature_dim
        
        if not isinstance(self.content, str):
            return signature
        
        text = self.content.lower()
        
        # Feature 1: Character trigram frequencies (first 64 dimensions)
        trigrams: Counter = Counter()
        for i in range(len(text) - 2):
            trigrams[text[i:i+3]] += 1
        
        for i, (trigram, count) in enumerate(trigrams.most_common(64)):
            if i >= 64:
                break
            signature[i] = count / max(len(text), 1)
        
        # Feature 2: Word frequency distribution (next 32 dimensions)
        words: Counter = Counter()
        word_list = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text)
        for word in word_list:
            if len(word) > 2:
                words[word] += 1
        
        for i, (word, count) in enumerate(words.most_common(32)):
            if i >= 32:
                break
            signature[64 + i] = count / max(len(words), 1)
        
        # Feature 3: Structural features (next 32 dimensions)
        lines = self.content.split("\n")
        signature[96] = len(lines) / 100.0
        signature[97] = sum(len(line) for line in lines) / max(len(self.content), 1)
        signature[98] = sum(1 for line in lines if line.strip().startswith("#")) / max(len(lines), 1)
        signature[99] = sum(1 for line in lines if line.strip().startswith("def ")) / max(len(lines), 1)
        signature[100] = sum(1 for line in lines if line.strip().startswith("class ")) / max(len(lines), 1)
        signature[101] = self.content.count("(") / max(len(self.content), 1)
        signature[102] = self.content.count("{") / max(len(self.content), 1)
        signature[103] = self.content.count("[") / max(len(self.content), 1)
        
        # Normalize to unit vector
        norm = sum(x * x for x in signature) ** 0.5
        if norm > 0:
            signature = [x / norm for x in signature]
        
        return signature
    
    def _estimate_token_count(self) -> int:
        """Estimate token count for text."""
        if isinstance(self.content, str):
            return max(1, len(self.content) // 4)
        return 1
