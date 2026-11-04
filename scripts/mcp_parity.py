#!/usr/bin/env python3
"""Full MCP and CLI compare of Rust against Python 1.5.2.

Speaks newline JSON-RPC tools/call to both stdio servers. Rust is
`stele-context serve-mcp`. Python is the installed PyPI 1.5.2 command
`python -m stele_context.cli serve-mcp`, unmodified. The fixture has Python,
JavaScript, Markdown, a one-page PDF with two text lines, a gitignored file,
and a skip_dirs directory. Normalisation is temp paths and CRLF. Timestamp
values may differ by the gap between the two processes; their type stays a
float with a fractional part.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


PDF = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents [4 0 R 5 0 R] >> endobj
4 0 obj << /Length 24 >>
stream
BT (Hello line) Tj ET
endstream
endobj
5 0 obj << /Length 24 >>
stream
BT (Second line) Tj ET
endstream
endobj
trailer << /Root 1 0 R >>
%%EOF
"""

def fixture(root: Path) -> None:
    (root / "src").mkdir()
    (root / "web").mkdir()
    (root / "docs").mkdir()
    (root / "vendor").mkdir()
    (root / "src" / "calc.py").write_text("def classify(n):\n    return n\n")
    (root / "src" / "main.py").write_text("from calc import classify\n\ndef main():\n    return classify(1)\n")
    (root / "web" / "app.js").write_text("function greet(){ return 1 }\n")
    (root / "docs" / "guide.md").write_text("# guide\n\nclassify the input\n")
    media = Path(__file__).resolve().parents[1] / "parity" / "fixtures" / "media"
    note = media / "note.pdf"
    if note.is_file():
        (root / "docs" / "report.pdf").write_bytes(note.read_bytes())
    else:
        (root / "docs" / "report.pdf").write_bytes(PDF)
    for name in ("small3.pdf", "big2.pdf"):
        src = media / name
        if src.is_file():
            (root / "docs" / name).write_bytes(src.read_bytes())
    (root / "secret.py").write_text("def secret():\n    return 1\n")
    (root / "vendor" / "hidden.py").write_text("def hidden():\n    return 1\n")
    (root / ".gitignore").write_text("secret.py\n")
    (root / ".stele-context.toml").write_text(
        "[stele-context]\nchunk_size = 256\nskip_dirs = [\"vendor\"]\nrespect_gitignore = true\n"
    )
    (root / ".git").mkdir()


