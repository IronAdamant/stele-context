"""
CLI handlers for Stele metadata commands.

Provides handler functions for annotate, get-annotations,
delete-annotation, update-annotation, map, and history subcommands.
"""

import argparse
import json
import sys
from datetime import datetime

from stele.engine import Stele


def cmd_annotate(args: argparse.Namespace, stele: Stele) -> int:
    """Add an annotation to a document or chunk."""
    result = stele.annotate(
        target=args.target,
        target_type=args.type,
        content=args.content,
        tags=args.tags,
    )
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1
    if getattr(args, "output_json", False):
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Created annotation #{result['id']} on {result['target_type']} '{result['target']}'"
        )
    return 0


def cmd_get_annotations(args: argparse.Namespace, stele: Stele) -> int:
    """Retrieve and display annotations."""
    annotations = stele.get_annotations(
        target=args.target,
        target_type=args.type,
        tags=args.tags,
    )
    if getattr(args, "output_json", False):
        print(json.dumps(annotations, indent=2, default=str))
        return 0
    if not annotations:
        print("No annotations found.")
        return 0
    print(f"Found {len(annotations)} annotation(s):\n")
    for ann in annotations:
        tags_str = f" [{', '.join(ann['tags'])}]" if ann["tags"] else ""
        print(f"  #{ann['id']} ({ann['target_type']}) {ann['target']}{tags_str}")
        print(f"    {ann['content']}")
        print()
    return 0


def cmd_delete_annotation(args: argparse.Namespace, stele: Stele) -> int:
    """Delete an annotation by ID."""
    result = stele.delete_annotation(args.annotation_id)
    if result["deleted"]:
        print(f"Deleted annotation #{args.annotation_id}")
    else:
        print(f"Annotation #{args.annotation_id} not found", file=sys.stderr)
        return 1
    return 0


def cmd_update_annotation(args: argparse.Namespace, stele: Stele) -> int:
    """Update an annotation's content and/or tags."""
    if args.content is None and args.tags is None:
        print("Error: provide --content and/or --tags to update", file=sys.stderr)
        return 1
    result = stele.update_annotation(
        annotation_id=args.annotation_id,
        content=args.content,
        tags=args.tags,
    )
    if getattr(args, "output_json", False):
        print(json.dumps(result, indent=2))
    elif result["updated"]:
        print(f"Updated annotation #{args.annotation_id}")
    else:
        print(f"Annotation #{args.annotation_id} not found", file=sys.stderr)
        return 1
    return 0


def cmd_map(args: argparse.Namespace, stele: Stele) -> int:
    """Show project map: all documents with chunk counts and annotations."""
    result = stele.get_map()
    if getattr(args, "output_json", False):
        print(json.dumps(result, indent=2, default=str))
        return 0
    if not result["documents"]:
        print("No indexed documents.")
        return 0
    print(
        f"Project Map ({result['total_documents']} documents, "
        f"{result['total_tokens']:,} tokens)\n"
    )
    for doc in result["documents"]:
        ann_count = len(doc["annotations"])
        ann_str = f", {ann_count} annotation(s)" if ann_count else ""
        print(f"  {doc['path']}")
        print(
            f"    {doc['chunk_count']} chunks, {doc['total_tokens']:,} tokens{ann_str}"
        )
        for ann in doc["annotations"]:
            tags_str = f" [{', '.join(ann['tags'])}]" if ann["tags"] else ""
            print(f"    > {ann['content']}{tags_str}")
        print()
    return 0


def cmd_history(args: argparse.Namespace, stele: Stele) -> int:
    """Show change history."""
    entries = stele.get_history(
        limit=args.limit,
        document_path=args.document,
    )
    if getattr(args, "output_json", False):
        print(json.dumps(entries, indent=2, default=str))
        return 0
    if not entries:
        print("No change history.")
        return 0
    print(f"Change History ({len(entries)} entries):\n")
    for entry in entries:
        ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        reason = f" — {entry['reason']}" if entry.get("reason") else ""
        session = entry.get("session_id", "unknown")
        summary = entry["summary"]
        unchanged = len(summary.get("unchanged", []))
        modified = len(summary.get("modified", []))
        new = len(summary.get("new", []))
        print(f"  [{ts}] session={session}{reason}")
        print(f"    unchanged={unchanged} modified={modified} new={new}")
        print()
    return 0
