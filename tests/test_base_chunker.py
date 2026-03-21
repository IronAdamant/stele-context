"""Tests for stele.chunkers.base module.

Covers:
- Chunk dataclass creation, field defaults, and lazy properties
- estimate_tokens() function accuracy across varied inputs
- BaseChunker ABC enforcement and can_handle() method
- Semantic signature computation and similarity
"""

import hashlib

import pytest

from stele.chunkers.base import BaseChunker, Chunk, _TOKEN_RE, estimate_tokens


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def text_chunk():
    """A simple text chunk for reuse across tests."""
    return Chunk(
        content="Hello, world! This is a test.",
        modality="text",
        start_pos=0,
        end_pos=29,
        document_path="test.txt",
        chunk_index=0,
    )


@pytest.fixture
def code_chunk():
    """A chunk containing Python code."""
    code = 'def greet(name):\n    """Say hello."""\n    return f\'Hello, {name}!\'\n'
    return Chunk(
        content=code,
        modality="code",
        start_pos=0,
        end_pos=len(code),
        document_path="example.py",
        chunk_index=0,
        metadata={"language": "python"},
    )


@pytest.fixture
def binary_chunk():
    """A chunk with binary (bytes) content."""
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    return Chunk(
        content=data,
        modality="image",
        start_pos=0,
        end_pos=len(data),
        document_path="icon.png",
    )


class ConcreteChunker(BaseChunker):
    """Minimal concrete subclass for testing BaseChunker."""

    def chunk(self, content, document_path, **kwargs):
        return [Chunk(content=content, modality="text")]

    def supported_extensions(self):
        return [".txt", ".md"]


# ---------------------------------------------------------------------------
# Chunk dataclass: creation and defaults
# ---------------------------------------------------------------------------


class TestChunkCreation:
    """Tests for Chunk dataclass construction and default field values."""

    def test_required_fields(self):
        """Content and modality are the only required positional fields."""
        chunk = Chunk(content="abc", modality="text")
        assert chunk.content == "abc"
        assert chunk.modality == "text"

    def test_default_positions(self):
        """start_pos and end_pos default to 0."""
        chunk = Chunk(content="x", modality="text")
        assert chunk.start_pos == 0
        assert chunk.end_pos == 0

    def test_default_document_path(self):
        """document_path defaults to empty string."""
        chunk = Chunk(content="x", modality="text")
        assert chunk.document_path == ""

    def test_default_chunk_index(self):
        """chunk_index defaults to 0."""
        chunk = Chunk(content="x", modality="text")
        assert chunk.chunk_index == 0

    def test_default_metadata(self):
        """metadata defaults to an empty dict."""
        chunk = Chunk(content="x", modality="text")
        assert chunk.metadata == {}

    def test_metadata_isolation(self):
        """Each chunk gets its own metadata dict (no shared default)."""
        a = Chunk(content="a", modality="text")
        b = Chunk(content="b", modality="text")
        a.metadata["key"] = "val"
        assert "key" not in b.metadata

    def test_private_fields_default_none(self):
        """Lazy-computed private fields start as None."""
        chunk = Chunk(content="x", modality="text")
        assert chunk._content_hash is None
        assert chunk._semantic_signature is None
        assert chunk._token_count is None
        assert chunk._chunk_id is None

    def test_custom_metadata(self):
        """Custom metadata dict is preserved."""
        meta = {"language": "python", "version": 3}
        chunk = Chunk(content="x", modality="code", metadata=meta)
        assert chunk.metadata == meta
        assert chunk.metadata is meta


# ---------------------------------------------------------------------------
# Chunk lazy properties
# ---------------------------------------------------------------------------