class Server:
    def __init__(self, argv, env, cwd):
        self.proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self.n = 0

    def call(self, method, params):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        body = None
        for _ in range(8):
            line = read_line(self.proc.stdout, 20, method)
            if not line:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise SystemExit(f"server closed: {err[-500:]}")
            body = json.loads(line)
            notice = str(body.get("method", ""))
            if notice.startswith("notifications/"):
                continue
            break
        if body is None:
            raise SystemExit(f"no reply for {method}")
        if "error" in body:
            return {"ok": False, "error": body["error"]}
        result = body.get("result") or {}
        content = result.get("content") or []
        if content and isinstance(content, list):
            text = content[0].get("text", "")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
        return result

    def notify(self, method):
        msg = {"jsonrpc": "2.0", "method": method}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def read_line(pipe, seconds, label):
    # select() on a pipe needs Winsock on Windows and fails there.
    box = {}

    def grab():
        box["line"] = pipe.readline()

    thread = threading.Thread(target=grab, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise SystemExit(f"timeout waiting for {label}")
    return box.get("line", "")


def tool(server, name, arguments):
    return server.call("tools/call", {"name": name, "arguments": arguments})


def walk(value, root, other_root, key=""):
    if isinstance(value, dict):
        return {k: walk(v, root, other_root, k) for k, v in sorted(value.items())}
    if isinstance(value, list):
        rows = [walk(v, root, other_root, key) for v in value]
        # `unchanged` order follows PYTHONHASHSEED. Other lists keep their order.
        if key == "unchanged":
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        return rows
    if key in {"version", "stele_version"} and isinstance(value, str) and value[:3] in {"1.5", "2.0"}:
        return "version"
    if isinstance(value, str):
        text = value.replace("\r\n", "\n").replace("\\", "/")
        for prefix in (root, other_root):
            if prefix:
                text = text.replace(prefix.replace("\\", "/"), "")
        marker = "stele-mcp-"
        lowered = text.lower()
        if marker in lowered:
            start = lowered.index(marker)
            end = start + len(marker)
            while end < len(text) and text[end] not in "/":
                end += 1
            text = text[end:]
        while "stele-context-mcp-" in text:
            start = text.index("stele-context-mcp-")
            end = start + len("stele-context-mcp-")
            while end < len(text) and text[end].isdigit():
                end += 1
            text = text[:start] + "AGENT" + text[end:]
        return text
    if key == "pid" and isinstance(value, int):
        return 0
    # Signature bytes match 1.5.2. Cosine of the f64 query against the f32
    # vector still moves in the 8th digit. Five decimal places is the compare.
    if key == "relevance_score" and isinstance(value, float):
        return round(value, 5)
    return value


def blank_graph(value):
    """2.0 has no on-disk HNSW file and no SQLite database file."""
    if isinstance(value, dict):
        for key in (
            "max_level",
            "avg_connections",
            "insert_count",
            "node_count",
            "database_size_bytes",
            "page_count",
            "wal_size_bytes",
            "hnsw_span",
        ):
            if key in value:
                value[key] = "store"
        for child in value.values():
            blank_graph(child)
    elif isinstance(value, list):
        for child in value:
            blank_graph(child)


def timestamps_ok(left, right, bad):
    """Floats within the run match. An int against a float is a real diff."""
    if isinstance(left, dict) and isinstance(right, dict):
        keys = set(left) | set(right)
        out_l, out_r = {}, {}
        for key in keys:
            lv, rv = left.get(key), right.get(key)
            if key in {
                "created_at", "updated_at", "last_updated", "locked_at",
                "expires_at", "latest_timestamp", "indexed_at", "at",
                "last_heartbeat", "file_mtime", "document_indexed_at",
                "stored_last_modified", "started_at", "indexed_at",
                "latest_indexed_at", "seconds_since_last_index",
                "read_at", "timestamp",
            } and isinstance(lv, (int, float)) and isinstance(rv, (int, float)):
                if isinstance(lv, bool) or isinstance(rv, bool):
                    out_l[key], out_r[key] = lv, rv
                elif type(lv) is int or type(rv) is int:
                    bad.append(key)
                    out_l[key], out_r[key] = lv, rv
                elif abs(float(lv) - float(rv)) <= 180:
                    out_l[key] = out_r[key] = "time"
                else:
                    out_l[key], out_r[key] = lv, rv
            else:
                a, b = timestamps_ok(lv, rv, bad)
                out_l[key], out_r[key] = a, b
        return out_l, out_r
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        pairs = [timestamps_ok(a, b, bad) for a, b in zip(left, right)]
        return [a for a, _ in pairs], [b for _, b in pairs]
    return left, right


def first_chunk(value):
    found = []

    def visit(item):
        if isinstance(item, dict):
            cid = item.get("chunk_id")
            if isinstance(cid, str) and cid:
                found.append(cid)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found[0] if found else ""


# Fields a JSON snapshot cannot measure. Listed in MIGRATION.md.
SQLITE_FIELDS = {
    "journal_mode",
    "synchronous",
    "page_count",
    "page_size",
    "wal_autocheckpoint",
    "busy_timeout_ms",
    "database_size_bytes",
    "wal_size_bytes",
}


def documented(name, left, right):
    """Narrow adjustments named in MIGRATION.md. Returns (note, skip_compare)."""
    notes = []
    skip = False
    if name == "doctor" and isinstance(left, dict) and isinstance(right, dict):
        for side in (left, right):
            health = side.get("db_health")
            if isinstance(health, dict):
                for key in SQLITE_FIELDS:
                    health.pop(key, None)
        notes.append("sqlite pragma and database file fields")
        for side in (left, right):
            health = side.get("db_health") if isinstance(side, dict) else None
            if isinstance(health, dict):
                for key in (
                    "recent_operations_1h",
                    "recent_failures_1h",
                    "busy_ratio",
                    "recommended_action",
                ):
                    health.pop(key, None)
        notes.append("operation_log is the SQLite telemetry table")
    if name == "stats" and isinstance(left, dict) and isinstance(right, dict):
        storage = left.get("storage") if isinstance(left.get("storage"), dict) else None
        other = right.get("storage") if isinstance(right.get("storage"), dict) else None
        if isinstance(storage, dict) and isinstance(other, dict):
            storage.pop("kv_cache_size_bytes", None)
            other.pop("kv_cache_size_bytes", None)
            notes.append("kv_cache_size_bytes is zlib files on 1.5.2")
    if name == "get_supported_formats" and isinstance(left, dict) and isinstance(right, dict):
        lf = left.get("formats") if isinstance(left.get("formats"), dict) else {}
        rf = right.get("formats") if isinstance(right.get("formats"), dict) else {}
        extra = sorted(key for key, value in lf.items() if key not in rf or rf.get(key) is None)
        for key in list(rf):
            if rf.get(key) is None:
                rf.pop(key, None)
        if extra:
            for key in extra:
                lf.pop(key, None)
            notes.append("rust also lists " + ",".join(extra))
    if name == "get_session_read_files":
        err = right.get("error") if isinstance(right, dict) else None
        if isinstance(err, str) and "staleness_score" in err:
            notes.append("1.5.2 query errors; rust returns the read list")
            skip = True
    if name in {"store_embedding", "bulk_annotate"} and isinstance(right, dict):
        err = str(right.get("error", ""))
        if "unexpected keyword" in err or "missing" in err:
            notes.append("1.5.2 rejects the alias argument; rust accepts it")
            skip = True
    return "; ".join(notes), skip


def first_diff(left, right, path="$"):
    if left == right:
        return None
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            found = first_diff(left.get(key), right.get(key), f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (item, other) in enumerate(zip(left, right)):
            found = first_diff(item, other, f"{path}[{index}]")
            if found:
                return found
        return None
    return path, left, right


def main() -> int:
    if os.environ.get("STELE_PARITY_PY") != "1":
        chosen = None
        for cand in ("python3", "python", sys.executable):
            if subprocess.run(
                [cand, "-c", "import pymupdf"], capture_output=True
            ).returncode == 0:
                chosen = cand
                break
        if chosen and os.path.realpath(chosen) != os.path.realpath(sys.executable):
            os.environ["STELE_PARITY_PY"] = "1"
            os.execvp(chosen, [chosen, *sys.argv])
        os.environ["STELE_PARITY_PY"] = "1"
    binary = sys.argv[sys.argv.index("--binary") + 1]
    version = subprocess.check_output(
        [sys.executable, "-c", "import stele_context; print(stele_context.__version__)"],
        text=True,
    ).strip()
    if version != "1.5.2":
        print(f"parity diff python package: need PyPI stele-context 1.5.2, found {version}", flush=True)
        return 1
    root = Path(tempfile.mkdtemp(prefix="stele-mcp-"))
    fixture(root)
    rs_store = Path(tempfile.mkdtemp(prefix="stele-rs-"))
    py_store = Path(tempfile.mkdtemp(prefix="stele-py-"))
    env = os.environ.copy()
    env["STELE_MCP_MODE"] = "full"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    rust = Server(
        [binary, "--storage-dir", str(rs_store), "serve-mcp"],
        env,
        root,
    )
    python = Server(
        [sys.executable, "-m", "stele_context.cli", "--storage-dir", str(py_store), "serve-mcp"],
        env,
        root,
    )
    calls = [
        ("index", {"paths": ["."]}),
        ("remove", {"document_path": "docs/guide.md"}),
        ("search", {"query": "classify", "search_mode": "keyword", "top_k": 5}),
        ("search_text", {"pattern": "classify", "regex": False}),
        ("agent_grep", {"pattern": "classify", "group_by": "file"}),
        ("query", {"query": "classify"}),
        ("find_definition", {"symbol": "classify"}),
        ("find_references", {"symbol": "classify"}),
        ("impact_radius", {"symbol": "classify"}),
        ("coupling", {"document_path": "src/main.py", "mode": "edges"}),
        ("get_context", {"document_paths": ["src/calc.py"], "session_id": "s1"}),
        ("doctor", {}),
        ("map", {"compact": True}),
        ("document_lock", {"action": "status", "document_path": "src/calc.py"}),
        ("annotations", {"action": "get", "target": "src/calc.py", "target_type": "document"}),
        ("register_dynamic_symbols", {"symbols": [{"name": "on_hook", "kind": "function", "document_path": "src/calc.py"}], "agent_id": "dyn-agent"}),
        ("get_dynamic_symbols", {"agent_id": "dyn-agent"}),
        ("remove_dynamic_symbols", {"agent_id": "dyn-agent"}),
        ("detect_modality", {"path": "docs/report.pdf"}),
        ("get_supported_formats", {}),
        ("enrichment_plan", {"top_n": 15, "min_tokens": 20}),
        ("get_search_history", {"session_id": "s1"}),
        ("get_session_read_files", {"session_id": "s1"}),
        ("detect_changes", {}),
        ("prune_history", {"max_entries": 1000}),
        ("history", {"limit": 20}),
        ("get_relevant_kv", {"session_id": "s1", "query": "classify", "top_k": 3}),
        ("list_sessions", {}),
        ("rebuild_symbols", {}),
        ("stale_chunks", {"threshold": 0.5}),
        ("list_agents", {"active_only": True}),
        ("get_notifications", {}),
        ("environment_check", {}),
        ("clean_bytecache", {}),
        ("get_chunk_history", {"limit": 5}),
        ("batch", {"operations": [{"method": "find_definition", "params": {"symbol": "classify"}}]}),
        ("stats", {}),
        ("project_brief", {}),
        ("annotate", {"target": "src/calc.py", "target_type": "document", "content": "note", "tags": ["t1"]}),
        ("get_annotations", {"target": "src/calc.py", "target_type": "document"}),
        ("search_annotations", {"query": "note"}),
        ("update_annotation", {"annotation_id": 1, "content": "edited", "tags": ["t2"]}),
        ("delete_annotation", {"annotation_id": 1}),
        ("acquire_document_lock", {"document_path": "src/calc.py", "agent_id": "other-agent"}),
        ("refresh_document_lock", {"document_path": "src/calc.py", "agent_id": "other-agent"}),
        ("get_document_lock_status", {"document_path": "src/calc.py"}),
        ("get_conflicts", {}),
        ("release_document_lock", {"document_path": "src/calc.py", "agent_id": "other-agent"}),
        ("release_agent_locks", {"agent_id": "other-agent"}),
        ("reap_expired_locks", {}),
        ("rollback", {"session_id": "missing", "target_turn": 0}),
        ("prune_chunks", {"session_id": "missing", "max_tokens": 10}),
    ]
    covered = {name for name, _ in calls}
    # Chunk tools are appended once a chunk id is known.
    later = {
        "save_kv_state",
        "llm_embed",
        "bulk_store_summaries",
        "bulk_store_embeddings",
        "bulk_store_chunk_agent_notes",
        "store_semantic_summary",
        "store_chunk_agent_notes",
    }
    required = {
        "index", "remove", "detect_changes", "search", "agent_grep", "search_text",
        "query", "find_references", "find_definition", "impact_radius", "coupling",
        "get_context", "doctor", "map", "document_lock", "annotations",
        "register_dynamic_symbols", "remove_dynamic_symbols", "llm_embed",
        "bulk_store_summaries", "enrichment_plan", "get_search_history",
        "get_session_read_files", "detect_modality", "get_supported_formats",
        "prune_history", "history", "get_relevant_kv", "save_kv_state", "rollback",
        "prune_chunks", "list_sessions", "rebuild_symbols", "stale_chunks",
        "get_dynamic_symbols", "list_agents", "get_notifications", "environment_check",
        "clean_bytecache", "bulk_store_embeddings", "bulk_store_chunk_agent_notes",
        "get_chunk_history", "batch", "stats", "project_brief", "annotate",
        "get_annotations", "delete_annotation", "update_annotation",
        "search_annotations", "acquire_document_lock",
        "refresh_document_lock", "release_document_lock", "get_document_lock_status",
        "release_agent_locks", "get_conflicts", "reap_expired_locks",
        "store_semantic_summary", "store_chunk_agent_notes",
    }
    if covered | later != required:
        missing = sorted(required - covered - later)
        extra = sorted((covered | later) - required)
        print(f"parity diff tool coverage missing={missing} extra={extra}", flush=True)
        return 1
    diffs = 0
    root_s = str(root)
    try:
        init = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "parity", "version": "0"},
        }
        rust.call("initialize", init)
        python.call("initialize", init)
        rust.notify("notifications/initialized")
        python.notify("notifications/initialized")
        pending = list(calls)
        seen_chunk = False
        saved_cid = ""
        invoked = set()
        while pending:
            name, args = pending.pop(0)
            invoked.add(name)
            left = tool(rust, name, args)
            right = tool(python, name, args)
            bad = []
            left_n = walk(left, root_s, str(rs_store))
            right_n = walk(right, root_s, str(py_store))
            for side in (left_n, right_n):
                blank_graph(side)
            left_n, right_n = timestamps_ok(left_n, right_n, bad)
            note, skip = documented(name, left_n, right_n)
            if note:
                print(f"documented {name}: {note}", flush=True)
            if bad:
                print(f"parity diff {name} timestamp type: {bad}", flush=True)
                diffs += 1
            if not skip and left_n != right_n:
                print(f"parity diff {name}: {first_diff(left_n, right_n)}", flush=True)
                diffs += 1
            if not seen_chunk and name == "get_context":
                cid = first_chunk(left)
                if cid:
                    seen_chunk = True
                    saved_cid = cid
                    vec = [0.1] * 128
                    pending[0:0] = [
                        ("save_kv_state", {"session_id": "s1", "kv_data": {cid: {"note": "alpha", "n": 1}, "missing-chunk": {"note": "absent"}}}),
                        ("get_relevant_kv", {"session_id": "s1", "query": "classify", "top_k": 3}),
                        ("get_relevant_kv", {"session_id": "s1", "query": "zzzz-unrelated-token", "top_k": 2}),
                        ("save_kv_state", {"session_id": "s2", "kv_data": {cid: {"note": "beta", "n": 2}}}),
                        ("get_relevant_kv", {"session_id": "s2", "query": "return", "top_k": 2}),
                        ("llm_embed", {"text": "def classify(n):\n    return n\n", "chunk_id": cid}),
                        ("bulk_store_summaries", {"summaries": {cid: "classifies numbers"}}),
                        ("store_semantic_summary", {"chunk_id": cid, "summary": "hello"}),
                        ("bulk_store_embeddings", {"embeddings": {cid: vec}}),
                        ("bulk_store_chunk_agent_notes", {"notes": {cid: "keep this"}}),
                        ("store_chunk_agent_notes", {"chunk_id": cid, "notes": "note"}),
                    ]
        missed = sorted(required - invoked)
        if missed:
            print(f"parity diff tools not called: {missed}", flush=True)
            diffs += 1
        rust.close()
        python.close()
        # CLI behaviour, not just --help. The server must release the store lock first.
        def cli(store, argv):
            proc = subprocess.run(
                [binary, "--storage-dir", str(store), *argv],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return proc.returncode, proc.stdout
        # Deep-compare CLI stdout before the Rust-only flag checks mutate the store.
        def py_cli(store, argv):
            proc = subprocess.run(
                [sys.executable, "-m", "stele_context.cli", "--storage-dir", str(store), *argv],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            return proc.returncode, proc.stdout

        def unwrap(text):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(value, dict) and "schema_version" in value and "result" in value:
                return value["result"]
            return value

        comparable = [
            ["search", "classify", "--path-prefix", "src", "--search-mode", "keyword", "--json"],
            ["search-text", "classify", "--limit", "5", "--json"],
            ["agent-grep", "classify", "--group-by", "file", "--json"],
            ["find-definition", "classify", "--json"],
            ["find-references", "classify", "--json"],
            ["query", "classify", "--json"],
            ["impact-radius", "--symbol", "classify", "--json"],
            ["coupling", "src/main.py", "--json"],
            ["map", "--json"],
            ["project-brief", "--json"],
            ["enrichment-plan", "--json"],
            ["stats", "--compact"],
            ["history", "--json"],
        ]
        for argv in comparable:
            rc_r, out_r = cli(rs_store, argv)
            rc_p, out_p = py_cli(py_store, argv)
            if rc_r != 0 or rc_p != 0:
                print(f"parity diff cli {argv}: rust {rc_r} python {rc_p} {out_r[:120]!r} {out_p[:120]!r}", flush=True)
                diffs += 1
                continue
            left_n = walk(unwrap(out_r), root_s, str(rs_store))
            right_n = walk(unwrap(out_p), root_s, str(py_store))
            for side in (left_n, right_n):
                blank_graph(side)
            bad = []
            left_n, right_n = timestamps_ok(left_n, right_n, bad)
            if left_n != right_n:
                print(f"parity diff cli {argv}: {first_diff(left_n, right_n)}", flush=True)
                diffs += 1
        rc_r, _out_r = cli(rs_store, ["search-text", "classify", "--path-prefix", "src"])
        rc_p, _out_p = py_cli(py_store, ["search-text", "classify", "--path-prefix", "src", "--json"])
        if rc_r == 0 or rc_p == 0:
            print(f"parity diff cli search-text --path-prefix accepted: rust {rc_r} python {rc_p}", flush=True)
            diffs += 1
        # Same annotation on both stores, then compare get-annotations.
        _rc_a, out_a = cli(rs_store, ["annotations", "--action", "create", "src/calc.py", "tagged", "--tags", "t1", "--json"])
        rc_b, out_b = py_cli(py_store, ["annotate", "--type", "document", "--content", "tagged", "--tags", "t1", "--json", "src/calc.py"])
        if rc_b != 0:
            print(f"parity diff cli annotate: {rc_b} {out_b[:300]!r}", flush=True)
            diffs += 1
        rc_r, out_r = cli(rs_store, ["get-annotations", "--target", "src/calc.py", "--type", "document", "--json"])
        rc_p, out_p = py_cli(py_store, ["get-annotations", "--target", "src/calc.py", "--type", "document", "--json"])
        left_n = walk(unwrap(out_r), root_s, str(rs_store))
        right_n = walk(unwrap(out_p), root_s, str(py_store))
        if isinstance(left_n, dict) and "annotations" in left_n:
            left_n = left_n["annotations"]
        bad = []
        left_n, right_n = timestamps_ok(left_n, right_n, bad)
        if left_n != right_n:
            print(f"parity diff cli get-annotations: {first_diff(left_n, right_n)}", flush=True)
            diffs += 1
        # Schema names the 1.5.2 functions do not accept. Observed after the
        # CLI compare so a Rust-only write does not change the shared stores.
        if not saved_cid:
            print("parity diff chunk id missing for alias calls", flush=True)
            diffs += 1
        else:
            rust_b = Server(
                [binary, "--storage-dir", str(rs_store), "serve-mcp"], env, root
            )
            python_b = Server(
                [sys.executable, "-m", "stele_context.cli", "--storage-dir", str(py_store), "serve-mcp"],
                env,
                root,
            )
            try:
                rust_b.call("initialize", init)
                python_b.call("initialize", init)
                rust_b.notify("notifications/initialized")
                python_b.notify("notifications/initialized")
                alias_calls = [
                    ("store_embedding", {"chunk_id": saved_cid, "embedding": [0.3] * 128}),
                    ("bulk_annotate", {"items": [{"target": "src/main.py", "target_type": "document", "content": "bulk", "tags": ["b"]}]}),
                ]
                for name, args in alias_calls:
                    left = tool(rust_b, name, args)
                    right = tool(python_b, name, args)
                    err = right.get("error") if isinstance(right, dict) else right
                    err_s = json.dumps(err, default=str) if not isinstance(err, str) else err
                    rust_ran = isinstance(left, dict) and (
                        left.get("stored") or left.get("created") is not None
                    )
                    if err and rust_ran:
                        print(
                            f"documented {name}: 1.5.2 stdio rejects the schema name ({err_s[:180]}); rust runs it",
                            flush=True,
                        )
                    else:
                        print(f"parity diff {name} alias: rust {left} python {right}", flush=True)
                        diffs += 1
            finally:
                rust_b.close()
                python_b.close()
        code, out = cli(rs_store, ["clear"])
        if "cancelled" not in out:
            print("parity diff cli --confirm missing:", out[:200], flush=True)
            diffs += 1
        behaviour = [
            ["search", "classify", "--path-prefix", "src", "--search-mode", "keyword", "--json"],
            ["search-text", "classif.", "--regex", "--limit", "5", "--json"],
            ["search", "classify", "--compact", "--top-k", "2", "--json"],
            ["agent-grep", "classify", "--group-by", "file", "--json"],
            ["detect", "--no-scan-new"],
            ["search", "classify", "--working-tree", "--json"],
            ["annotations", "--action", "create", "src/calc.py", "tagged", "--tags", "t1", "--json"],
        ]
        for argv in behaviour:
            code, out = cli(rs_store, argv)
            if code != 0 or not out.strip():
                print(f"parity diff cli {argv}: {code} {out[:200]}", flush=True)
                diffs += 1
        help_out = subprocess.run([binary, "--help"], capture_output=True, text=True).stdout
        if "annotations" not in help_out:
            print("parity diff cli help missing annotations")
            diffs += 1
    finally:
        rust.close()
        python.close()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(rs_store, ignore_errors=True)
        shutil.rmtree(py_store, ignore_errors=True)
    print(f"undocumented diffs: {diffs}")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
