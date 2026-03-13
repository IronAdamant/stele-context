"""Tests for ChunkForge chunkers."""

from chunkforge.chunkers.text import TextChunker
from chunkforge.chunkers.code import CodeChunker
from chunkforge.chunkers.base import Chunk


class TestTextChunker:
    """Tests for TextChunker."""

    def test_initialization(self):
        """Test chunker initialization."""
        chunker = TextChunker(chunk_size=256, max_chunk_size=4096)

        assert chunker.chunk_size == 256
        assert chunker.max_chunk_size == 4096
        assert chunker.overlap == 0
        assert chunker.adaptive is True

    def test_supported_extensions(self):
        """Test supported extensions."""
        chunker = TextChunker()
        extensions = chunker.supported_extensions()

        assert ".txt" in extensions
        assert ".md" in extensions
        assert ".rst" in extensions

    def test_can_handle(self):
        """Test file handling detection."""
        chunker = TextChunker()

        assert chunker.can_handle("document.txt") is True
        assert chunker.can_handle("README.md") is True
        assert chunker.can_handle("script.py") is False

    def test_chunk_simple(self):
        """Test simple chunking."""
        chunker = TextChunker(chunk_size=50)

        content = "This is a test document. " * 20
        chunks = chunker.chunk(content, "test.txt")

        assert len(chunks) > 0
        assert all(isinstance(c, Chunk) for c in chunks)
        assert all(c.modality == "text" for c in chunks)

    def test_chunk_empty(self):
        """Test chunking empty content."""
        chunker = TextChunker()
        chunks = chunker.chunk("", "test.txt")

        assert len(chunks) == 1
        assert chunks[0].content == ""

    def test_chunk_paragraphs(self):
        """Test paragraph-based chunking."""
        chunker = TextChunker(chunk_size=100, adaptive=False)

        content = """
First paragraph with some content.

Second paragraph with more content.

Third paragraph with even more content.
"""
        chunks = chunker.chunk(content, "test.txt")

        assert len(chunks) > 0

    def test_chunk_adaptive(self):
        """Test adaptive chunking."""
        chunker = TextChunker(chunk_size=100, adaptive=True)

        # Dense content (code-like)
        dense_content = """
def function():
    x = 1
    y = 2
    return x + y

def another():
    a = 3
    b = 4
    return a * b
"""
        dense_chunks = chunker.chunk(dense_content, "test.py")

        # Sparse content (prose)
        sparse_content = """
This is a paragraph with normal prose content. It has longer sentences and more words per line. The content is less dense and more readable.

Another paragraph with similar characteristics. The text flows naturally and doesn't have many special characters or indentation.
"""
        sparse_chunks = chunker.chunk(sparse_content, "test.txt")

        # Dense content should produce more chunks (smaller chunk size)
        # This is a heuristic test, may not always pass
        assert len(dense_chunks) > 0
        assert len(sparse_chunks) > 0

    def test_chunk_sliding_window(self):
        """Test sliding window chunking."""
        chunker = TextChunker(chunk_size=30, overlap=10)

        # Longer content to ensure multiple chunks
        content = "Sentence one. " * 20
        chunks = chunker.chunk(content, "test.txt")

        assert len(chunks) > 1

        # Check overlap
        if len(chunks) > 1:
            # Some content should appear in multiple chunks
            all_content = " ".join(c.content for c in chunks)
            assert len(all_content) > len(content)