class TestChunkContentHash:
    """Tests for the content_hash property."""

    def test_hash_is_sha256_hex(self, text_chunk):
        """content_hash is a 64-char hex SHA-256 digest."""
        h = text_chunk.content_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_deterministic(self, text_chunk):
        """Same content produces same hash on repeated access."""
        assert text_chunk.content_hash == text_chunk.content_hash

    def test_hash_matches_manual_sha256(self):
        """Hash matches manual SHA-256 of UTF-8 encoded content."""
        content = "deterministic check"
        chunk = Chunk(content=content, modality="text")
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert chunk.content_hash == expected

    def test_hash_differs_for_different_content(self):
        """Different content produces different hashes."""
        a = Chunk(content="alpha", modality="text")
        b = Chunk(content="bravo", modality="text")
        assert a.content_hash != b.content_hash

    def test_hash_same_content_same_hash(self):
        """Identical content in separate Chunk objects yields identical hash."""
        a = Chunk(content="same", modality="text")
        b = Chunk(content="same", modality="text")
        assert a.content_hash == b.content_hash

    def test_hash_bytes_content(self, binary_chunk):
        """Bytes content is hashed directly."""
        expected = hashlib.sha256(binary_chunk.content).hexdigest()
        assert binary_chunk.content_hash == expected

    def test_hash_non_str_non_bytes_content(self):
        """Non-str/non-bytes content is str()-converted before hashing."""
        chunk = Chunk(content=12345, modality="text")
        expected = hashlib.sha256(b"12345").hexdigest()
        assert chunk.content_hash == expected

    def test_hash_cached(self, text_chunk):
        """Hash is computed once then cached in _content_hash."""
        _ = text_chunk.content_hash
        assert text_chunk._content_hash is not None


class TestChunkId:
    """Tests for the chunk_id property."""

    def test_id_is_32_hex(self, text_chunk):
        """chunk_id is a 32-character hex string."""
        cid = text_chunk.chunk_id
        assert len(cid) == 32
        assert all(c in "0123456789abcdef" for c in cid)

    def test_id_deterministic(self, text_chunk):
        """Same chunk produces same id."""
        assert text_chunk.chunk_id == text_chunk.chunk_id

    def test_id_same_for_identical_chunks(self):
        """Two chunks with identical fields get the same id."""
        kwargs = dict(
            content="x",
            modality="text",
            start_pos=0,
            end_pos=1,
            document_path="f.txt",
        )
        a = Chunk(**kwargs)
        b = Chunk(**kwargs)
        assert a.chunk_id == b.chunk_id

    def test_id_differs_for_different_path(self):
        """Changing document_path changes chunk_id."""
        a = Chunk(content="x", modality="text", document_path="a.txt")
        b = Chunk(content="x", modality="text", document_path="b.txt")
        assert a.chunk_id != b.chunk_id

    def test_id_differs_for_different_positions(self):
        """Changing start_pos/end_pos changes chunk_id."""
        a = Chunk(content="x", modality="text", start_pos=0, end_pos=1)
        b = Chunk(content="x", modality="text", start_pos=5, end_pos=6)
        assert a.chunk_id != b.chunk_id

    def test_id_cached(self, text_chunk):
        """chunk_id is cached after first access."""
        _ = text_chunk.chunk_id
        assert text_chunk._chunk_id is not None


