"""Tests for numpy_compat pure-Python fallback layer."""

import math
import struct

import pytest

from stele.chunkers.numpy_compat import (
    cosine_similarity,
    sig_from_bytes,
    sig_to_bytes,
    sig_to_list,
)


class TestSigRoundTrip:
    """Tests for sig_to_bytes / sig_from_bytes round-trip fidelity."""

    def test_round_trip_128_dim(self):
        """Standard 128-dim signature survives a round trip."""
        sig = [float(i) / 128.0 for i in range(128)]
        restored = sig_to_list(sig_from_bytes(sig_to_bytes(sig)))
        assert len(restored) == 128
        for orig, got in zip(sig, restored):
            assert got == pytest.approx(orig, abs=1e-6)

    def test_round_trip_single_element(self):
        """Single-element signature round trips correctly."""
        sig = [3.14]
        restored = sig_to_list(sig_from_bytes(sig_to_bytes(sig)))
        assert len(restored) == 1
        assert restored[0] == pytest.approx(3.14, abs=1e-6)

    def test_round_trip_empty(self):
        """Empty signature produces empty bytes and restores to empty list."""
        sig = []
        raw = sig_to_bytes(sig)
        assert raw == b""
        restored = sig_to_list(sig_from_bytes(raw))
        assert restored == []

    def test_round_trip_negative_values(self):
        """Negative floats survive the round trip."""
        sig = [-1.0, -0.5, 0.0, 0.5, 1.0]
        restored = sig_to_list(sig_from_bytes(sig_to_bytes(sig)))
        for orig, got in zip(sig, restored):
            assert got == pytest.approx(orig, abs=1e-6)

    def test_round_trip_large_values(self):
        """Large magnitude floats survive the round trip within float32 precision."""
        sig = [1e10, -1e10, 1e-10, -1e-10]
        restored = sig_to_list(sig_from_bytes(sig_to_bytes(sig)))
        for orig, got in zip(sig, restored):
            assert got == pytest.approx(orig, rel=1e-6)

    def test_bytes_length_matches_float32(self):
        """Output bytes length equals 4 * number of elements (float32)."""
        for n in [0, 1, 5, 128]:
            sig = [0.0] * n
            assert len(sig_to_bytes(sig)) == 4 * n


class TestSigToBytes:
    """Tests for sig_to_bytes encoding."""

    def test_known_encoding(self):
        """A single 1.0 encodes to the IEEE 754 float32 representation."""
        raw = sig_to_bytes([1.0])
        expected = struct.pack("f", 1.0)
        assert raw == expected

    def test_two_values(self):
        """Two-element signature matches struct.pack output."""
        sig = [2.0, -3.0]
        raw = sig_to_bytes(sig)
        assert raw == struct.pack("2f", 2.0, -3.0)


class TestSigFromBytes:
    """Tests for sig_from_bytes decoding."""

    def test_known_decoding(self):
        """Manually packed float32 bytes decode to expected values."""
        data = struct.pack("3f", 1.0, 2.0, 3.0)
        result = sig_to_list(sig_from_bytes(data))
        assert result == pytest.approx([1.0, 2.0, 3.0])

    def test_empty_bytes(self):
        """Empty bytes produce an empty result."""
        result = sig_to_list(sig_from_bytes(b""))
        assert result == []


class TestCosineSimilarity:
    """Tests for cosine_similarity between vectors."""

    def test_identical_vectors(self):
        """Identical non-zero vectors have cosine similarity 1.0."""
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_identical_unit_vector(self):
        """A unit vector compared to itself gives 1.0."""
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        """Opposite vectors have cosine similarity -1.0."""
        v1 = [1.0, 2.0, 3.0]
        v2 = [-1.0, -2.0, -3.0]
        assert cosine_similarity(v1, v2) == pytest.approx(-1.0)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors have cosine similarity 0.0."""
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert cosine_similarity(v1, v2) == pytest.approx(0.0)

    def test_orthogonal_3d(self):
        """Orthogonal 3D vectors have cosine similarity 0.0."""
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 0.0, 1.0]
        assert cosine_similarity(v1, v2) == pytest.approx(0.0)

    def test_zero_first_vector(self):
        """Zero first vector returns 0.0 (no division error)."""
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_zero_second_vector(self):
        """Zero second vector returns 0.0 (no division error)."""
        assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_both_zero_vectors(self):
        """Both zero vectors returns 0.0."""
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    def test_single_dimension(self):
        """Single-dimension vectors work correctly."""
        assert cosine_similarity([5.0], [3.0]) == pytest.approx(1.0)
        assert cosine_similarity([5.0], [-3.0]) == pytest.approx(-1.0)

    def test_similar_vectors(self):
        """Similar but not identical vectors have high cosine similarity."""
        v1 = [1.0, 1.0, 0.0]
        v2 = [1.0, 1.0, 0.1]
        sim = cosine_similarity(v1, v2)
        assert 0.9 < sim < 1.0

    def test_result_in_range(self):
        """Result is always in [-1, 1] for non-zero vectors."""
        import random

        rng = random.Random(42)
        for _ in range(50):
            v1 = [rng.gauss(0, 1) for _ in range(10)]
            v2 = [rng.gauss(0, 1) for _ in range(10)]
            sim = cosine_similarity(v1, v2)
            assert -1.0 - 1e-9 <= sim <= 1.0 + 1e-9

    def test_symmetry(self):
        """cosine_similarity(a, b) == cosine_similarity(b, a)."""
        v1 = [1.0, 2.0, 3.0]
        v2 = [4.0, 5.0, 6.0]
        assert cosine_similarity(v1, v2) == pytest.approx(cosine_similarity(v2, v1))

    def test_scale_invariance(self):
        """Scaling a vector does not change cosine similarity."""
        v1 = [1.0, 2.0, 3.0]
        v2 = [4.0, 5.0, 6.0]
        v1_scaled = [x * 100.0 for x in v1]
        assert cosine_similarity(v1, v2) == pytest.approx(
            cosine_similarity(v1_scaled, v2)
        )

    def test_known_angle(self):
        """45-degree angle between vectors gives cos(pi/4)."""
        v1 = [1.0, 0.0]
        v2 = [1.0, 1.0]
        expected = math.cos(math.pi / 4)
        assert cosine_similarity(v1, v2) == pytest.approx(expected, abs=1e-9)


class TestSigToList:
    """Tests for sig_to_list conversion."""

    def test_list_passthrough(self):
        """A plain list is returned as a list."""
        sig = [1.0, 2.0, 3.0]
        result = sig_to_list(sig)
        assert result == [1.0, 2.0, 3.0]
        assert isinstance(result, list)

    def test_tuple_input(self):
        """A tuple is converted to a list."""
        result = sig_to_list((1.0, 2.0))
        assert result == [1.0, 2.0]
        assert isinstance(result, list)

    def test_empty_input(self):
        """An empty iterable returns an empty list."""
        assert sig_to_list([]) == []
        assert sig_to_list(()) == []

    def test_from_bytes_result(self):
        """Result of sig_from_bytes can be converted to list."""
        data = struct.pack("3f", 1.0, 2.0, 3.0)
        sig = sig_from_bytes(data)
        result = sig_to_list(sig)
        assert isinstance(result, list)
        assert result == pytest.approx([1.0, 2.0, 3.0])

    def test_generator_input(self):
        """A generator is consumed and converted to a list."""
        gen = (float(i) for i in range(3))
        result = sig_to_list(gen)
        assert result == [0.0, 1.0, 2.0]
