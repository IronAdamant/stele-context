#!/usr/bin/env python3
"""Speak MCP initialize, tools/list, and one tools/call to stele-context-mcp.

Run this with the interpreter from a fresh virtualenv where the built wheel
was installed with the ``mcp`` extra. The script does not import
``stele_context``; the console script under test is the installed one.

Set ``STELE_SMOKE_MCP_MAJOR`` to ``1`` or ``2`` to require that major.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NoReturn

INITIALIZE_ID = 1
LIST_ID = 2
CALL_ID = 3
FIRST_TIMEOUT_S = 45.0
NEXT_TIMEOUT_S = 20.0


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _server_command() -> str:
    # sys.executable may be a symlink into the base install. The console
    # script is installed under the venv prefix, not next to that target.
    roots = [Path(sys.prefix) / "bin", Path(sys.prefix) / "Scripts"]
    for root in roots:
        candidate = root / "stele-context-mcp"
        if candidate.is_file():
            return str(candidate)
    _fail(f"stele-context-mcp is not installed under {sys.prefix}")


def _require_versions() -> tuple[str, str]:
    try:
        stele = importlib.metadata.version("stele-context")
        mcp = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError as exc:
        _fail(f"expected an installed wheel with the mcp extra: {exc}")
    expected = os.environ.get("STELE_SMOKE_MCP_MAJOR", "").strip()
    major = mcp.split(".", 1)[0]
    if expected and major != expected:
        _fail(f"mcp {mcp} is not major {expected}")
    print(f"stele-context {stele}")
    print(f"mcp {mcp}")
    return stele, mcp


def _send(proc: subprocess.Popen[bytes], message: dict) -> None:
    if proc.stdin is None:
        _fail("stdin is closed")
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()


def _stderr_text(chunks: list[bytes]) -> str:
    return b"".join(chunks).decode("utf-8", "replace")


def _read_id(
    proc: subprocess.Popen[bytes],
    request_id: int,
    timeout: float,
    stderr_chunks: list[bytes],
    drain: threading.Thread,
) -> dict:
    if proc.stdout is None:
        _fail("stdout is closed")
    deadline = time.monotonic() + timeout
    pending = b""
    while time.monotonic() < deadline:
        if proc.poll() is not None and not pending:
            drain.join(timeout=2)
            err = _stderr_text(stderr_chunks)
            _fail(f"server exited {proc.returncode} before id={request_id}\n{err}")
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([proc.stdout], [], [], max(0.0, remaining))
        if not ready:
            break
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        pending += chunk
        while b"\n" in pending:
            raw, pending = pending.split(b"\n", 1)
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _fail(f"non-json stdout: {line!r}")
            if message.get("id") == request_id:
                return message
    drain.join(timeout=0.2)
    _fail(f"timed out waiting for id={request_id}\n{_stderr_text(stderr_chunks)}")


def _result(message: dict) -> dict:
    if "error" in message:
        _fail("JSON-RPC error:\n" + json.dumps(message, indent=2))
    result = message.get("result")
    if not isinstance(result, dict):
        _fail("missing result:\n" + json.dumps(message, indent=2))
    return result


def _dump(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, default=str))


def _text_blocks(result: dict) -> str:
    chunks: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
            elif isinstance(block, str):
                chunks.append(block)
    return "\n".join(chunks)


def main() -> None:
    stele_version, _mcp_version = _require_versions()
    command = _server_command()
    print(f"command {command}")

    with tempfile.TemporaryDirectory(prefix="stele-mcp-smoke-") as temp:
        storage = Path(temp) / "storage"
        work = Path(temp) / "work"
        storage.mkdir()
        work.mkdir()
        env = os.environ.copy()
        env["STELE_CONTEXT_STORAGE_DIR"] = str(storage)
        env["STELE_MCP_MODE"] = "lite"
        env.pop("PYTHONPATH", None)
        proc = subprocess.Popen(
            [command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=env,
        )
        stderr_chunks: list[bytes] = []

        def _drain_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    return
                stderr_chunks.append(chunk)

        drain = threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()
        try:
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": INITIALIZE_ID,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "stele-context-smoke",
                            "version": stele_version,
                        },
                    },
                },
            )
            initialized = _result(
                _read_id(proc, INITIALIZE_ID, FIRST_TIMEOUT_S, stderr_chunks, drain)
            )
            _dump("initialize", initialized)
            info = initialized.get("serverInfo") or initialized.get("server_info")
            if not isinstance(info, dict):
                _fail("initialize result has no serverInfo")
            if info.get("name") != "stele-context":
                _fail(f"server name is {info.get('name')!r}")
            if info.get("version") != stele_version:
                _fail(
                    f"server version {info.get('version')!r} != installed {stele_version}"
                )

            _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": LIST_ID,
                    "method": "tools/list",
                    "params": {},
                },
            )
            listed = _result(
                _read_id(proc, LIST_ID, NEXT_TIMEOUT_S, stderr_chunks, drain)
            )
            _dump("tools/list", listed)
            tools = listed.get("tools")
            if not isinstance(tools, list):
                _fail("tools/list result has no tools array")
            names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
            if "doctor" not in names:
                _fail(f"doctor missing from tools: {names}")

            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": CALL_ID,
                    "method": "tools/call",
                    "params": {"name": "doctor", "arguments": {}},
                },
            )
            called = _result(
                _read_id(proc, CALL_ID, NEXT_TIMEOUT_S, stderr_chunks, drain)
            )
            _dump("tools/call doctor", called)
            if called.get("isError") is True or called.get("is_error") is True:
                _fail("doctor tools/call returned isError")
            body = _text_blocks(called)
            if stele_version not in body:
                _fail(f"doctor result does not contain {stele_version}")
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            drain.join(timeout=2)

    err = _stderr_text(stderr_chunks).strip()
    if err:
        print("\n=== stderr ===")
        print(err)
    print("\nPASS initialize, tools/list, tools/call doctor")


if __name__ == "__main__":
    main()
