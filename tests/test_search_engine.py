"""Tests for hybrid search ranking logic in search_engine.py."""

from __future__ import annotations


from stele_context.search_engine import (
    compute_search_alpha,
    extract_query_identifiers,
    _text_signature,
    init_chunkers,
)


class TestComputeSearchAlpha:
    """Tests for alpha auto-tuning based on query characteristics."""

    def test_plain_english_keeps_base_alpha(self):
        """Prose queries (no code signals) keep base alpha so HNSW can complement BM25.

        The statistical HNSW signal does not understand semantics, but it can
        still surface structurally similar chunks.  For natural-language queries
        we keep base_alpha so both signals contribute.  Code-like queries are
        the ones that need alpha reduced to favor BM25 keyword matching.
        """
        base = 0.7
        result = compute_search_alpha("how do I handle authentication", base)
        assert result == base

    def test_underscore_lowers_alpha(self):
        """A query with an underscore is a code signal; alpha should drop."""
        base = 0.7
        result = compute_search_alpha("parse_config function", base)
        assert result < base
        assert result >= 0.4

    def test_brackets_lower_alpha(self):
        """A query containing brackets is a code signal; alpha should drop."""
        base = 0.7
        result = compute_search_alpha("if condition {} block", base)
        assert result < base

    def test_def_keyword_lowers_alpha(self):
        """'def' keyword in query is a code signal; alpha should drop."""
        base = 0.7
        result = compute_search_alpha("def my_function", base)
        assert result < base

    def test_class_keyword_lowers_alpha(self):
        """'class' keyword in query is a code signal; alpha should drop."""
        base = 0.7
        result = compute_search_alpha("class MyModel", base)
        assert result < base

    def test_camelcase_identifier_lowers_alpha(self):
        """CamelCase identifier is a code signal; alpha should drop."""
        base = 0.7
        result = compute_search_alpha("MyClass parseJson", base)
        assert result < base

    def test_three_or_more_signals_maximum_reduction(self):
        """Three code signals should trigger the maximum alpha reduction."""
        base = 0.7
        # underscore + brackets + def keyword = 3 signals
        result = compute_search_alpha("def parse_config(args) {}", base)
        assert result == max(0.3, base - 0.3)

    def test_maximum_reduction_floor_is_0_3(self):
        """Alpha should never go below 0.3 regardless of base value."""
        base = 0.4
        result = compute_search_alpha("def parse_config(args) {}", base)
        assert result >= 0.3

    def test_one_signal_floor_is_0_3(self):
        """With 1-2 signals alpha floor is 0.3 to allow meaningful reduction."""
        base = 0.45
        result = compute_search_alpha("parse_config", base)
        assert result >= 0.3

    def test_dot_access_lowers_alpha(self):
        """A dotted expression like 'obj.method' is a code signal."""
        base = 0.7
        result = compute_search_alpha("obj.method call", base)
        assert result < base

    def test_trailing_dot_is_plain_english(self):
        """A query ending with a period (sentence) has no code signals; keeps base alpha."""
        base = 0.7
        # "the end." — ends with dot so dot condition is False; no other signals → plain NL
        result = compute_search_alpha("the end.", base)
        assert result == base


