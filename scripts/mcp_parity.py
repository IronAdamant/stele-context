#!/usr/bin/env python3
"""Full MCP and CLI compare of Rust against Python 1.5.2.

Speaks newline JSON-RPC tools/call to `stele-context serve-mcp` and to the
1.5.2 tool registry (the dispatch `serve-mcp` uses). The fixture has Python,
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


def legacy_src(repo: Path) -> Path:
    dest = repo / "target" / "python-legacy-src"
    if (dest / "stele_context" / "engine.py").is_file():
        return dest
    archived = subprocess.run(
        ["git", "archive", "origin/python-legacy", "stele_context"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(["tar", "-x"], cwd=dest, input=archived.stdout, check=True)
    return dest


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

SHIM = r'''
import json, os, sys
sys.path.insert(0, os.environ["PYLEGACY"])
import stele_context.engine as _engine
import stele_context.engine_utils as _utils
_orig_norm = _utils.normalize_path
def _posix_norm(path, project_root):
    return _orig_norm(path, project_root).replace("\\", "/")
_utils.normalize_path = _posix_norm
_engine._norm = _posix_norm
from stele_context.engine import Stele
from stele_context.tool_registry import build_tool_map
root, store = sys.argv[1], sys.argv[2]
engine = Stele(storage_dir=store, project_root=root)
agent = f"stele-context-mcp-{os.getpid()}"
try:
    engine.register_agent(agent)
except Exception:
    pass
tools = build_tool_map(engine, {}, mode=os.environ.get("STELE_MCP_MODE", "full"))
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        emit({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"protocolVersion": "2025-06-18"}})
        continue
    if msg.get("method") != "tools/call":
        emit({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
        continue
    params = msg.get("params") or {}
    name = params.get("name")
    args = dict(params.get("arguments") or {})
    func = tools.get(name)
    if func is None:
        text = json.dumps({"ok": False, "error": f"Unknown method: {name}"})
    else:
        if "agent_id" not in args and name in {
            "index", "acquire_document_lock", "refresh_document_lock",
            "release_document_lock", "release_agent_locks",
        }:
            args["agent_id"] = agent
        try:
            text = json.dumps(func(**args), default=str)
        except TypeError:
            # Drop arguments the 1.5 callable does not take.
            import inspect
            sig = inspect.signature(func)
            kept = {k: v for k, v in args.items() if k in sig.parameters}
            try:
                text = json.dumps(func(**kept), default=str)
            except Exception as exc:
                text = json.dumps({"ok": False, "error": str(exc)})
        except Exception as exc:
            text = json.dumps({"ok": False, "error": str(exc)})
    emit({
        "jsonrpc": "2.0",
        "id": msg.get("id"),
        "result": {"content": [{"type": "text", "text": text}]},
    })
'''


def fixture(root: Path) -> None:
    (root / "src").mkdir()
    (root / "web").mkdir()
    (root / "docs").mkdir()
    (root / "vendor").mkdir()
    (root / "src" / "calc.py").write_text("def classify(n):\n    return n\n")
    (root / "src" / "main.py").write_text("from calc import classify\n\ndef main():\n    return classify(1)\n")
    (root / "web" / "app.js").write_text("function greet(){ return 1 }\n")
    (root / "docs" / "guide.md").write_text("# guide\n\nclassify the input\n")
    note = Path(__file__).resolve().parents[1] / "parity" / "fixtures" / "media" / "note.pdf"
    if note.is_file():
        (root / "docs" / "report.pdf").write_bytes(note.read_bytes())
    else:
        (root / "docs" / "report.pdf").write_bytes(PDF)
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
        )
        self.n = 0

    def call(self, method, params):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = read_line(self.proc.stdout, 20, method)
        if not line:
            err = self.proc.stderr.read() if self.proc.stderr else ""
            raise SystemExit(f"server closed: {err[-500:]}")
        body = json.loads(line)
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
        if key in {"unchanged", "files_with_matches", "indexed", "skipped"}:
            rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        return rows
    if key == "version" and isinstance(value, str) and value[:3] in {"1.5", "2.0"}:
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
    return value


def blank_graph(value):
    """2.0 has no on-disk HNSW file and no SQLite database file."""
    if isinstance(value, dict):
        for key in ("max_level", "avg_connections", "insert_count", "database_size_bytes"):
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
                "last_heartbeat", "file_mtime",                 "document_indexed_at",
                "stored_last_modified", "started_at", "indexed_at",
                "latest_indexed_at", "seconds_since_last_index",
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
    repo = Path(__file__).resolve().parents[1]
    binary = sys.argv[sys.argv.index("--binary") + 1]
    py = legacy_src(repo)
    root = Path(tempfile.mkdtemp(prefix="stele-mcp-"))
    fixture(root)
    rs_store = Path(tempfile.mkdtemp(prefix="stele-rs-"))
    py_store = Path(tempfile.mkdtemp(prefix="stele-py-"))
    shim_dir = Path(tempfile.mkdtemp(prefix="stele-shim-"))
    shim = shim_dir / "shim.py"
    shim.write_text(SHIM)
    env = os.environ.copy()
    env["STELE_MCP_MODE"] = "full"
    env["PYLEGACY"] = str(py)
    rust = Server(
        [binary, "--storage-dir", str(rs_store), "serve-mcp"],
        env,
        root,
    )
    python = Server(
        [sys.executable, str(shim), str(root), str(py_store)],
        env,
        root,
    )
    calls = [
        ("index", {"paths": ["."]}),
        ("search", {"query": "classify", "search_mode": "keyword", "top_k": 5}),
        ("search_text", {"pattern": "classify", "regex": False}),
        ("agent_grep", {"pattern": "classify"}),
        ("find_definition", {"symbol": "classify"}),
        ("find_references", {"symbol": "classify"}),
        ("get_context", {"document_paths": ["src/calc.py"]}),
        ("detect_changes", {}),
        ("list_sessions", {}),
        ("list_agents", {"active_only": True}),
        ("get_notifications", {}),
        ("rebuild_symbols", {}),
        ("stale_chunks", {}),
        ("prune_history", {}),
        ("environment_check", {}),
        ("clean_bytecache", {}),
        ("get_chunk_history", {"limit": 5}),
        ("batch", {"operations": [{"method": "find_definition", "params": {"symbol": "classify"}}]}),
        ("annotate", {"target": "src/calc.py", "target_type": "document", "content": "note", "tags": ["t1"]}),
        ("get_annotations", {"target": "src/calc.py", "target_type": "document"}),
        ("acquire_document_lock", {"document_path": "src/calc.py", "agent_id": "other-agent"}),
        ("stats", {}),
        ("project_brief", {}),
    ]
    diffs = 0
    root_s = str(root)
    try:
        rust.call("initialize", {})
        python.call("initialize", {})
        for name, args in calls:
            left = tool(rust, name, args)
            right = tool(python, name, args)
            bad = []
            left_n = walk(left, root_s, str(rs_store))
            right_n = walk(right, root_s, str(py_store))
            for side in (left_n, right_n):
                blank_graph(side)
            left_n, right_n = timestamps_ok(left_n, right_n, bad)
            for side in (left_n, right_n):
                if isinstance(side, dict) and isinstance(side.get("notifications"), list):
                    side["notifications"].sort(key=lambda row: str(row.get("document_path")))
            if bad:
                print(f"parity diff {name} timestamp type: {bad}", flush=True)
                diffs += 1
            if left_n != right_n:
                print(f"parity diff {name}: {first_diff(left_n, right_n)}", flush=True)
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
            )
            return proc.returncode, proc.stdout
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
        shutil.rmtree(shim_dir, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(rs_store, ignore_errors=True)
        shutil.rmtree(py_store, ignore_errors=True)
    print(f"parity diffs: {diffs}")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
