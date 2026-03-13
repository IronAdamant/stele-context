"""
Command-line interface for ChunkForge.

Provides CLI commands for:
- Starting the MCP server (HTTP or stdio)
- Indexing documents
- Semantic search
- Managing sessions
- Viewing statistics
"""

import argparse
import json
import sys
from typing import List, Optional

from chunkforge import __version__ as chunkforge_version
from chunkforge.engine import ChunkForge
from chunkforge.mcp_server import MCPServer


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for ChunkForge CLI."""
    parser = argparse.ArgumentParser(
        prog="chunkforge",
        description="ChunkForge — Local context cache for LLM agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the stdio MCP server (for Claude Desktop)
  chunkforge serve-mcp

  # Start the HTTP REST server
  chunkforge serve --port 9876

  # Index documents
  chunkforge index document1.py document2.md

  # Semantic search
  chunkforge search "authentication logic" --top-k 5

  # Show statistics
  chunkforge stats
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"chunkforge {chunkforge_version}",
    )

    parser.add_argument(
        "--storage-dir",
        type=str,
        default=None,
        help="Storage directory (default: ~/.chunkforge/)",
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

    return parser


def cmd_serve(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Start the HTTP REST server."""
    server = MCPServer(
        chunkforge=chunkforge,
        host=args.host,
        port=args.port,
    )

    try:
        server.start(blocking=args.blocking)

        if not args.blocking:
            print(f"Server running at {server.get_url()}")
            print("Press Ctrl+C to stop")

            try:
                import time

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


def cmd_serve_mcp(args: argparse.Namespace, chunkforge: Optional[ChunkForge]) -> int:
    """Start the stdio MCP server."""
    from chunkforge.mcp_stdio import main as mcp_main

    mcp_main(storage_dir=args.storage_dir)
    return 0


def cmd_index(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Index documents."""
    print(f"Indexing {len(args.paths)} document(s)...")

    result = chunkforge.index_documents(
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


def cmd_search(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Semantic search across indexed chunks."""
    results = chunkforge.search(query=args.query, top_k=args.top_k)

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


def cmd_detect(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Detect changes in indexed documents."""
    print(f"Detecting changes for session '{args.session}'...")

    result = chunkforge.detect_changes_and_update(
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


def cmd_stats(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Show storage statistics."""
    stats = chunkforge.get_stats()

    print("ChunkForge Statistics")
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


def cmd_clear(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """Clear all stored data."""
    if not args.confirm:
        response = input("Are you sure you want to clear all data? (yes/no): ")
        if response.lower() not in ("yes", "y"):
            print("Cancelled")
            return 0

    print("Clearing all data...")
    chunkforge.storage.clear_all()
    print("Done")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for ChunkForge CLI."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # serve-mcp doesn't need full ChunkForge init (it creates its own)
    if args.command == "serve-mcp":
        return cmd_serve_mcp(args, None)

    chunkforge = ChunkForge(storage_dir=args.storage_dir)

    command_handlers = {
        "serve": cmd_serve,
        "index": cmd_index,
        "search": cmd_search,
        "detect": cmd_detect,
        "stats": cmd_stats,
        "clear": cmd_clear,
    }

    handler = command_handlers.get(args.command)
    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    return handler(args, chunkforge)


if __name__ == "__main__":
    sys.exit(main())
