"""
Stele engine — smart context cache with semantic chunking and vector search.
"""

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from stele.config import load_config, apply_config
from stele.coordination import CoordinationBackend, detect_git_common_dir
from stele.rwlock import RWLock
from stele.symbol_graph import SymbolGraphManager

from stele.chunkers.numpy_compat import (
    cosine_similarity,
    sig_to_list,
    sig_from_bytes,
)
from stele.chunkers.base import Chunk
from stele.chunkers import (
    TextChunker,
    CodeChunker,
    HAS_IMAGE_CHUNKER,
    HAS_PDF_CHUNKER,
    HAS_AUDIO_CHUNKER,
    HAS_VIDEO_CHUNKER,
)
from stele.index import VectorIndex
from stele.index_store import (
    compute_chunk_ids_hash,
    load_if_fresh,
    save_index,
    save_bm25,
    load_bm25_if_fresh,
)
from stele.session import SessionManager
from stele.storage import StorageBackend


def _get_version() -> str:
    """Get version without circular import."""
    from stele import __version__

    return __version__


def _read_and_hash(path: Path, modality: str) -> tuple:
    """Read file content and compute SHA-256 hash.

    Returns (content, content_hash) where content is bytes for binary
    modalities and str for text/code/pdf.
    """
    if modality in ("image", "audio", "video"):
        raw = path.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()
    content = path.read_text(encoding="utf-8", errors="replace")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


