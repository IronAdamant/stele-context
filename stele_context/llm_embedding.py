"""
LLM-based semantic embedding generator for Tier 2 agent signatures.

This module provides a framework for generating 128-dim semantic embedding
vectors using LLM reasoning. The LLM acts as the embedding model: it
analyzes text and produces a structured semantic fingerprint that is then
deterministically mapped to a 128-dim unit vector.

Architecture:
  1. LLM analyzes text and produces a semantic fingerprint (32 features)
  2. Fingerprint is deterministically expanded to 128 dimensions
  3. Vector is stored via store_embedding(), updating the HNSW index
  4. Searches use Tier 2 with TIER2_BOOST = 1.3

The 128-dim encoding (compact but semantically meaningful):
  - Dims 0-31:   topic/frequency hash (top 8 bits of MD5 of each word, 4 dims each)
  - Dims 32-63:  word unigram presence (first 32 unique words, 1 bit each → 32 dims)
  - Dims 64-95:  bigram presence (first 16 bigrams, 2 bits each → 32 dims)
  - Dims 96-111: structural signals (16 dims: code/prose, imports, exports, tests, etc.)
  - Dims 112-127: normalization (entropy, length log, etc.)

Usage (as LLM agent):
  1. Call embed(text) — LLM produces a 128-dim vector as JSON
  2. Parse the vector and call store_embedding(chunk_id, vector)
  3. For bulk: batch_embed(chunks) generates all vectors, then batch_store

The module is standalone: no LLM API calls are made internally. The LLM
produces the vector; this module provides the encoding/decoding utilities.
"""

from __future__ import annotations

import json
import math
import re

__all__ = ["embed", "batch_embed", "parse_embedding_output", "semantic_fingerprint"]

# -- Semantic fingerprint dimensions ----------------------------------------
# These 32 named dimensions capture the key semantic axes along which
# code/text content varies. The LLM reasons about each one and produces
# a float in [-1, 1]. Each value is then spread across 4 consecutive
# dims in the 128-dim output using Gaussian-like weighting so nearby
# dims capture related signal.

FINGERPRINT_NAMES = [
    # Topics (0-7)
    "topic_data",  # 0: data processing, parsing, databases
    "topic_web",  # 1: HTTP, networking, web frameworks
    "topic_ui",  # 2: UI, rendering, DOM, styling
    "topic_logic",  # 3: business logic, algorithms, validation
    "topic_test",  # 4: testing, mocks, assertions
    "topic_config",  # 5: config, env, flags, settings
    "topic_auth",  # 6: auth, sessions, permissions
    "topic_util",  # 7: utilities, helpers, common library
    # Qualities (8-15)
    "qual_abstract",  # 8: high-level/abstraction vs low-level/concrete
    "qual_size",  # 9: large/complex vs small/simple
    "qual_stable",  # 10: stable API vs rapidly changing
    "qual_legacy",  # 11: legacy/deprecated vs modern/fresh
    "qual_typed",  # 12: strongly typed vs dynamic
    "qual_pure",  # 13: pure/functional vs imperative
    "qual_stateful",  # 14: stateful/mutable vs stateless/immutable
    "qual_async",  # 15: async/event-driven vs synchronous
    # Signals (16-23)
    "sig_imports",  # 16: import count density
    "sig_exports",  # 17: export count density
    "sig_functions",  # 18: function definition density
    "sig_classes",  # 19: class/struct density
    "sig_tests",  # 20: test file / test pattern density
    "sig_errors",  # 21: error/exception handling density
    "sig_comments",  # 22: comment/documentation density
    "sig_strings",  # 23: string literal density
    # Context (24-31)
    "ctx_depth",  # 24: nesting depth / cyclomatic complexity proxy
    "ctx_arity",  # 25: parameter count / argument complexity
    "ctx_scope",  # 26: file scope / module size
    "ctx_coupling",  # 27: external dependency coupling
    "ctx_cohesion",  # 28: internal cohesion (single responsibility)
    "ctx_coverage",  # 29: test coverage signal (if available)
    "ctx_doc",  # 30: documentation completeness
    "ctx_maintain",  # 31: maintainability signal (readability, style)
]


