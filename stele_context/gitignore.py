"""
Pure-stdlib .gitignore matcher for indexing.

Reads the project root's ``.gitignore`` so directory expansion skips
ignored files by default (node_modules trees, build output, secrets),
without users having to mirror their ignore rules into ``skip_dirs``.

Supported gitignore semantics:
  - blank lines and ``#`` comments
  - ``!`` negation (last matching rule wins)
  - trailing ``/`` directory-only patterns
  - leading ``/`` and embedded ``/`` anchor a pattern to the root
  - ``*`` (no slash), ``?``, ``[...]`` classes, and ``**`` globs
  - files under an ignored directory are ignored

Deliberately out of scope (rarely load-bearing, documented):
nested ``.gitignore`` files, ``.git/info/exclude``, escaped characters.

Standalone module — zero internal Stele dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class _Rule:
    regex: re.Pattern
    negate: bool
    dir_only: bool


def _translate(pattern: str) -> str:
    """Translate a gitignore glob into a regex over '/'-separated paths."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape("["))
                i += 1
            else:
                cls = pattern[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append(f"[{cls}]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _compile_rules(lines: list[str]) -> list[_Rule]:
    rules: list[_Rule] = []
    for raw in lines:
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        dir_only = line.endswith("/")
        if dir_only:
            line = line.rstrip("/")
        if not line:
            continue
        anchored = line.startswith("/") or "/" in line
        line = line.lstrip("/")
        body = _translate(line)
        # Anchored patterns match from the root; unanchored ones match any
        # trailing path component (git's implicit **/ prefix).
        pat = f"^{body}$" if anchored else f"(?:^|/){body}$"
        try:
            rules.append(_Rule(re.compile(pat), negate, dir_only))
        except re.error:
            continue
    return rules


class GitignoreMatcher:
    """Matches paths against a compiled .gitignore rule set."""

    __slots__ = ("base", "rules")

    def __init__(self, lines: list[str], base: Path):
        self.base = base.resolve()
        self.rules = _compile_rules(lines)

    @classmethod
    def load(cls, project_root: Path | None) -> GitignoreMatcher | None:
        """Build a matcher from <project_root>/.gitignore, or None if absent."""
        if project_root is None:
            return None
        gitignore = project_root / ".gitignore"
        try:
            lines = gitignore.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        matcher = cls(lines, project_root)
        return matcher if matcher.rules else None

    def _last_match(self, rel: str, is_dir: bool) -> bool | None:
        """Return the verdict of the last matching rule, or None if no match."""
        verdict: bool | None = None
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.search(rel):
                verdict = not rule.negate
        return verdict

    def is_ignored(self, rel_path: str, is_dir: bool = False) -> bool:
        """Check a '/'-separated path relative to the matcher's base."""
        parts = rel_path.split("/")
        # An ignored ancestor directory ignores everything inside it.
        for i in range(1, len(parts)):
            if self._last_match("/".join(parts[:i]), is_dir=True):
                return True
        return bool(self._last_match(rel_path, is_dir))

    def is_ignored_path(self, path: Path, is_dir: bool = False) -> bool:
        """Check an absolute or cwd-relative path; False when outside base."""
        try:
            rel = path.resolve().relative_to(self.base)
        except (OSError, ValueError):
            return False
        return self.is_ignored(rel.as_posix(), is_dir=is_dir)