class Stele:
    """Smart context cache with semantic chunking and vector search."""

    DEFAULT_CHUNK_SIZE = 256
    DEFAULT_MAX_CHUNK_SIZE = 4096
    DEFAULT_MERGE_THRESHOLD = 0.7
    DEFAULT_CHANGE_THRESHOLD = 0.85
    DEFAULT_SEARCH_ALPHA = 0.7
    DEFAULT_SKIP_DIRS = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".eggs",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }

    MODALITY_THRESHOLDS = {
        "text": {"merge": 0.70, "change": 0.85},
        "code": {"merge": 0.85, "change": 0.80},
        "pdf": {"merge": 0.75, "change": 0.85},
    }

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        project_root: Optional[str] = None,
        enable_coordination: bool = True,
        chunk_size: Optional[int] = None,
        max_chunk_size: Optional[int] = None,
        merge_threshold: Optional[float] = None,
        change_threshold: Optional[float] = None,
        search_alpha: Optional[float] = None,
        skip_dirs: Optional[set] = None,
    ):
        self._project_root = self._detect_project_root(project_root)

        # Load .stele.toml config — explicit constructor params win
        file_cfg = load_config(self._project_root)
        cfg = apply_config(
            file_cfg,
            storage_dir=storage_dir,
            chunk_size=chunk_size,
            max_chunk_size=max_chunk_size,
            merge_threshold=merge_threshold,
            change_threshold=change_threshold,
            search_alpha=search_alpha,
            skip_dirs=skip_dirs,
        )

        resolved_storage = cfg.get("storage_dir", storage_dir)
        if resolved_storage is None:
            resolved_storage = os.environ.get("STELE_STORAGE_DIR")
        if resolved_storage is None and self._project_root is not None:
            resolved_storage = str(self._project_root / ".stele")
        self.storage = StorageBackend(resolved_storage)
        self.chunk_size = cfg.get("chunk_size", self.DEFAULT_CHUNK_SIZE)
        self.max_chunk_size = cfg.get("max_chunk_size", self.DEFAULT_MAX_CHUNK_SIZE)
        self.merge_threshold = cfg.get("merge_threshold", self.DEFAULT_MERGE_THRESHOLD)
        self.change_threshold = cfg.get(
            "change_threshold", self.DEFAULT_CHANGE_THRESHOLD
        )
        self.search_alpha = cfg.get("search_alpha", self.DEFAULT_SEARCH_ALPHA)
        self.skip_dirs = self.DEFAULT_SKIP_DIRS | cfg.get("skip_dirs", set())
        self._init_chunkers()
        self.vector_index = self._load_or_rebuild_index()
        self.session_manager = SessionManager(self.storage, self.vector_index)
        self.symbol_manager = SymbolGraphManager(self.storage)
        self.bm25_index: Optional[Any] = None
        self._bm25_ready = False
        self._lock = RWLock()
        self._bm25_init_lock = threading.Lock()
        self._coordination = self._init_coordination() if enable_coordination else None

    def _init_coordination(self) -> Optional[CoordinationBackend]:
        """Initialize cross-worktree coordination if git common dir exists."""
        git_common = detect_git_common_dir(self._project_root)
        if git_common is None:
            return None
        try:
            return CoordinationBackend(git_common)
        except (OSError, Exception):
            return None

    # -- Lock routing helpers (coordination or local) -------------------------

    def _do_acquire_lock(
        self,
        doc_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> Dict[str, Any]:
        if self._coordination:
            return self._coordination.acquire_lock(doc_path, agent_id, ttl, force)
        return self.storage.acquire_document_lock(doc_path, agent_id, ttl, force)

    def _do_get_lock_status(self, doc_path: str) -> Dict[str, Any]:
        if self._coordination:
            return self._coordination.get_lock_status(doc_path)
        return self.storage.get_document_lock_status(doc_path)

    def _do_release_lock(
        self,
        doc_path: str,
        agent_id: str,
    ) -> Dict[str, Any]:
        if self._coordination:
            return self._coordination.release_lock(doc_path, agent_id)
        return self.storage.release_document_lock(doc_path, agent_id)

    def _do_record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        **kwargs: Any,
    ) -> Optional[int]:
        if self._coordination:
            return self._coordination.record_conflict(
                document_path,
                agent_a,
                agent_b,
                conflict_type,
                **kwargs,
            )
        return self.storage.record_conflict(
            document_path,
            agent_a,
            agent_b,
            conflict_type,
            **kwargs,
        )

    @staticmethod
    def _detect_project_root(
        explicit: Optional[str] = None,
    ) -> Optional[Path]:
        """Detect project root by walking up from CWD looking for .git.

        Works with both normal repos (.git directory) and worktrees
        (.git file).  Returns None if no .git is found, which disables
        path normalization and falls back to ~/.stele/ for storage.
        """
        if explicit is not None:
            return Path(explicit).resolve()
        cwd = Path.cwd().resolve()
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".git").exists():
                return parent
        return None

    def _normalize_path(self, path: str) -> str:
        """Convert a path to project-relative if within the project root.

        Absolute paths are resolved and made relative to the project root.
        Relative paths are resolved against the project root (not CWD),
        ensuring idempotent normalization — calling this on an already-
        normalized path returns the same result.
        """
        p = Path(path)
        if self._project_root is not None:
            if p.is_absolute():
                try:
                    return str(p.resolve().relative_to(self._project_root))
                except ValueError:
                    pass
            else:
                # Relative path — resolve against project root, not CWD
                resolved = (self._project_root / p).resolve()
                try:
                    return str(resolved.relative_to(self._project_root))
                except ValueError:
                    pass
        return str(p.resolve())

    def _resolve_path(self, normalized: str) -> Path:
        """Convert a normalized path back to absolute for file I/O.

        Relative paths are joined against the project root.
        Absolute paths are returned as-is.
        """
        p = Path(normalized)
        if not p.is_absolute() and self._project_root is not None:
            return self._project_root / p
        return p

    def _init_chunkers(self) -> None:
        """Initialize modality-specific chunkers."""
        from stele.chunkers import (
            ImageChunker,
            PDFChunker,
            AudioChunker,
            VideoChunker,
        )

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

        if HAS_IMAGE_CHUNKER and ImageChunker is not None:
            self.chunkers["image"] = ImageChunker()
        if HAS_PDF_CHUNKER and PDFChunker is not None:
            self.chunkers["pdf"] = PDFChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            )
        if HAS_AUDIO_CHUNKER and AudioChunker is not None:
            self.chunkers["audio"] = AudioChunker()
        if HAS_VIDEO_CHUNKER and VideoChunker is not None:
            self.chunkers["video"] = VideoChunker()

    def _load_or_rebuild_index(self) -> VectorIndex:
        """Load persisted index if fresh, otherwise rebuild from SQLite."""
        current_hash = compute_chunk_ids_hash(self.storage)
        index = load_if_fresh(self.storage.index_dir, current_hash)
        if index is not None:
            return index

        index = VectorIndex()
        for chunk in self.storage.search_chunks():
            try:
                # Prefer agent-supplied signature for search quality
                raw_sig = chunk.get("agent_signature") or chunk["semantic_signature"]
                sig = sig_from_bytes(raw_sig)
                index.add_chunk(chunk["chunk_id"], sig_to_list(sig))
            except Exception:
                continue
        save_index(index, current_hash, self.storage.index_dir)
        return index

    def _save_index(self) -> None:
        """Persist current index to disk."""
        current_hash = compute_chunk_ids_hash(self.storage)
        save_index(self.vector_index, current_hash, self.storage.index_dir)

    def _ensure_bm25(self) -> None:
        """Lazily initialize BM25 index — load from disk or rebuild.

        Uses double-checked locking so concurrent readers don't race
        during initialization.
        """
        if self._bm25_ready:
            return
        with self._bm25_init_lock:
            if self._bm25_ready:
                return
            from stele.bm25 import BM25Index

            # Try loading persisted BM25 index
            current_hash = compute_chunk_ids_hash(self.storage)
            loaded = load_bm25_if_fresh(self.storage.index_dir, current_hash)
            if loaded is not None:
                self.bm25_index = loaded
                self._bm25_ready = True
                return

            # Rebuild from SQLite
            self.bm25_index = BM25Index()
            for chunk in self.storage.search_chunks():
                content = chunk.get("content")
                if content:
                    self.bm25_index.add_document(chunk["chunk_id"], content)
            self._bm25_ready = True
            save_bm25(self.bm25_index, current_hash, self.storage.index_dir)

    def _save_bm25(self) -> None:
        """Persist BM25 index alongside HNSW."""
        if self._bm25_ready and self.bm25_index is not None:
            current_hash = compute_chunk_ids_hash(self.storage)
            save_bm25(self.bm25_index, current_hash, self.storage.index_dir)

    def _compute_search_alpha(self, query: str) -> float:
        """Auto-tune blend weight based on query characteristics.

        Code-like queries (identifiers, brackets, keywords) get lower
        alpha to weight keyword matching more heavily.
        """
        signals = sum(
            [
                "_" in query,
                bool(re.search(r"[A-Z][a-z]+[A-Z]", query)),
                any(c in query for c in "{}[]();"),
                bool(
                    re.search(
                        r"\b(def|class|function|import|const|let|var|fn|pub)\b", query
                    )
                ),
                "." in query and not query.endswith("."),
            ]
        )
        if signals >= 3:
            return max(0.3, self.search_alpha - 0.3)
        if signals >= 1:
            return max(0.4, self.search_alpha - 0.15)
        return self.search_alpha

    _QUERY_STOP_WORDS = frozenset(
        {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "have",
            "are",
            "was",
            "not",
            "all",
            "but",
            "how",
        }
    )

    @staticmethod
    def _extract_query_identifiers(query: str) -> List[str]:
        """Extract identifier-like tokens from a search query.

        Splits on whitespace, underscores, and camelCase boundaries.
        Returns unique tokens >= 3 chars, suitable for symbol name matching.
        """
        parts = re.findall(
            r"[A-Z]?[a-z]{2,}|[A-Z]{2,}(?=[A-Z][a-z]|\b)|[a-z_]\w{2,}", query
        )
        full = re.findall(r"[a-zA-Z_]\w{2,}", query)
        tokens = set(parts + full)
        return [t for t in tokens if t.lower() not in Stele._QUERY_STOP_WORDS]

    def _persist_chunks(self, chunks: List[Chunk], doc_path: str) -> None:
        """Store chunks and add them to the vector and keyword indexes."""
        for chunk in chunks:
            chunk_content = chunk.content if isinstance(chunk.content, str) else None
            self.storage.store_chunk(
                chunk_id=chunk.chunk_id,
                document_path=doc_path,
                content_hash=chunk.content_hash,
                semantic_signature=chunk.semantic_signature,
                start_pos=chunk.start_pos,
                end_pos=chunk.end_pos,
                token_count=chunk.token_count,
                content=chunk_content,
            )
            self.vector_index.add_chunk(
                chunk.chunk_id,
                sig_to_list(chunk.semantic_signature),
            )
            if self._bm25_ready and self.bm25_index is not None and chunk_content:
                self.bm25_index.add_document(chunk.chunk_id, chunk_content)

    def _remove_stale_chunks(self, old_ids: set, new_ids: set) -> None:
        """Remove chunks that no longer exist after re-indexing."""
        stale_ids = old_ids - new_ids
        if stale_ids:
            for cid in stale_ids:
                self.vector_index.remove_chunk(cid)
                if self._bm25_ready and self.bm25_index is not None:
                    self.bm25_index.remove_document(cid)
            self.storage.delete_chunks(list(stale_ids))

    def _check_document_ownership(
        self,
        document_path: str,
        agent_id: Optional[str],
    ) -> None:
        """Raise PermissionError if document is locked by another agent.

        Called from within write-locked methods.  If ``agent_id`` is
        ``None``, ownership checking is skipped (backward compat).
        Routes through coordination (shared locks) when available,
        otherwise falls back to local per-worktree locks.
        """
        if agent_id is None:
            return
        status = self._do_get_lock_status(document_path)
        if status.get("locked") and status["locked_by"] != agent_id:
            self._do_record_conflict(
                document_path=document_path,
                agent_a=status["locked_by"],
                agent_b=agent_id,
                conflict_type="ownership_violation",
            )
            worktree_info = ""
            if status.get("worktree"):
                worktree_info = f" (worktree: {status['worktree']})"
            raise PermissionError(
                f"Document '{document_path}' is locked by agent "
                f"'{status['locked_by']}'{worktree_info}"
            )

    def detect_modality(self, file_path: str) -> str:
        """Detect file modality.

        Note: No lock needed — reads only immutable chunker config.
        Kept lockless intentionally since it's called from within
        locked methods too.
        """
        ext = Path(file_path).suffix.lower()
        code_extensions = self.chunkers["code"].supported_extensions()
        if ext in code_extensions:
            return "code"
        for modality, chunker in self.chunkers.items():
            if modality != "text" and ext in chunker.supported_extensions():
                return modality
        if ext in self.chunkers["text"].supported_extensions():
            return "text"
        return "unknown"

    def _expand_paths(self, paths: List[str]) -> List[str]:
        """Expand directories and globs into individual file paths.

        Directories are walked recursively; only files with supported
        extensions are included.  Hidden directories (starting with '.')
        and directories in ``self.skip_dirs`` are skipped.

        All returned paths are normalized via ``_normalize_path`` so that
        they are project-relative when a project root is set.
        """
        supported: set = set()
        for chunker in self.chunkers.values():
            supported.update(chunker.supported_extensions())

        expanded: List[str] = []
        for path_str in paths:
            p = Path(path_str)
            if p.is_file():
                expanded.append(self._normalize_path(str(p)))
            elif p.is_dir():
                for child in sorted(p.rglob("*")):
                    if any(part in self.skip_dirs for part in child.parts):
                        continue
                    if any(part.startswith(".") for part in child.relative_to(p).parts):
                        continue
                    if child.is_file() and child.suffix.lower() in supported:
                        expanded.append(self._normalize_path(str(child)))
            else:
                expanded.append(self._normalize_path(path_str))
        return expanded

    def _chunk_and_store(
        self,
        abs_path: Path,
        doc_path: str,
        content: Any,
        content_hash: str,
        modality: str,
    ) -> list:
        """Chunk a single file, persist chunks, extract symbols, return Chunk list.

        Args:
            abs_path: Absolute filesystem path (for stat/mtime).
            doc_path: Normalized path used as the storage key.
        """
        existing_doc = self.storage.get_document(doc_path)

        # Build signature cache from old chunks (skip recomputation)
        old_chunks_meta = []
        if existing_doc:
            old_chunks_meta = self.storage.get_document_chunks(doc_path)
        sig_cache = {
            c["content_hash"]: c["semantic_signature"] for c in old_chunks_meta
        }

        # Route through appropriate chunker
        chunker = self.chunkers.get(modality, self.chunkers["text"])
        chunks = chunker.chunk(content, doc_path)

        # Inject cached signatures for unchanged chunks
        for chunk in chunks:
            cached_sig = sig_cache.get(chunk.content_hash)
            if cached_sig is not None:
                chunk._semantic_signature = sig_from_bytes(cached_sig)

        chunks = self._merge_similar_chunks(chunks)

        # Clean up stale chunks from previous indexing
        if old_chunks_meta:
            old_ids = {c["chunk_id"] for c in old_chunks_meta}
            new_ids = {c.chunk_id for c in chunks}
            self._remove_stale_chunks(old_ids, new_ids)

        self._persist_chunks(chunks, doc_path)
        self.symbol_manager.extract_document_symbols(doc_path, chunks)

        self.storage.store_document(
            document_path=doc_path,
            content_hash=content_hash,
            chunk_count=len(chunks),
            last_modified=abs_path.stat().st_mtime,
        )
        return chunks

    def index_documents(
        self,
        paths: List[str],
        force_reindex: bool = False,
        agent_id: Optional[str] = None,
        expected_versions: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Index documents through modality-specific chunkers.

        Accepts file paths AND directory paths.  Directories are walked
        recursively; only files with supported extensions are indexed.
        Hidden dirs and common non-source dirs (.git, node_modules, etc.)
        are automatically skipped.

        Args:
            agent_id: If set, ownership is checked per document.
            expected_versions: If set, maps path -> expected doc_version
                for optimistic locking.  Mismatches are rejected.
        """
        with self._lock.write_lock():
            return self._index_documents_unlocked(
                paths, force_reindex, agent_id, expected_versions
            )

    def _index_documents_unlocked(
        self,
        paths: List[str],
        force_reindex: bool = False,
        agent_id: Optional[str] = None,
        expected_versions: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        paths = self._expand_paths(paths)

        if expected_versions:
            expected_versions = {
                self._normalize_path(k): v for k, v in expected_versions.items()
            }

        results: Dict[str, Any] = {
            "indexed": [],
            "skipped": [],
            "errors": [],
            "conflicts": [],
            "total_chunks": 0,
            "total_tokens": 0,
        }

        for norm_path in paths:
            abs_path = self._resolve_path(norm_path)

            if not abs_path.exists():
                results["errors"].append({"path": norm_path, "error": "File not found"})
                continue
            if not abs_path.is_file():
                results["errors"].append({"path": norm_path, "error": "Not a file"})
                continue

            try:
                # Ownership check (raises if locked by another agent)
                self._check_document_ownership(norm_path, agent_id)

                # Auto-acquire lock when agent_id is set and doc exists unlocked
                existing_doc = self.storage.get_document(norm_path)
                if agent_id and existing_doc:
                    status = self._do_get_lock_status(norm_path)
                    if not status.get("locked"):
                        self._do_acquire_lock(norm_path, agent_id)

                # Optimistic version check
                if expected_versions and norm_path in expected_versions:
                    ver_result = self.storage.check_and_increment_doc_version(
                        norm_path, expected_versions[norm_path]
                    )
                    if not ver_result.get("success"):
                        if agent_id:
                            self.storage.record_conflict(
                                document_path=norm_path,
                                agent_a="unknown",
                                agent_b=agent_id,
                                conflict_type="version_conflict",
                                expected_version=ver_result.get("expected"),
                                actual_version=ver_result.get("actual"),
                            )
                        results["conflicts"].append(
                            {
                                "path": norm_path,
                                "reason": "version_conflict",
                                **ver_result,
                            }
                        )
                        continue

                modality = self.detect_modality(str(abs_path))
                content: Any
                content, content_hash = _read_and_hash(abs_path, modality)

                if existing_doc and not force_reindex:
                    if existing_doc["content_hash"] == content_hash:
                        results["skipped"].append(
                            {
                                "path": norm_path,
                                "reason": "Unchanged",
                                "chunk_count": existing_doc["chunk_count"],
                            }
                        )
                        continue

                chunks = self._chunk_and_store(
                    abs_path,
                    norm_path,
                    content,
                    content_hash,
                    modality,
                )

                # Auto-acquire lock on newly-created documents
                if agent_id and not existing_doc:
                    self._do_acquire_lock(norm_path, agent_id)

                # Increment version (if not already done by optimistic check)
                if not (expected_versions and norm_path in expected_versions):
                    self.storage.increment_doc_version(norm_path)

                total_tokens = sum(c.token_count for c in chunks)
                results["indexed"].append(
                    {
                        "path": norm_path,
                        "chunk_count": len(chunks),
                        "total_tokens": total_tokens,
                        "modality": modality,
                    }
                )
                results["total_chunks"] += len(chunks)
                results["total_tokens"] += total_tokens

            except PermissionError as e:
                results["conflicts"].append({"path": norm_path, "error": str(e)})
            except Exception as e:
                results["errors"].append({"path": norm_path, "error": str(e)})

        if results["indexed"]:
            self._save_index()
            self._save_bm25()
            affected = set()
            for doc_info in results["indexed"]:
                for c in self.storage.get_document_chunks(doc_info["path"]):
                    affected.add(c["chunk_id"])
            self.symbol_manager.rebuild_edges(affected_chunk_ids=affected or None)

        # Notify other agents about changes
        if self._coordination and results["indexed"]:
            self._coordination.notify_changes_batch(
                [(d["path"], "indexed") for d in results["indexed"]],
                agent_id or "",
            )

        return results

    def remove_document(
        self, document_path: str, agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Remove a document and all its chunks, annotations, and index entries."""
        with self._lock.write_lock():
            document_path = self._normalize_path(document_path)
            self._check_document_ownership(document_path, agent_id)
            result = self.storage.remove_document(document_path)
            if result.get("removed"):
                for chunk_id in result.get("chunk_ids", []):
                    self.vector_index.remove_chunk(chunk_id)
                    if self._bm25_ready and self.bm25_index is not None:
                        self.bm25_index.remove_document(chunk_id)
                self._save_index()
                self._save_bm25()
                if self._coordination:
                    self._coordination.notify_change(
                        document_path,
                        "removed",
                        agent_id or "",
                    )
            return result

    def _merge_similar_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Merge adjacent chunks with high similarity (single-pass).

        Uses modality-specific merge thresholds: code chunks require
        higher similarity to merge (preserving AST boundaries), while
        prose chunks merge more aggressively.
        """
        if len(chunks) <= 1:
            return chunks

        modality = chunks[0].modality if chunks else "text"
        threshold = self.MODALITY_THRESHOLDS.get(modality, {}).get(
            "merge", self.merge_threshold
        )

        # Keywords that signal a new definition boundary in code
        _DEF_STARTS = (
            "def ",
            "class ",
            "function ",
            "func ",
            "fn ",
            "pub fn ",
            "async def ",
            "async function ",
            "export function ",
            "export class ",
            "export default ",
        )

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            current = merged[-1]

            # Never merge across function/class boundaries in code
            if modality == "code" and isinstance(chunk.content, str):
                leading = chunk.content.lstrip()
                if any(leading.startswith(kw) for kw in _DEF_STARTS):
                    merged.append(chunk)
                    continue

            similarity = current.similarity(chunk)
            combined_tokens = current.token_count + chunk.token_count

            if (
                similarity >= threshold
                and combined_tokens <= self.max_chunk_size
                and isinstance(current.content, str)
                and isinstance(chunk.content, str)
            ):
                merged_content = current.content + "\n\n" + chunk.content
                merged[-1] = Chunk(
                    content=merged_content,
                    modality=current.modality,
                    start_pos=current.start_pos,
                    end_pos=chunk.end_pos,
                    document_path=current.document_path,
                    chunk_index=current.chunk_index,
                    metadata={**current.metadata, **chunk.metadata},
                )
            else:
                merged.append(chunk)

        return merged

    def annotate(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add an annotation to a document or chunk."""
        with self._lock.write_lock():
            if target_type == "document":
                target = self._normalize_path(target)
            if target_type not in ("document", "chunk"):
                return {"error": "target_type must be 'document' or 'chunk'"}

            if target_type == "document":
                doc = self.storage.get_document(target)
                if doc is None:
                    return {"error": f"Document not found: {target}"}
            else:
                chunk = self.storage.get_chunk(target)
                if chunk is None:
                    return {"error": f"Chunk not found: {target}"}

            annotation_id = self.storage.store_annotation(
                target, target_type, content, tags
            )
            return {"id": annotation_id, "target": target, "target_type": target_type}

    def get_annotations(
        self,
        target: Optional[str] = None,
        target_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve filtered annotations."""
        with self._lock.read_lock():
            if target is not None and target_type == "document":
                target = self._normalize_path(target)
            return self.storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> Dict[str, Any]:
        """Delete an annotation by ID."""
        with self._lock.write_lock():
            deleted = self.storage.delete_annotation(annotation_id)
            return {"deleted": deleted, "id": annotation_id}

    def update_annotation(
        self,
        annotation_id: int,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an annotation by ID."""
        with self._lock.write_lock():
            updated = self.storage.update_annotation(annotation_id, content, tags)
            return {"updated": updated, "id": annotation_id}

    def search_annotations(
        self, query: str, target_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search annotation content text."""
        with self._lock.read_lock():
            return self.storage.search_annotations(query, target_type)

    def bulk_annotate(self, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Annotate multiple targets in one call.

        Each entry: {target, target_type, content, tags?}
        """
        with self._lock.write_lock():
            results = []
            errors = []
            for entry in annotations:
                result = self._annotate_unlocked(
                    target=entry["target"],
                    target_type=entry["target_type"],
                    content=entry["content"],
                    tags=entry.get("tags"),
                )
                if "error" in result:
                    errors.append({**entry, "error": result["error"]})
                else:
                    results.append(result)
            return {"created": results, "errors": errors}

    def _annotate_unlocked(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Internal annotate without lock (for use inside write_lock)."""
        if target_type == "document":
            target = self._normalize_path(target)
        if target_type not in ("document", "chunk"):
            return {"error": "target_type must be 'document' or 'chunk'"}
        if target_type == "document":
            doc = self.storage.get_document(target)
            if doc is None:
                return {"error": f"Document not found: {target}"}
        else:
            chunk = self.storage.get_chunk(target)
            if chunk is None:
                return {"error": f"Chunk not found: {target}"}
        annotation_id = self.storage.store_annotation(
            target, target_type, content, tags
        )
        return {"id": annotation_id, "target": target, "target_type": target_type}

    def prune_history(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Prune old history entries."""
        with self._lock.write_lock():
            deleted = self.storage.prune_history(max_age_seconds, max_entries)
            return {"pruned": deleted}

    def get_map(self) -> Dict[str, Any]:
        """Get a project overview: all documents with chunk counts and annotations."""
        with self._lock.read_lock():
            return self._get_map_unlocked()

    def _get_map_unlocked(self) -> Dict[str, Any]:
        documents = self.storage.get_all_documents()
        result = []
        total_tokens = 0

        for doc in documents:
            chunks = self.storage.search_chunks(document_path=doc["document_path"])
            doc_tokens = sum(c["token_count"] for c in chunks)
            total_tokens += doc_tokens

            annotations = self.storage.get_annotations(
                target=doc["document_path"], target_type="document"
            )

            result.append(
                {
                    "path": doc["document_path"],
                    "chunk_count": doc["chunk_count"],
                    "total_tokens": doc_tokens,
                    "indexed_at": doc["indexed_at"],
                    "annotations": [
                        {"id": a["id"], "content": a["content"], "tags": a["tags"]}
                        for a in annotations
                    ],
                }
            )

        return {
            "documents": result,
            "total_documents": len(result),
            "total_tokens": total_tokens,
        }

    def get_history(
        self,
        limit: int = 20,
        document_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get change history entries."""
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            return self.storage.get_change_history(limit, document_path)

    def get_chunk_history(
        self,
        chunk_id: Optional[str] = None,
        document_path: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get chunk version history.

        Args:
            chunk_id: Filter by specific chunk ID
            document_path: Filter by document path
            limit: Max entries to return
        """
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            return self.storage.get_chunk_history(chunk_id, document_path, limit)

    def store_semantic_summary(
        self,
        chunk_id: str,
        summary: str,
    ) -> Dict[str, Any]:
        """Store an agent-supplied semantic summary for a chunk.

        The agent provides a natural language description of what the chunk
        does.  Stele computes a 128-dim signature from the summary and
        updates the HNSW index with it, improving search quality.

        The original statistical signature is preserved for change detection.

        Args:
            chunk_id: Chunk to annotate
            summary: Agent's semantic description (e.g. "JWT middleware
                that validates tokens and attaches user to request context")

        Returns:
            Dict with status and chunk_id.
        """
        with self._lock.write_lock():
            # Compute signature from the summary text
            summary_chunk = Chunk(
                content=summary,
                modality="text",
                start_pos=0,
                end_pos=len(summary),
                document_path="<summary>",
            )
            agent_sig = sig_to_list(summary_chunk.semantic_signature)

            ok = self.storage.store_semantic_summary(
                chunk_id,
                summary,
                agent_sig,
            )
            if not ok:
                return {"stored": False, "error": "chunk not found"}

            # Update HNSW index with the agent-derived signature
            self.vector_index.remove_chunk(chunk_id)
            self.vector_index.add_chunk(chunk_id, agent_sig)
            self._save_index()

            return {"stored": True, "chunk_id": chunk_id}

    def store_embedding(
        self,
        chunk_id: str,
        vector: List[float],
    ) -> Dict[str, Any]:
        """Store a raw embedding vector for a chunk.

        For agents that have direct access to embedding APIs.
        The vector replaces the statistical signature in the HNSW
        index for better search quality.

        Args:
            chunk_id: Chunk to update
            vector: Embedding vector (will be normalized to unit length)

        Returns:
            Dict with status and chunk_id.
        """
        with self._lock.write_lock():
            # Normalize to unit vector
            norm = sum(x * x for x in vector) ** 0.5
            if norm > 0:
                vector = [x / norm for x in vector]

            ok = self.storage.store_agent_signature(chunk_id, vector)
            if not ok:
                return {"stored": False, "error": "chunk not found"}

            # Update HNSW index
            self.vector_index.remove_chunk(chunk_id)
            self.vector_index.add_chunk(chunk_id, vector)
            self._save_index()

            return {"stored": True, "chunk_id": chunk_id}

    def _classify_chunks_for_change(
        self,
        new_chunks: list,
        old_chunks_meta: list,
        modality: str,
        doc_path: str,
        results: Dict[str, Any],
    ) -> None:
        """Compare new chunks against old metadata; update results counters."""
        change_thresh = self.MODALITY_THRESHOLDS.get(modality, {}).get(
            "change", self.change_threshold
        )

        old_by_pos: Dict = {}
        for meta in old_chunks_meta:
            old_by_pos[(meta["start_pos"], meta["end_pos"])] = meta

        for new_chunk in new_chunks:
            old_meta = old_by_pos.get((new_chunk.start_pos, new_chunk.end_pos))

            if old_meta is None:
                search_results = self.vector_index.search(
                    sig_to_list(new_chunk.semantic_signature), k=1
                )
                if search_results and search_results[0][1] >= change_thresh:
                    results["kv_restored"] += 1
                else:
                    results["new"].append(
                        {
                            "path": doc_path,
                            "chunk_id": new_chunk.chunk_id,
                            "reason": "New chunk",
                        }
                    )
                    results["kv_reprocessed"] += 1
            elif new_chunk.content_hash == old_meta["content_hash"]:
                results["kv_restored"] += 1
            else:
                old_sig = sig_from_bytes(old_meta["semantic_signature"])
                similarity = cosine_similarity(old_sig, new_chunk.semantic_signature)
                if similarity >= change_thresh:
                    results["kv_restored"] += 1
                else:
                    results["kv_reprocessed"] += 1

    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: Optional[List[str]] = None,
        reason: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Detect changes in documents and update accordingly."""
        with self._lock.write_lock():
            return self._detect_changes_unlocked(
                session_id, document_paths, reason, agent_id
            )

    def _detect_changes_unlocked(
        self,
        session_id: str,
        document_paths: Optional[List[str]] = None,
        reason: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.storage.create_session(session_id, agent_id=agent_id)

        results: Dict[str, Any] = {
            "unchanged": [],
            "modified": [],
            "new": [],
            "removed": [],
            "conflicts": [],
            "kv_restored": 0,
            "kv_reprocessed": 0,
        }

        if document_paths is None:
            all_chunks = self.storage.search_chunks()
            document_paths = list({c["document_path"] for c in all_chunks})
        else:
            document_paths = [self._normalize_path(p) for p in document_paths]

        session = self.storage.get_session(session_id)

        for doc_path in document_paths:
            abs_path = self._resolve_path(doc_path)

            if not abs_path.exists():
                results["removed"].append(doc_path)
                # Inline removal to avoid re-acquiring write lock
                rm_result = self.storage.remove_document(doc_path)
                if rm_result.get("removed"):
                    for cid in rm_result.get("chunk_ids", []):
                        self.vector_index.remove_chunk(cid)
                        if self._bm25_ready and self.bm25_index is not None:
                            self.bm25_index.remove_document(cid)
                continue

            try:
                self._check_document_ownership(doc_path, agent_id)
            except PermissionError as e:
                results["conflicts"].append({"path": doc_path, "error": str(e)})
                continue

            try:
                modality = self.detect_modality(str(abs_path))
                content: Any
                content, content_hash = _read_and_hash(abs_path, modality)
            except Exception:
                results["modified"].append({"path": doc_path, "reason": "Read error"})
                continue

            stored_doc = self.storage.get_document(doc_path)
            if stored_doc is None:
                results["new"].append({"path": doc_path, "reason": "Not indexed"})
                continue

            if stored_doc["content_hash"] == content_hash:
                results["unchanged"].append(doc_path)
                chunks = self.storage.get_document_chunks(doc_path)
                if session and session["turn_count"] > 0:
                    for chunk_meta in chunks:
                        kv_data = self.storage.load_kv_state(
                            session_id,
                            chunk_meta["chunk_id"],
                            session["turn_count"] - 1,
                        )
                        if kv_data is not None:
                            results["kv_restored"] += 1
            else:
                results["modified"].append(
                    {
                        "path": doc_path,
                        "old_hash": stored_doc["content_hash"][:16],
                        "new_hash": content_hash[:16],
                    }
                )
                old_chunks_meta = self.storage.get_document_chunks(doc_path)

                # Re-chunk, inject cached sigs, merge, classify
                chunker = self.chunkers.get(modality, self.chunkers["text"])
                sig_cache = {
                    c["content_hash"]: c["semantic_signature"] for c in old_chunks_meta
                }
                new_chunks = chunker.chunk(content, doc_path)
                for nc in new_chunks:
                    cached = sig_cache.get(nc.content_hash)
                    if cached is not None:
                        nc._semantic_signature = sig_from_bytes(cached)
                new_chunks = self._merge_similar_chunks(new_chunks)

                self._classify_chunks_for_change(
                    new_chunks, old_chunks_meta, modality, doc_path, results
                )

                # Persist updated chunks and clean up stale ones
                self._persist_chunks(new_chunks, doc_path)
                self.symbol_manager.extract_document_symbols(doc_path, new_chunks)
                old_chunk_ids = {m["chunk_id"] for m in old_chunks_meta}
                new_chunk_ids = {c.chunk_id for c in new_chunks}
                self._remove_stale_chunks(old_chunk_ids, new_chunk_ids)

                self.storage.store_document(
                    document_path=doc_path,
                    content_hash=content_hash,
                    chunk_count=len(new_chunks),
                    last_modified=abs_path.stat().st_mtime,
                )
                self.storage.increment_doc_version(doc_path)

        if results["modified"]:
            self._save_index()
            self._save_bm25()
            modified_chunk_ids: set = set()
            for doc_info in results["modified"]:
                for c in self.storage.get_document_chunks(doc_info["path"]):
                    modified_chunk_ids.add(c["chunk_id"])
            self.symbol_manager.rebuild_edges(
                affected_chunk_ids=modified_chunk_ids or None
            )
            if modified_chunk_ids:
                self.symbol_manager.propagate_staleness(modified_chunk_ids)

        self.storage.record_change(
            summary=results, session_id=session_id, reason=reason
        )

        # Notify other agents about changes
        if self._coordination:
            changes = []
            for d in results.get("modified", []):
                if isinstance(d, dict):
                    changes.append((d["path"], "modified"))
            for path in results.get("removed", []):
                changes.append((path, "removed"))
            if changes:
                self._coordination.notify_changes_batch(
                    changes,
                    agent_id or "",
                )

        return results

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Hybrid semantic + keyword search across all indexed chunks.

        Combines HNSW vector similarity with BM25 keyword scoring.
        The blend is controlled by search_alpha (1.0 = pure vector,
        0.0 = pure keyword).
        """
        with self._lock.read_lock():
            return self._search_unlocked(query, top_k)

    def _search_unlocked(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        query_chunk = Chunk(
            content=query,
            modality="text",
            start_pos=0,
            end_pos=len(query),
            document_path="<query>",
        )

        query_sig = sig_to_list(query_chunk.semantic_signature)

        # Widen HNSW candidate set for re-ranking
        hnsw_results = self.vector_index.search(query_sig, k=top_k * 3)

        if not hnsw_results:
            return []

        # BM25 re-ranking
        self._ensure_bm25()
        candidate_ids = [cid for cid, _ in hnsw_results]
        hnsw_scores = {cid: score for cid, score in hnsw_results}
        assert self.bm25_index is not None
        bm25_scores = self.bm25_index.score_batch(query, candidate_ids)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 0.0
        if max_bm25 > 0:
            bm25_norm = {k: v / max_bm25 for k, v in bm25_scores.items()}
        else:
            bm25_norm = bm25_scores

        # Blend: alpha * vector + (1 - alpha) * keyword
        alpha = self._compute_search_alpha(query)
        combined = {}
        for cid in candidate_ids:
            vec_score = hnsw_scores.get(cid, 0.0)
            kw_score = bm25_norm.get(cid, 0.0)
            combined[cid] = alpha * vec_score + (1.0 - alpha) * kw_score

        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for chunk_id, score in ranked:
            chunk_meta = self.storage.get_chunk(chunk_id)
            if chunk_meta is None:
                continue

            content = self.storage.get_chunk_content(chunk_id)

            entry: Dict[str, Any] = {
                "chunk_id": chunk_id,
                "content": content,
                "document_path": chunk_meta["document_path"],
                "relevance_score": float(score),
                "token_count": chunk_meta["token_count"],
                "start_pos": chunk_meta["start_pos"],
                "end_pos": chunk_meta["end_pos"],
            }

            self.symbol_manager.attach_edges(entry, chunk_id)
            results.append(entry)

        # Symbol-boosted search: find chunks defining symbols that match
        # query identifiers but weren't found by HNSW+BM25
        existing_ids = {r["chunk_id"] for r in results}
        query_idents = self._extract_query_identifiers(query)
        if query_idents:
            sym_matches = self.storage.search_symbol_names(query_idents)
            min_score = results[-1]["relevance_score"] if results else 0.1
            for sym in sym_matches:
                cid = sym["chunk_id"]
                if cid in existing_ids:
                    continue
                chunk_meta = self.storage.get_chunk(cid)
                if chunk_meta is None:
                    continue
                content = self.storage.get_chunk_content(cid)
                entry = {
                    "chunk_id": cid,
                    "content": content,
                    "document_path": sym["document_path"],
                    "relevance_score": round(min_score * 0.85, 4),
                    "token_count": chunk_meta["token_count"],
                    "start_pos": chunk_meta["start_pos"],
                    "end_pos": chunk_meta["end_pos"],
                    "symbol_match": sym["name"],
                }
                self.symbol_manager.attach_edges(entry, cid)
                results.append(entry)
                existing_ids.add(cid)

            # Re-sort and truncate
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            results = results[:top_k]

        return results

    def get_context(
        self,
        document_paths: List[str],
    ) -> Dict[str, Any]:
        """Get cached context for documents."""
        with self._lock.read_lock():
            return self._get_context_unlocked(document_paths)

    def _get_context_unlocked(
        self,
        document_paths: List[str],
    ) -> Dict[str, Any]:
        document_paths = [self._normalize_path(p) for p in document_paths]

        result: Dict[str, Any] = {
            "unchanged": [],
            "changed": [],
            "new": [],
        }

        for doc_path in document_paths:
            abs_path = self._resolve_path(doc_path)

            if not abs_path.exists():
                continue

            stored_doc = self.storage.get_document(doc_path)
            if stored_doc is None:
                result["new"].append({"path": doc_path})
                continue

            try:
                modality = self.detect_modality(str(abs_path))
                _, content_hash = _read_and_hash(abs_path, modality)
            except Exception:
                result["changed"].append({"path": doc_path, "reason": "Read error"})
                continue

            if stored_doc["content_hash"] == content_hash:
                chunks = self.storage.search_chunks(document_path=doc_path)
                chunk_data = []
                for chunk in chunks:
                    chunk_data.append(
                        {
                            "chunk_id": chunk["chunk_id"],
                            "content": chunk.get("content"),
                            "start_pos": chunk["start_pos"],
                            "end_pos": chunk["end_pos"],
                            "token_count": chunk["token_count"],
                        }
                    )
                result["unchanged"].append(
                    {
                        "path": doc_path,
                        "chunks": chunk_data,
                        "total_tokens": sum(c["token_count"] for c in chunk_data),
                    }
                )
            else:
                result["changed"].append(
                    {
                        "path": doc_path,
                        "old_hash": stored_doc["content_hash"][:16],
                        "new_hash": content_hash[:16],
                    }
                )

        return result

    def get_relevant_kv(
        self,
        session_id: str,
        query: str,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """Get cached state for chunks most relevant to a query."""
        with self._lock.read_lock():
            return self.session_manager.get_relevant_chunks(session_id, query, top_k)

    def save_kv_state(
        self,
        session_id: str,
        kv_data: Dict[str, Any],
        chunk_ids: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save KV-cache state for a session."""
        with self._lock.write_lock():
            if agent_id is not None:
                self.storage.create_session(session_id, agent_id=agent_id)
            return self.session_manager.save_state(session_id, kv_data, chunk_ids)

    save_state = save_kv_state

    def rollback(self, session_id: str, target_turn: int) -> Dict[str, Any]:
        """Rollback session to a previous turn."""
        with self._lock.write_lock():
            return self.session_manager.rollback(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> Dict[str, Any]:
        """Prune low-relevance chunks to stay under token limit."""
        with self._lock.write_lock():
            return self.session_manager.prune(session_id, max_tokens)

    # -- Symbol graph (delegated to SymbolGraphManager) ----------------------

    def stale_chunks(self, threshold: float = 0.3) -> Dict[str, Any]:
        """Get chunks whose dependencies have changed since last indexing."""
        with self._lock.read_lock():
            return self.symbol_manager.stale_chunks(threshold)

    def find_references(self, symbol: str) -> Dict[str, Any]:
        """Find all references to a symbol across the codebase."""
        with self._lock.read_lock():
            return self.symbol_manager.find_references(symbol)

    def find_definition(self, symbol: str) -> Dict[str, Any]:
        """Find the definition(s) of a symbol."""
        with self._lock.read_lock():
            return self.symbol_manager.find_definition(symbol)

    def impact_radius(self, chunk_id: str, depth: int = 2) -> Dict[str, Any]:
        """Find all chunks potentially affected by a change to this chunk."""
        with self._lock.read_lock():
            return self.symbol_manager.impact_radius(chunk_id, depth)

    def rebuild_symbol_graph(self) -> Dict[str, Any]:
        """Rebuild the entire symbol graph from stored chunk content."""
        with self._lock.write_lock():
            return self.symbol_manager.rebuild_graph()

    def get_stats(self) -> Dict[str, Any]:
        """Get Stele statistics."""
        with self._lock.read_lock():
            storage_stats = self.storage.get_storage_stats()
            index_stats = self.vector_index.get_stats()

            return {
                "version": _get_version(),
                "storage": storage_stats,
                "index": index_stats,
                "config": {
                    "chunk_size": self.chunk_size,
                    "max_chunk_size": self.max_chunk_size,
                    "merge_threshold": self.merge_threshold,
                    "change_threshold": self.change_threshold,
                    "search_alpha": self.search_alpha,
                },
            }

    def list_sessions(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by agent_id."""
        with self._lock.read_lock():
            return self.storage.list_sessions(agent_id=agent_id)

    # -- Document ownership & conflict prevention -----------------------------

    def acquire_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Acquire exclusive write ownership of a document."""
        with self._lock.write_lock():
            document_path = self._normalize_path(document_path)
            return self._do_acquire_lock(document_path, agent_id, ttl, force)

    def refresh_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Refresh lock TTL without releasing."""
        with self._lock.write_lock():
            document_path = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.refresh_lock(
                    document_path,
                    agent_id,
                    ttl,
                )
            return self.storage.refresh_document_lock(
                document_path,
                agent_id,
                ttl,
            )

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> Dict[str, Any]:
        """Release ownership of a document."""
        with self._lock.write_lock():
            document_path = self._normalize_path(document_path)
            return self._do_release_lock(document_path, agent_id)

    def get_document_lock_status(self, document_path: str) -> Dict[str, Any]:
        """Check lock status of a document."""
        with self._lock.read_lock():
            document_path = self._normalize_path(document_path)
            return self._do_get_lock_status(document_path)

    def release_agent_locks(self, agent_id: str) -> Dict[str, Any]:
        """Release all locks held by an agent."""
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.release_agent_locks(agent_id)
            return self.storage.release_agent_locks(agent_id)

    def reap_expired_locks(self) -> Dict[str, Any]:
        """Clear all expired document locks."""
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.reap_expired_locks()
            return self.storage.reap_expired_locks()

    def get_conflicts(
        self,
        document_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get conflict history."""
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.get_conflicts(
                    document_path,
                    agent_id,
                    limit,
                )
            return self.storage.get_conflicts(document_path, agent_id, limit)

    # -- Agent coordination ---------------------------------------------------

    def register_agent(self, agent_id: str) -> Dict[str, Any]:
        """Register an agent with the cross-worktree coordination DB."""
        if not self._coordination:
            return {"registered": False, "reason": "no_coordination"}
        root = str(self._project_root) if self._project_root else ""
        return self._coordination.register_agent(agent_id, root)

    def deregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """Deregister an agent and release all its shared locks."""
        if not self._coordination:
            return {"deregistered": False, "reason": "no_coordination"}
        return self._coordination.deregister_agent(agent_id)

    def heartbeat(self, agent_id: str) -> Dict[str, Any]:
        """Update heartbeat for a registered agent."""
        if not self._coordination:
            return {"updated": False}
        return self._coordination.heartbeat(agent_id)

    def list_agents(
        self,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """List agents registered across all worktrees."""
        if not self._coordination:
            return []
        return self._coordination.list_agents(active_only=active_only)

    def get_notifications(
        self,
        since: Optional[float] = None,
        exclude_self: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get change notifications from other agents.

        Args:
            since: Unix timestamp; only notifications after this time.
            exclude_self: Agent ID to exclude (skip your own changes).
            limit: Max notifications to return.
        """
        if not self._coordination:
            return {"notifications": [], "count": 0, "latest_timestamp": 0.0}
        return self._coordination.get_notifications(
            since=since,
            exclude_agent=exclude_self,
            limit=limit,
        )

    # -- Environment checks ---------------------------------------------------

    def check_environment(self) -> Dict[str, Any]:
        """Run environment checks: stale bytecache + editable installs."""
        from stele.env_checks import scan_stale_pycache, check_editable_installs

        result: Dict[str, Any] = {"issues": []}

        if self._project_root:
            bytecache = scan_stale_pycache(
                self._project_root,
                self.skip_dirs - {"__pycache__"},
            )
            if bytecache["total_stale_files"] > 0:
                result["issues"].append(
                    {
                        "type": "stale_bytecache",
                        **bytecache,
                    }
                )

            editable = check_editable_installs(self._project_root)
            if editable["count"] > 0:
                result["issues"].append(
                    {
                        "type": "editable_install_mismatch",
                        **editable,
                    }
                )

        result["total_issues"] = len(result["issues"])
        return result

    def clean_bytecache(self) -> Dict[str, Any]:
        """Remove stale __pycache__ files from the project."""
        if not self._project_root:
            return {"cleaned": 0}
        from stele.env_checks import clean_stale_pycache

        return clean_stale_pycache(
            self._project_root,
            self.skip_dirs - {"__pycache__"},
        )