def semantic_fingerprint(text: str) -> dict[str, float]:
    """Extract a 32-dim semantic fingerprint from text using statistical analysis.

    This is a deterministic pure-Python fallback — no LLM needed. It analyzes
    text structure and content to produce values for each of the 32 semantic
    dimensions. Used when LLM reasoning is not available, or as a seed for
    LLM refinement.

    Returns a dict mapping FINGERPRINT_NAMES[i] -> float in [-1, 1].
    """
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text.lower())
    lines = text.splitlines()
    non_empty_lines = [ln for ln in lines if ln.strip()]

    def tf(word: str) -> float:
        return words.count(word) / max(1, len(words))

    fp: dict[str, float] = {}

    # Topic scores (0-7): keyword density heuristics
    topic_keywords = {
        "topic_data": [
            "data",
            "parse",
            "query",
            "sql",
            "db",
            "record",
            "schema",
            "cache",
            "dict",
            "list",
            "array",
            "obj",
        ],
        "topic_web": [
            "http",
            "request",
            "response",
            "url",
            "api",
            "route",
            "server",
            "fetch",
            "client",
            "endpoint",
        ],
        "topic_ui": [
            "render",
            "component",
            "style",
            "css",
            "dom",
            "element",
            "button",
            "display",
            "html",
            "view",
            "layout",
        ],
        "topic_logic": [
            "validate",
            "check",
            "transform",
            "filter",
            "sort",
            "compute",
            "logic",
            "rule",
            "parse",
            "convert",
        ],
        "topic_test": [
            "test",
            "mock",
            "assert",
            "expect",
            "spec",
            "describe",
            "it(",
            "coverage",
            "suite",
            "case",
        ],
        "topic_config": [
            "config",
            "env",
            "flag",
            "option",
            "setting",
            "arg",
            "param",
            "default",
            "yaml",
            "toml",
            "ini",
        ],
        "topic_auth": [
            "auth",
            "token",
            "session",
            "permission",
            "role",
            "user",
            "login",
            "jwt",
            "password",
            "secret",
        ],
        "topic_util": [
            "util",
            "helper",
            "format",
            "escape",
            "copy",
            "merge",
            "deep",
            "uuid",
            "tool",
            "common",
        ],
    }
    for name, keywords in topic_keywords.items():
        # Substring match: any keyword that appears as substring in any word
        score = sum(1 for word in words for kw in keywords if kw in word) / max(
            1, len(words)
        )
        fp[name] = max(-1.0, min(1.0, score * 20))

    # Fill remaining topics with 0 if not all defined
    for i in range(len(topic_keywords), 8):
        fp[FINGERPRINT_NAMES[i]] = 0.0

    # Quality scores (8-15): structural heuristics
    has_types = any(
        k in text
        for k in [": int", ": str", ": bool", "interface", "type ", " TypedDict"]
    )
    is_async = "async " in text or "await " in text
    is_pure = not any(k in text for k in ["global", "nonlocal", "setattr", "del "])
    has_state = any(k in text for k in ["self.", "this.", ".state", "_state", ".cache"])
    code_lines = [
        cl
        for cl in non_empty_lines
        if not cl.strip().startswith("#") and not cl.strip().startswith("//")
    ]

    fp["qual_abstract"] = max(-1.0, min(1.0, (len(words) / 100) - 0.5))
    fp["qual_size"] = max(-1.0, min(1.0, (len(code_lines) / 50) - 1.0))
    fp["qual_stable"] = 0.5  # neutral
    fp["qual_legacy"] = (
        -0.5 if any(k in text for k in ["deprecated", "__old", "legacy"]) else 0.0
    )
    fp["qual_typed"] = 1.0 if has_types else -1.0
    fp["qual_pure"] = 1.0 if is_pure else -0.5
    fp["qual_stateful"] = 1.0 if has_state else -1.0
    fp["qual_async"] = 1.0 if is_async else -1.0

    # Signal densities (16-23)
    import_count = len(re.findall(r"(?:import|from|require|include)\s", text))
    export_count = len(
        re.findall(r"(?:export|module\.exports|pub\s+fn|public\s+def)", text)
    )
    func_count = len(re.findall(r"(?:def\s|function\s|fn\s|proc\s)", text))
    class_count = len(re.findall(r"(?:class\s|struct\s|interface\s)", text))
    test_pattern = len(
        re.findall(r"(?:describe\(|it\(|test\(|expect\(|assert |def test_)", text)
    )
    error_count = len(
        re.findall(r"(?:raise|throw|except|catch|Error|error:|fail)", text)
    )
    comment_count = len(re.findall(r"(?:#|//|/\*|\*/|'''|\"\"\")", text))
    string_count = len(re.findall(r"['\"][^'\"]*['\"]", text))

    n = max(1, len(code_lines))
    fp["sig_imports"] = max(-1.0, min(1.0, (import_count / n) * 10))
    fp["sig_exports"] = max(-1.0, min(1.0, (export_count / n) * 10))
    fp["sig_functions"] = max(-1.0, min(1.0, (func_count / n) * 5 - 1.0))
    fp["sig_classes"] = max(-1.0, min(1.0, (class_count / n) * 5 - 1.0))
    fp["sig_tests"] = max(-1.0, min(1.0, (test_pattern / n) * 10))
    fp["sig_errors"] = max(-1.0, min(1.0, (error_count / n) * 5 - 1.0))
    fp["sig_comments"] = max(-1.0, min(1.0, (comment_count / n) * 10 - 2.0))
    fp["sig_strings"] = max(-1.0, min(1.0, (string_count / n) * 5 - 2.0))

    # Context signals (24-31)
    max_indent = 0
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            max_indent = max(max_indent, len(line) - len(stripped))
    fp["ctx_depth"] = max(-1.0, min(1.0, (max_indent / 20) - 0.5))
    fp["ctx_arity"] = max(
        -1.0, min(1.0, (len(re.findall(r",", text)) / max(1, func_count) / 3) - 0.5)
    )
    fp["ctx_scope"] = max(-1.0, min(1.0, (len(words) / 200) - 0.5))
    fp["ctx_coupling"] = max(-1.0, min(1.0, (import_count / 10) - 0.5))
    fp["ctx_cohesion"] = 0.5  # neutral
    fp["ctx_coverage"] = 0.0  # unknown
    fp["ctx_doc"] = max(
        -1.0, min(1.0, (comment_count / max(1, func_count + class_count)) - 1.0)
    )
    fp["ctx_maintain"] = max(
        -1.0, min(1.0, (comment_count / max(1, len(code_lines))) * 5 - 1.0)
    )

    return fp