class TestExtractQueryIdentifiers:
    """Tests for identifier extraction from queries."""

    def test_basic_identifier_extraction(self):
        """Should extract word-like identifiers from a simple query."""
        tokens = extract_query_identifiers("parse config file")
        assert "parse" in tokens
        assert "config" in tokens
        assert "file" in tokens

    def test_stop_words_filtered(self):
        """Common stop words should be excluded from results."""
        tokens = extract_query_identifiers("how are the functions defined")
        lowered = [t.lower() for t in tokens]
        for word in ("how", "are", "the"):
            assert word not in lowered

    def test_single_char_tokens_filtered(self):
        """Single-character tokens should be excluded by the regex patterns."""
        tokens = extract_query_identifiers("a b c parse")
        # 'a', 'b', 'c' are length-1 and not matched by any pattern
        for t in tokens:
            assert len(t) >= 2
        assert "parse" in tokens

    def test_camelcase_splitting(self):
        """CamelCase names should be split into sub-tokens."""
        tokens = extract_query_identifiers("MyClassName")
        lowered = [t.lower() for t in tokens]
        # At minimum the full identifier or its parts should appear
        assert any(t in lowered for t in ("my", "class", "classname", "myclassname"))

    def test_snake_case_splitting(self):
        """snake_case tokens should be included; full name also captured."""
        tokens = extract_query_identifiers("parse_config_file")
        lowered = [t.lower() for t in tokens]
        # The regex finds word-boundary parts; full snake token also matched
        assert "parse_config_file" in lowered or "parse" in lowered

    def test_returns_list(self):
        """Return type should be a list."""
        result = extract_query_identifiers("some query")
        assert isinstance(result, list)

    def test_empty_query(self):
        """Empty query should return an empty list."""
        result = extract_query_identifiers("")
        assert result == []

    def test_no_duplicates(self):
        """Result should contain unique tokens (set-based de-duplication)."""
        tokens = extract_query_identifiers("parse parse parse")
        assert len(tokens) == len(set(tokens))


class TestTextSignature:
    """Tests for the internal _text_signature helper."""

    def test_returns_list_of_128_floats(self):
        """Signature should be exactly 128 floats."""
        sig = _text_signature("hello world")
        assert isinstance(sig, list)
        assert len(sig) == 128
        assert all(isinstance(v, float) for v in sig)

    def test_deterministic(self):
        """Same input should always produce identical output."""
        text = "def compute_search_alpha(query, base_alpha):"
        sig1 = _text_signature(text)
        sig2 = _text_signature(text)
        assert sig1 == sig2

    def test_different_texts_produce_different_sigs(self):
        """Distinct texts should (almost certainly) produce distinct signatures."""
        sig_a = _text_signature("authentication login handler")
        sig_b = _text_signature("database connection pool manager")
        assert sig_a != sig_b

    def test_empty_string(self):
        """Empty string should still return a 128-element list."""
        sig = _text_signature("")
        assert len(sig) == 128


class TestInitChunkers:
    """Tests for chunker initialization."""

    def test_always_has_text_chunker(self):
        """TextChunker must always be present."""
        chunkers = init_chunkers(chunk_size=500, max_chunk_size=1000)
        assert "text" in chunkers

    def test_always_has_code_chunker(self):
        """CodeChunker must always be present."""
        chunkers = init_chunkers(chunk_size=500, max_chunk_size=1000)
        assert "code" in chunkers

    def test_optional_chunkers_depend_on_flags(self):
        """Optional chunkers should only appear when their HAS_* flag is True."""
        from stele_context.chunkers import (
            HAS_IMAGE_CHUNKER,
            HAS_PDF_CHUNKER,
            HAS_AUDIO_CHUNKER,
            HAS_VIDEO_CHUNKER,
        )

        chunkers = init_chunkers(chunk_size=500, max_chunk_size=1000)

        assert ("image" in chunkers) == HAS_IMAGE_CHUNKER
        assert ("pdf" in chunkers) == HAS_PDF_CHUNKER
        assert ("audio" in chunkers) == HAS_AUDIO_CHUNKER
        assert ("video" in chunkers) == HAS_VIDEO_CHUNKER

    def test_chunk_size_passed_to_chunkers(self):
        """Chunkers should respect the chunk_size parameter."""
        chunkers = init_chunkers(chunk_size=256, max_chunk_size=512)
        assert chunkers["text"].chunk_size == 256
        assert chunkers["code"].chunk_size == 256

    def test_max_chunk_size_passed_to_chunkers(self):
        """Chunkers should respect the max_chunk_size parameter."""
        chunkers = init_chunkers(chunk_size=256, max_chunk_size=512)
        assert chunkers["text"].max_chunk_size == 512
        assert chunkers["code"].max_chunk_size == 512