class TestChunkTokenCount:
    """Tests for the token_count property."""

    def test_token_count_positive(self, text_chunk):
        """Token count is at least 1."""
        assert text_chunk.token_count >= 1

    def test_token_count_bytes(self, binary_chunk):
        """Bytes content uses len//4 heuristic."""
        expected = max(1, len(binary_chunk.content) // 4)
        assert binary_chunk.token_count == expected

    def test_token_count_non_str_non_bytes(self):
        """Non-str/non-bytes content returns 1."""
        chunk = Chunk(content=42, modality="text")
        assert chunk.token_count == 1

    def test_token_count_cached(self, text_chunk):
        """Token count is cached after first access."""
        _ = text_chunk.token_count
        assert text_chunk._token_count is not None


# ---------------------------------------------------------------------------
# Semantic signature and similarity
# ---------------------------------------------------------------------------


class TestSemanticSignature:
    """Tests for the semantic_signature property."""

    def test_signature_length(self, text_chunk):
        """Signature is a 128-element sequence."""
        sig = text_chunk.semantic_signature
        assert len(sig) == 128

    def test_signature_unit_vector(self, text_chunk):
        """Signature is normalized (approximately unit length)."""
        sig = text_chunk.semantic_signature
        norm = sum(x * x for x in sig) ** 0.5
        assert abs(norm - 1.0) < 1e-4

    def test_signature_cached(self, text_chunk):
        """Signature is cached after first access."""
        _ = text_chunk.semantic_signature
        assert text_chunk._semantic_signature is not None

    def test_signature_binary_content(self, binary_chunk):
        """Binary content produces a hash-based 128-dim signature."""
        sig = binary_chunk.semantic_signature
        assert len(sig) == 128

    def test_signature_code_detects_def(self, code_chunk):
        """Code starting with 'def' sets the positional feature at dim 104."""
        sig = code_chunk.semantic_signature
        # dim 104 corresponds to first-line def/function keyword detection.
        # Since signature is normalized, just confirm it is positive.
        assert sig[104] > 0.0

    def test_signature_empty_content(self):
        """Empty string still produces a 128-dim signature."""
        chunk = Chunk(content="", modality="text")
        sig = chunk.semantic_signature
        assert len(sig) == 128


class TestChunkSimilarity:
    """Tests for the similarity() method."""

    def test_self_similarity_is_one(self, text_chunk):
        """A chunk's similarity with itself should be ~1.0."""
        sim = text_chunk.similarity(text_chunk)
        assert abs(sim - 1.0) < 1e-4

    def test_similar_content_high_score(self):
        """Nearly identical content yields high similarity."""
        a = Chunk(
            content="the quick brown fox jumps over the lazy dog", modality="text"
        )
        b = Chunk(
            content="the quick brown fox leaps over the lazy dog", modality="text"
        )
        assert a.similarity(b) > 0.8

    def test_dissimilar_content_lower_score(self):
        """Unrelated content yields lower similarity than near-duplicates."""
        base = Chunk(content="def compute(x): return x * 2", modality="text")
        similar = Chunk(content="def compute(y): return y * 2", modality="text")
        different = Chunk(
            content="The weather in Paris is pleasant in spring.",
            modality="text",
        )
        assert base.similarity(similar) > base.similarity(different)

    def test_similarity_range(self, text_chunk, code_chunk):
        """Similarity is always in [0, 1]."""
        sim = text_chunk.similarity(code_chunk)
        assert 0.0 <= sim <= 1.0


# ---------------------------------------------------------------------------
# estimate_tokens() function
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    """Tests for the module-level estimate_tokens() function."""

    def test_empty_string(self):
        """Empty string returns 1 (minimum)."""
        assert estimate_tokens("") == 1

    def test_single_word(self):
        """Single word produces at least 1 token."""
        assert estimate_tokens("hello") >= 1

    def test_short_sentence(self):
        """Short sentence token count is within a reasonable range."""
        result = estimate_tokens("Hello, world!")
        # "Hello" "," " " "world" "!" -> ~5 raw, merges reduce
        assert 1 <= result <= 10

    def test_long_text(self):
        """Longer text produces proportionally more tokens."""
        short = estimate_tokens("word")
        long_ = estimate_tokens("word " * 100)
        assert long_ > short

    def test_code_snippet(self):
        """Code with punctuation and identifiers is tokenized."""
        code = "def foo(bar, baz=None): return bar + baz"
        result = estimate_tokens(code)
        assert result >= 5

    def test_punctuation_merges(self):
        """Adjacent punctuation pairs are merged (BPE correction)."""
        # ): and == should each merge into single tokens
        raw = len(_TOKEN_RE.findall("x): y == z"))
        merged = estimate_tokens("x): y == z")
        assert merged < raw

    def test_space_word_merges(self):
        """Leading space + word merges reduce count (BPE correction)."""
        raw = len(_TOKEN_RE.findall("a b c"))
        merged = estimate_tokens("a b c")
        assert merged < raw

    def test_minimum_is_one(self):
        """Return value is never less than 1."""
        assert estimate_tokens("") >= 1
        assert estimate_tokens(" ") >= 1
        assert estimate_tokens("x") >= 1

    def test_unicode_text(self):
        """Unicode characters do not cause errors."""
        result = estimate_tokens("cafe\u0301 nai\u0308ve re\u0301sume\u0301")
        assert result >= 1

    def test_cjk_characters(self):
        """CJK text returns a positive count."""
        assert estimate_tokens("\u4f60\u597d\u4e16\u754c") >= 1

    def test_mixed_case_identifiers(self):
        """camelCase and PascalCase are split into subwords."""
        result = estimate_tokens("camelCaseIdentifier")
        # Expect: "camel", "Case", "Identifier" -> 3 tokens
        assert result >= 3

    def test_snake_case(self):
        """snake_case produces tokens for each segment plus underscores."""
        result = estimate_tokens("my_variable_name")
        assert result >= 3

    def test_numbers(self):
        """Numeric sequences form their own tokens."""
        result = estimate_tokens("error 404 not found")
        assert result >= 3

    def test_whitespace_only(self):
        """Whitespace-only input returns at least 1."""
        assert estimate_tokens("   \t\n") >= 1


# ---------------------------------------------------------------------------
# _TOKEN_RE regex
# ---------------------------------------------------------------------------


class TestTokenRegex:
    """Tests for the _TOKEN_RE regex used by estimate_tokens."""

    def test_splits_camel_case(self):
        """camelCase is split at uppercase boundaries."""
        tokens = _TOKEN_RE.findall("camelCase")
        assert "camel" in tokens
        assert "Case" in tokens

    def test_splits_numbers(self):
        """Numbers are separate tokens."""
        tokens = _TOKEN_RE.findall("abc123def")
        assert "123" in tokens

    def test_splits_punctuation(self):
        """Punctuation characters are individual tokens."""
        tokens = _TOKEN_RE.findall("a+b")
        assert "+" in tokens

    def test_preserves_whitespace(self):
        """Whitespace becomes its own token group."""
        tokens = _TOKEN_RE.findall("a b")
        assert any(t.isspace() for t in tokens)


# ---------------------------------------------------------------------------
# BaseChunker ABC enforcement
# ---------------------------------------------------------------------------


class TestBaseChunkerABC:
    """Tests for BaseChunker abstract base class."""

    def test_cannot_instantiate_directly(self):
        """Instantiating BaseChunker raises TypeError."""
        with pytest.raises(TypeError):
            BaseChunker()

    def test_incomplete_subclass_raises(self):
        """Subclass missing abstract methods raises TypeError."""

        class Incomplete(BaseChunker):
            def chunk(self, content, document_path, **kwargs):
                return []

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_instantiates(self):
        """A fully implemented subclass can be instantiated."""
        chunker = ConcreteChunker()
        assert isinstance(chunker, BaseChunker)

    def test_chunk_returns_list(self):
        """Concrete chunk() returns a list of Chunk objects."""
        chunker = ConcreteChunker()
        result = chunker.chunk("some content", "file.txt")
        assert isinstance(result, list)
        assert all(isinstance(c, Chunk) for c in result)

    def test_supported_extensions_returns_list(self):
        """supported_extensions() returns a list of strings."""
        chunker = ConcreteChunker()
        exts = chunker.supported_extensions()
        assert isinstance(exts, list)
        assert all(isinstance(e, str) for e in exts)


# ---------------------------------------------------------------------------
# BaseChunker.can_handle()
# ---------------------------------------------------------------------------


class TestCanHandle:
    """Tests for BaseChunker.can_handle() method."""

    def test_matching_extension(self):
        """Returns True for supported extensions."""
        chunker = ConcreteChunker()
        assert chunker.can_handle("readme.txt") is True
        assert chunker.can_handle("notes.md") is True

    def test_non_matching_extension(self):
        """Returns False for unsupported extensions."""
        chunker = ConcreteChunker()
        assert chunker.can_handle("script.py") is False
        assert chunker.can_handle("image.png") is False

    def test_case_insensitive_extension(self):
        """Extension matching is case-insensitive."""
        chunker = ConcreteChunker()
        assert chunker.can_handle("README.TXT") is True
        assert chunker.can_handle("notes.MD") is True

    def test_path_with_directories(self):
        """Works with full paths, not just filenames."""
        chunker = ConcreteChunker()
        assert chunker.can_handle("/home/user/docs/file.txt") is True
        assert chunker.can_handle("src/module/lib.py") is False

    def test_no_extension(self):
        """Files without extensions are not handled."""
        chunker = ConcreteChunker()
        assert chunker.can_handle("Makefile") is False
