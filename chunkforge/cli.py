"""
Command-line interface for ChunkForge.

Provides CLI commands for:
- Starting the MCP server
- Indexing documents
- Managing sessions
- Viewing statistics

Uses Python standard library argparse for zero additional dependencies.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from chunkforge.core import ChunkForge
from chunkforge.mcp_server import MCPServer


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for ChunkForge CLI."""
    parser = argparse.ArgumentParser(
        prog="chunkforge",
        description="ChunkForge - Local KV-cache rollback and offload engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the MCP server
  chunkforge serve --port 9876

  # Index documents
  chunkforge index document1.py document2.md

  # Show statistics
  chunkforge stats

  # Clear all data
  chunkforge clear
        """,
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="chunkforge 0.3.0",
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
    
    # serve command
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the MCP server",
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
    
    # detect command
    detect_parser = subparsers.add_parser(
        "detect",
        help="Detect changes and update KV-cache",
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
    """
    Start the MCP server.
    
    Args:
        args: Parsed arguments
        chunkforge: ChunkForge instance
        
    Returns:
        Exit code
    """
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
            
            # Keep main thread alive
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


def cmd_index(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """
    Index documents.
    
    Args:
        args: Parsed arguments
        chunkforge: ChunkForge instance
        
    Returns:
        Exit code
    """
    print(f"Indexing {len(args.paths)} document(s)...")
    
    result = chunkforge.index_documents(
        paths=args.paths,
        force_reindex=args.force,
    )
    
    # Print results
    if result["indexed"]:
        print(f"\nIndexed {len(result['indexed'])} document(s):")
        for item in result["indexed"]:
            print(f"  {item['path']}: {item['chunk_count']} chunks, {item['total_tokens']} tokens")
    
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


def cmd_detect(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """
    Detect changes and update KV-cache.
    
    Args:
        args: Parsed arguments
        chunkforge: ChunkForge instance
        
    Returns:
        Exit code
    """
    print(f"Detecting changes for session '{args.session}'...")
    
    result = chunkforge.detect_changes_and_update(
        session_id=args.session,
        document_paths=args.paths if args.paths else None,
    )
    
    # Print results
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
    
    print(f"\nKV-cache: {result['kv_restored']} restored, {result['kv_reprocessed']} reprocessed")
    return 0


def cmd_stats(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """
    Show storage statistics.
    
    Args:
        args: Parsed arguments
        chunkforge: ChunkForge instance
        
    Returns:
        Exit code
    """
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
    
    config = stats["config"]
    print("Configuration:")
    print(f"  Chunk size: {config['chunk_size']} tokens")
    print(f"  Max chunk size: {config['max_chunk_size']} tokens")
    print(f"  Merge threshold: {config['merge_threshold']}")
    print(f"  Change threshold: {config['change_threshold']}")
    
    return 0


def cmd_clear(args: argparse.Namespace, chunkforge: ChunkForge) -> int:
    """
    Clear all stored data.
    
    Args:
        args: Parsed arguments
        chunkforge: ChunkForge instance
        
    Returns:
        Exit code
    """
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
    """
    Main entry point for ChunkForge CLI.
    
    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])
        
    Returns:
        Exit code
    """
    parser = create_parser()
    args = parser.parse_args(argv)
    
    if args.command is None:
        parser.print_help()
        return 0
    
    # Create ChunkForge instance
    chunkforge = ChunkForge(storage_dir=args.storage_dir)
    
    # Dispatch to command handler
    command_handlers = {
        "serve": cmd_serve,
        "index": cmd_index,
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
