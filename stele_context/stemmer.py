"""Pure-Python Porter stemmer (zero dependencies).

Implements the standard 5-step Porter stemming algorithm as described in:
    Porter, M.F. "An algorithm for suffix stripping."
    Program 14.3 (1980): 130-137.

Public API
----------
stem(word)              -- stem a single word
split_identifier(name)  -- split camelCase / snake_case into component words
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contains_vowel(stem: str) -> bool:
    """Return True if *stem* contains at least one vowel."""
    for i, ch in enumerate(stem):
        if _is_vowel(stem, i):
            return True
    return False


def _is_vowel(word: str, i: int) -> bool:
    """Return True if word[i] is a vowel (a, e, i, o, u, or y not preceded by a vowel)."""
    ch = word[i]
    if ch in "aeiou":
        return True
    if ch == "y":
        return i == 0 or not _is_vowel(word, i - 1)
    return False


def _measure(stem: str) -> int:
    """Return the *measure* m of a stem -- the number of VC sequences.

    [C](VC)^m[V]  where C = consonant sequence, V = vowel sequence.
    """
    if not stem:
        return 0

    # Build a CV pattern string.
    cv = []
    for i, ch in enumerate(stem):
        cv.append("V" if _is_vowel(stem, i) else "C")
    # Collapse runs.
    pattern = cv[0]
    for c in cv[1:]:
        if c != pattern[-1]:
            pattern += c
    # Strip optional leading C and trailing V.
    if pattern.startswith("C"):
        pattern = pattern[1:]
    if pattern.endswith("V"):
        pattern = pattern[:-1]
    # What remains should be alternating VC pairs.
    return pattern.count("VC")


def _ends_double_consonant(word: str) -> bool:
    """Return True if *word* ends with a double consonant."""
    if len(word) < 2:
        return False
    return word[-1] == word[-2] and not _is_vowel(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    """Return True if *word* ends consonant-vowel-consonant (and the last C is not w, x, or y)."""
    if len(word) < 3:
        return False
    return (
        not _is_vowel(word, len(word) - 1)
        and _is_vowel(word, len(word) - 2)
        and not _is_vowel(word, len(word) - 3)
        and word[-1] not in "wxy"
    )


# ---------------------------------------------------------------------------
# Porter stemmer -- 5 steps
# ---------------------------------------------------------------------------


def _step1a(word: str) -> str:
    """Deal with plurals."""
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies"):
        return word[:-2]
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def _step1b(word: str) -> str:
    """Deal with -ed, -ing."""
    if word.endswith("eed"):
        stem = word[:-3]
        if _measure(stem) > 0:
            return word[:-1]  # eed -> ee
        return word

    made_change = False
    if word.endswith("ed"):
        stem = word[:-2]
        if _contains_vowel(stem):
            word = stem
            made_change = True
    elif word.endswith("ing"):
        stem = word[:-3]
        if _contains_vowel(stem):
            word = stem
            made_change = True

    if made_change:
        if word.endswith("at") or word.endswith("bl") or word.endswith("iz"):
            word += "e"
        elif _ends_double_consonant(word) and word[-1] not in "lsz":
            word = word[:-1]
        elif _measure(word) == 1 and _ends_cvc(word):
            word += "e"

    return word


def _step1c(word: str) -> str:
    """Turn terminal y to i when there is another vowel in the stem."""
    if word.endswith("y"):
        stem = word[:-1]
        if _contains_vowel(stem):
            return stem + "i"
    return word


# Step 2-4 mapping tables.
_STEP2_SUFFIXES = [
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("abli", "able"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
]

_STEP3_SUFFIXES = [
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ful", ""),
    ("ness", ""),
]

_STEP4_SUFFIXES = [
    "al",
    "ance",
    "ence",
    "er",
    "ic",
    "able",
    "ible",
    "ant",
    "ement",
    "ment",
    "ent",
    "ion",
    "ou",
    "ism",
    "ate",
    "iti",
    "ous",
    "ive",
    "ize",
]


def _step2(word: str) -> str:
    for suffix, replacement in _STEP2_SUFFIXES:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if _measure(stem) > 0:
                return stem + replacement
            return word
    return word


def _step3(word: str) -> str:
    for suffix, replacement in _STEP3_SUFFIXES:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if _measure(stem) > 0:
                return stem + replacement
            return word
    return word


def _step4(word: str) -> str:
    for suffix in _STEP4_SUFFIXES:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if suffix == "ion":
                if _measure(stem) > 1 and len(stem) > 0 and stem[-1] in "st":
                    return stem
            elif _measure(stem) > 1:
                return stem
            return word
    return word


def _step5a(word: str) -> str:
    """Remove a final -e if m > 1, or m == 1 and not *o."""
    if word.endswith("e"):
        stem = word[:-1]
        m = _measure(stem)
        if m > 1:
            return stem
        if m == 1 and not _ends_cvc(stem):
            return stem
    return word


def _step5b(word: str) -> str:
    """Remove double final consonant when m > 1 (e.g. -ll -> -l)."""
    if _ends_double_consonant(word) and _measure(word) > 1:
        return word[:-1]
    return word


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stem(word: str) -> str:
    """Return the Porter stem of *word*.

    The word is lowercased before stemming.  Very short words (length <= 2)
    are returned unchanged.

    >>> stem("running")
    'run'
    >>> stem("connections")
    'connect'
    """
    word = word.lower().strip()
    if len(word) <= 2:
        return word

    word = _step1a(word)
    word = _step1b(word)
    word = _step1c(word)
    word = _step2(word)
    word = _step3(word)
    word = _step4(word)
    word = _step5a(word)
    word = _step5b(word)
    return word


# ---------------------------------------------------------------------------
# Identifier splitting
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]+|[a-z]+|\d+")
_SNAKE_SEP_RE = re.compile(r"[_\-]+")


def split_identifier(name: str) -> list[str]:
    """Split a camelCase, PascalCase, or snake_case identifier into words.

    Returns lowercase component words.  Numbers are preserved as separate
    tokens.  Empty / whitespace-only input returns an empty list.

    >>> split_identifier("loginHandler")
    ['login', 'handler']
    >>> split_identifier("get_user_name")
    ['get', 'user', 'name']
    >>> split_identifier("HTMLParser")
    ['html', 'parser']
    >>> split_identifier("getHTTPSConnection")
    ['get', 'https', 'connection']
    """
    if not name or not name.strip():
        return []

    # First split on underscores / hyphens.
    parts = _SNAKE_SEP_RE.split(name.strip())

    words: list[str] = []
    for part in parts:
        if not part:
            continue
        # Then split each part on camelCase boundaries.
        matches = _CAMEL_RE.findall(part)
        for m in matches:
            lowered = m.lower()
            if lowered:
                words.append(lowered)

    return words