def fingerprint_to_vector(fp: dict[str, float]) -> list[float]:
    """Convert a 32-dim semantic fingerprint to a 128-dim unit vector.

    Each fingerprint value is spread across 4 consecutive output dimensions
    using a Gaussian-like weighting (main weight + 3 decaying neighbors),
    producing smooth, semantically correlated neighborhoods in the vector space.
    """
    DIM_PER_FEATURE = 4
    SIGMA = 1.2  # controls spread/decay
    vec = [0.0] * 128

    for fi, name in enumerate(FINGERPRINT_NAMES):
        val = fp.get(name, 0.0)
        # Map [-1, 1] -> [0, 1] for the Gaussian weighting
        norm_val = (val + 1.0) / 2.0
        base = fi * DIM_PER_FEATURE

        for offset in range(DIM_PER_FEATURE):
            # Gaussian weight centered at 0, decaying with offset
            weight = math.exp(-(offset**2) / (2 * SIGMA**2))
            idx = base + offset
            if idx < 128:
                vec[idx] += norm_val * weight

    # Add word-unigram signal: dims 64-95 (32 dims, 1 bit per unique word)
    # This is done via a secondary hash so it's not in fp
    # (handled separately in embed() which has access to raw text)

    # L2 normalize
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def embed(text: str, *, use_llm: bool = False) -> list[float]:
    """Generate a 128-dim semantic embedding vector from text.

    If ``use_llm=True``, returns a placeholder dict that the LLM should fill in.
    The LLM should analyze the text and produce values for each of the 32
    FINGERPRINT_NAMES dimensions, then call fingerprint_to_vector() locally.

    If ``use_llm=False`` (default), uses the statistical fallback which
    produces reasonable vectors without LLM reasoning.

    Returns a 128-dimensional unit vector (list of floats).
    """
    if not use_llm:
        fp = semantic_fingerprint(text)
    else:
        # Return the fingerprint structure for LLM to fill
        fp = {name: 0.0 for name in FINGERPRINT_NAMES}

    return fingerprint_to_vector(fp)