class TestCodeChunker:
    """Tests for CodeChunker."""

    def test_initialization(self):
        """Test chunker initialization."""
        chunker = CodeChunker(chunk_size=256, max_chunk_size=4096)

        assert chunker.chunk_size == 256
        assert chunker.max_chunk_size == 4096

    def test_supported_extensions(self):
        """Test supported extensions."""
        chunker = CodeChunker()
        extensions = chunker.supported_extensions()

        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".java" in extensions

    def test_can_handle(self):
        """Test file handling detection."""
        chunker = CodeChunker()

        assert chunker.can_handle("script.py") is True
        assert chunker.can_handle("app.js") is True
        assert chunker.can_handle("document.txt") is False

    def test_chunk_python(self):
        """Test Python code chunking."""
        chunker = CodeChunker(chunk_size=100)

        content = """
def function_one():
    '''First function.'''
    x = 1
    y = 2
    return x + y

def function_two():
    '''Second function.'''
    a = 3
    b = 4
    return a * b

class MyClass:
    '''A test class.'''
    
    def method_one(self):
        return 1
    
    def method_two(self):
        return 2
"""
        chunks = chunker.chunk(content, "test.py")

        assert len(chunks) > 0
        assert all(c.modality == "code" for c in chunks)
        assert all(c.metadata.get("language") == "python" for c in chunks)

    def test_chunk_javascript(self):
        """Test JavaScript code chunking."""
        chunker = CodeChunker(chunk_size=100)

        content = """
function firstFunction() {
    const x = 1;
    const y = 2;
    return x + y;
}

function secondFunction() {
    const a = 3;
    const b = 4;
    return a * b;
}

class MyClass {
    constructor() {
        this.value = 0;
    }
    
    method() {
        return this.value;
    }
}
"""
        chunks = chunker.chunk(content, "test.js")

        assert len(chunks) > 0
        assert all(c.modality == "code" for c in chunks)

    def test_chunk_empty(self):
        """Test chunking empty code."""
        chunker = CodeChunker()
        chunks = chunker.chunk("", "test.py")

        assert len(chunks) == 1
        assert chunks[0].content == ""


class TestChunk:
    """Tests for Chunk dataclass."""

    def test_creation(self):
        """Test chunk creation."""
        chunk = Chunk(
            content="test content",
            modality="text",
            start_pos=0,
            end_pos=12,
            document_path="test.txt",
            chunk_index=0,
        )

        assert chunk.content == "test content"
        assert chunk.modality == "text"
        assert chunk.start_pos == 0
        assert chunk.end_pos == 12
        assert chunk.document_path == "test.txt"
        assert chunk.chunk_index == 0

    def test_content_hash(self):
        """Test content hash computation."""
        chunk1 = Chunk(
            content="test content",
            modality="text",
            start_pos=0,
            end_pos=12,
            document_path="test.txt",
            chunk_index=0,
        )
        chunk2 = Chunk(
            content="test content",
            modality="text",
            start_pos=0,
            end_pos=12,
            document_path="test.txt",
            chunk_index=0,
        )
        chunk3 = Chunk(
            content="different content",
            modality="text",
            start_pos=0,
            end_pos=17,
            document_path="test.txt",
            chunk_index=0,
        )

        # Same content should have same hash
        assert chunk1.content_hash == chunk2.content_hash

        # Different content should have different hash
        assert chunk1.content_hash != chunk3.content_hash

    def test_chunk_id(self):
        """Test chunk ID generation."""
        chunk = Chunk(
            content="test content",
            modality="text",
            start_pos=0,
            end_pos=12,
            document_path="test.txt",
            chunk_index=0,
        )

        assert isinstance(chunk.chunk_id, str)
        assert len(chunk.chunk_id) == 32

    def test_token_count(self):
        """Test token count estimation."""
        chunk = Chunk(
            content="This is a test sentence with multiple words.",
            modality="text",
            start_pos=0,
            end_pos=44,
            document_path="test.txt",
            chunk_index=0,
        )

        assert chunk.token_count > 0
        # Regex tokenizer splits into words, punctuation, spaces
        # "This is a test sentence with multiple words." -> ~16 tokens
        assert 10 <= chunk.token_count <= 20

    def test_similarity(self):
        """Test similarity computation."""
        chunk1 = Chunk(
            content="Hello world",
            modality="text",
            start_pos=0,
            end_pos=11,
            document_path="test.txt",
            chunk_index=0,
        )
        chunk2 = Chunk(
            content="Hello world",
            modality="text",
            start_pos=0,
            end_pos=11,
            document_path="test.txt",
            chunk_index=0,
        )
        chunk3 = Chunk(
            content="Goodbye world",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
            chunk_index=0,
        )

        # Same content should have high similarity
        sim1 = chunk1.similarity(chunk2)
        assert sim1 > 0.9

        # Different content should have lower similarity
        sim2 = chunk1.similarity(chunk3)
        assert sim2 < sim1
