"""
Command-line interface for Stele.

Provides CLI commands for:
- Starting the MCP server (HTTP or stdio)
- Indexing documents
- Semantic search
- Managing sessions
- Viewing statistics
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from stele import __version__ as stele_version
from stele.engine import Stele
from stele.mcp_server import MCPServer


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for Stele CLI."""
    parser = argparse.ArgumentParser(
        prog="stele",
        description="Stele — Local context cache for LLM agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the stdio MCP server (for Claude Desktop)
  stele serve-mcp

  # Start the HTTP REST server
  stele serve --port 9876

  # Index documents
  stele index document1.py document2.md

  # Semantic search
  stele search "authentication logic" --top-k 5

  # Show statistics
  stele stats
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"stele {stele_version}",
    )

    parser.add_argument(
        "--storage-dir",
        type=str,
        default=None,
        help="Storage directory (default: ~/.stele/)",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
    )

    # serve command (HTTP REST)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the HTTP REST server",
    )
    serve_parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Host to bind to (default: localhost)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=9876,
        help="Port to bind to (default: 9876)",
    )
    serve_parser.add_argument(
        "--blocking",
        action="store_true",
        help="Run in blocking mode (default: background)",
    )

    # serve-mcp command (stdio MCP)
    subparsers.add_parser(
        "serve-mcp",
        help="Start the stdio MCP server (for Claude Desktop)",
    )

    # index command
    index_parser = subparsers.add_parser(
        "index",
        help="Index documents",
    )
    index_parser.add_argument(
        "paths",
        nargs="+",
        help="Document paths to index",
    )
    index_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-indexing even if unchanged",
    )

    # remove command
    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove a document and all its data from the index",
    )
    remove_parser.add_argument(
        "path",
        help="Document path to remove",
    )
    remove_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # search command
    search_parser = subparsers.add_parser(
        "search",
        help="Semantic search across indexed chunks",
    )
    search_parser.add_argument(
        "query",
        help="Search query text",
    )
    search_parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of results (default: 10)",
    )
    search_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # detect command
    detect_parser = subparsers.add_parser(
        "detect",
        help="Detect changes in indexed documents",
    )
    detect_parser.add_argument(
        "--session",
        type=str,
        default="default",
        help="Session ID (default: default)",
    )
    detect_parser.add_argument(
        "paths",
        nargs="*",
        help="Document paths to check (default: all indexed)",
    )

    # stats command
    subparsers.add_parser(
        "stats",
        help="Show storage statistics",
    )

    # clear command
    clear_parser = subparsers.add_parser(
        "clear",
        help="Clear all stored data",
    )
    clear_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt",
    )

    # annotate command
    annotate_parser = subparsers.add_parser(
        "annotate",
        help="Add an annotation to a document or chunk",
    )
    annotate_parser.add_argument(
        "target",
        help="Document path or chunk ID",
    )
    annotate_parser.add_argument(
        "--type",
        required=True,
        choices=["document", "chunk"],
        help="Target type",
    )
    annotate_parser.add_argument(
        "--content",
        required=True,
        help="Annotation text",
    )
    annotate_parser.add_argument(
        "--tags",
        nargs="+",
        help="Optional tags",
    )
    annotate_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # get-annotations command
    get_ann_parser = subparsers.add_parser(
        "get-annotations",
        help="Retrieve annotations",
    )
    get_ann_parser.add_argument(
        "--target",
        help="Filter by document path or chunk ID",
    )
    get_ann_parser.add_argument(
        "--type",
        choices=["document", "chunk"],
        help="Filter by target type",
    )
    get_ann_parser.add_argument(
        "--tags",
        nargs="+",
        help="Filter by tags",
    )
    get_ann_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # delete-annotation command
    del_ann_parser = subparsers.add_parser(
        "delete-annotation",
        help="Delete an annotation by ID",
    )
    del_ann_parser.add_argument(
        "annotation_id",
        type=int,
        help="Annotation ID to delete",
    )

    # update-annotation command
    upd_ann_parser = subparsers.add_parser(
        "update-annotation",
        help="Update an annotation's content and/or tags",
    )
    upd_ann_parser.add_argument(
        "annotation_id",
        type=int,
        help="Annotation ID to update",
    )
    upd_ann_parser.add_argument(
        "--content",
        help="New annotation text",
    )
    upd_ann_parser.add_argument(
        "--tags",
        nargs="+",
        help="New tags (replaces existing)",
    )
    upd_ann_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # map command
    map_parser = subparsers.add_parser(
        "map",
        help="Show project map: documents, chunks, annotations",
    )
    map_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    # history command
    history_parser = subparsers.add_parser(
        "history",
        help="Show change history",
    )
    history_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max entries to show (default: 20)",
    )
    history_parser.add_argument(
        "--document",
        help="Filter by document path",
    )
    history_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )

    return parser