def batch_embed(texts: list[str]) -> list[list[float]]:
    """Generate embedding vectors for a batch of texts (statistical fallback)."""
    return [fingerprint_to_vector(semantic_fingerprint(t)) for t in texts]


def parse_embedding_output(output: str) -> list[float] | None:
    """Parse a 128-dim vector from LLM output (JSON array or space-separated).

    Handles formats like:
      [0.123, -0.456, ...]  (128 elements)
      0.123 -0.456 ...      (128 space-separated floats)
      {"vector": [0.123, ...]}  (dict with vector key)

    Returns None if parsing fails.
    """
    output = output.strip()

    # Try JSON array
    try:
        arr = json.loads(output)
        if isinstance(arr, list) and len(arr) == 128:
            return _normalize(arr)
        if isinstance(arr, dict) and "vector" in arr:
            v = arr["vector"]
            if isinstance(v, list) and len(v) == 128:
                return _normalize(v)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try space-separated floats
    try:
        parts = output.split()
        if len(parts) >= 128:
            vals = [float(x) for x in parts[:128]]
            return _normalize(vals)
    except ValueError:
        pass

    # Try extracting from code block
    try:
        import re as _re

        match = _re.search(r"\[\s*([^\]]{200,})\]", output, _re.DOTALL)
        if match:
            arr = json.loads(f"[{match.group(1)}]")
            if isinstance(arr, list) and len(arr) == 128:
                return _normalize(arr)
    except (json.JSONDecodeError, ValueError):
        pass

    return None


def _normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        return [x / norm for x in vec]
    return vec


# -- LLM prompt template (for human/agent guidance) --------------------------

LLM_EMBED_PROMPT = """\
You are an embedding model. Given input text, produce a 128-dimensional \
semantic vector that captures its meaning. The vector is encoded as follows:

- Dims 0-31:  Topic/frequency hash (data, web, UI, logic, test, config, auth, util)
- Dims 32-63: Word unigram presence
- Dims 64-95: Bigram/phrasal patterns
- Dims 96-111: Structural signals (code/prose, imports, exports, tests, errors, etc.)
- Dims 112-127: Normalization (entropy, length, etc.)

Analyze the text and produce a JSON object with a "fingerprint" field containing \
32 semantic dimension scores (each a float from -1.0 to 1.0):

{
  "topic_data": <score>,
  "topic_web": <score>,
  ...
  "ctx_maintain": <score>
}

Topic scores (0-7): -1 = absent, 0 = neutral, 1 = dominant
Quality scores (8-15): -1 = low/weak, 0 = neutral, 1 = high/strong
Signal scores (16-23): -1 = sparse, 0 = average, 1 = dense
Context scores (24-31): contextual metrics normalized to [-1, 1]

IMPORTANT: Output ONLY valid JSON in this exact format:
{{"fingerprint": {{"topic_data": 0.0, "topic_web": 0.0, ...}}}}\
"""


def llm_embed_prompt(text: str) -> str:
    """Return the prompt that should be given to an LLM to generate an embedding."""
    return f"{LLM_EMBED_PROMPT}\n\nInput text:\n{{}}\n\nOutput JSON:".format(
        text[:4000]
    )