def cmd_serve(args: argparse.Namespace, stele: Stele) -> int:
    """Start the HTTP REST server."""
    server = MCPServer(
        stele=stele,
        host=args.host,
        port=args.port,
    )

    try:
        server.start(blocking=args.blocking)

        if not args.blocking:
            print(f"Server running at {server.get_url()}")
            print("Press Ctrl+C to stop")

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping server...")
                server.stop()

        return 0
    except OSError as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nServer stopped")
        return 0


def cmd_serve_mcp(args: argparse.Namespace, _stele: None = None) -> int:
    """Start the stdio MCP server."""
    from stele.mcp_stdio import run as mcp_run

    mcp_run(storage_dir=args.storage_dir)
    return 0


def cmd_remove(args: argparse.Namespace, stele: Stele) -> int:
    """Remove a document and all its data."""
    result = stele.remove_document(args.path)
    if getattr(args, "output_json", False):
        print(json.dumps(result, indent=2))
    elif result.get("removed"):
        print(
            f"Removed {args.path}: {result['chunks_removed']} chunks, "
            f"{result['annotations_removed']} annotations deleted"
        )
    else:
        print(f"Document not found: {args.path}", file=sys.stderr)
        return 1
    return 0


def cmd_index(args: argparse.Namespace, stele: Stele) -> int:
    """Index documents."""
    print(f"Indexing {len(args.paths)} document(s)...")

    result = stele.index_documents(
        paths=args.paths,
        force_reindex=args.force,
    )

    if result["indexed"]:
        print(f"\nIndexed {len(result['indexed'])} document(s):")
        for item in result["indexed"]:
            modality = item.get("modality", "unknown")
            print(
                f"  {item['path']}: {item['chunk_count']} chunks, "
                f"{item['total_tokens']} tokens [{modality}]"
            )

    if result["skipped"]:
        print(f"\nSkipped {len(result['skipped'])} unchanged document(s):")
        for item in result["skipped"]:
            print(f"  {item['path']}: {item['reason']}")

    if result["errors"]:
        print(f"\nErrors ({len(result['errors'])}):", file=sys.stderr)
        for item in result["errors"]:
            print(f"  {item['path']}: {item['error']}", file=sys.stderr)
        return 1

    print(f"\nTotal: {result['total_chunks']} chunks, {result['total_tokens']} tokens")
    return 0


def cmd_search(args: argparse.Namespace, stele: Stele) -> int:
    """Semantic search across indexed chunks."""
    results = stele.search(query=args.query, top_k=args.top_k)

    if args.output_json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    if not results:
        print("No results found.")
        return 0

    print(f"Found {len(results)} result(s) for: {args.query}\n")
    for i, result in enumerate(results, 1):
        score = result["relevance_score"]
        path = result["document_path"]
        tokens = result["token_count"]
        content = result.get("content", "")

        print(f"  {i}. [{score:.3f}] {path} ({tokens} tokens)")
        if content:
            preview = content[:200].replace("\n", " ")
            if len(content) > 200:
                preview += "..."
            print(f"     {preview}")
        print()

    return 0


def cmd_detect(args: argparse.Namespace, stele: Stele) -> int:
    """Detect changes in indexed documents."""
    print(f"Detecting changes for session '{args.session}'...")

    result = stele.detect_changes_and_update(
        session_id=args.session,
        document_paths=args.paths if args.paths else None,
    )

    if result["unchanged"]:
        print(f"\nUnchanged ({len(result['unchanged'])}):")
        for path in result["unchanged"]:
            print(f"  {path}")

    if result["modified"]:
        print(f"\nModified ({len(result['modified'])}):")
        for item in result["modified"]:
            if isinstance(item, dict):
                print(f"  {item['path']}: {item.get('reason', 'content changed')}")
            else:
                print(f"  {item}")

    if result["new"]:
        print(f"\nNew ({len(result['new'])}):")
        for item in result["new"]:
            if isinstance(item, dict):
                print(f"  {item['path']}: {item.get('reason', 'new document')}")
            else:
                print(f"  {item}")

    if result["removed"]:
        print(f"\nRemoved ({len(result['removed'])}):")
        for path in result["removed"]:
            print(f"  {path}")

    print(
        f"\nCache: {result['kv_restored']} restored, {result['kv_reprocessed']} reprocessed"
    )
    return 0


def cmd_stats(args: argparse.Namespace, stele: Stele) -> int:
    """Show storage statistics."""
    stats = stele.get_stats()

    print("Stele Statistics")
    print("=" * 50)
    print(f"Version: {stats['version']}")
    print()

    storage = stats["storage"]
    print("Storage:")
    print(f"  Directory: {storage['storage_dir']}")
    print(f"  Documents: {storage['document_count']}")
    print(f"  Chunks: {storage['chunk_count']}")
    print(f"  Sessions: {storage['session_count']}")
    print(f"  Total tokens: {storage['total_tokens']:,}")
    print(f"  KV cache size: {storage['kv_cache_size_bytes'] / 1024 / 1024:.2f} MB")
    print(f"  Database size: {storage['database_size_bytes'] / 1024 / 1024:.2f} MB")
    print()

    index = stats.get("index", {})
    if index:
        print("Vector Index:")
        print(f"  Chunks indexed: {index.get('chunk_count', 0)}")
        print(f"  HNSW nodes: {index.get('node_count', 0)}")
        print()

    config = stats["config"]
    print("Configuration:")
    print(f"  Chunk size: {config['chunk_size']} tokens")
    print(f"  Max chunk size: {config['max_chunk_size']} tokens")
    print(f"  Merge threshold: {config['merge_threshold']}")
    print(f"  Change threshold: {config['change_threshold']}")

    return 0


def cmd_clear(args: argparse.Namespace, stele: Stele) -> int:
    """Clear all stored data."""
    if not args.confirm:
        response = input("Are you sure you want to clear all data? (yes/no): ")
        if response.lower() not in ("yes", "y"):
            print("Cancelled")
            return 0

    print("Clearing all data...")
    stele.storage.clear_all()
    # Clear persisted HNSW index files
    for idx_file in stele.storage.index_dir.glob("*"):
        idx_file.unlink()
    # Reset in-memory vector index
    stele.vector_index = stele._load_or_rebuild_index()
    print("Done")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point for Stele CLI."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # serve-mcp doesn't need full Stele init (it creates its own)
    if args.command == "serve-mcp":
        return cmd_serve_mcp(args, None)

    stele = Stele(storage_dir=args.storage_dir)

    from stele.cli_metadata import (
        cmd_annotate,
        cmd_get_annotations,
        cmd_delete_annotation,
        cmd_update_annotation,
        cmd_map,
        cmd_history,
    )

    command_handlers = {
        "serve": cmd_serve,
        "remove": cmd_remove,
        "index": cmd_index,
        "search": cmd_search,
        "detect": cmd_detect,
        "stats": cmd_stats,
        "clear": cmd_clear,
        "annotate": cmd_annotate,
        "get-annotations": cmd_get_annotations,
        "delete-annotation": cmd_delete_annotation,
        "update-annotation": cmd_update_annotation,
        "map": cmd_map,
        "history": cmd_history,
    }

    handler = command_handlers.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    return handler(args, stele)


if __name__ == "__main__":
    sys.exit(main())
