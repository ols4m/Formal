#!/usr/bin/env python3
"""
Resources CLI - A simple command-line tool to save and organize resources.

Usage:
    python cli.py add <url>          Add a resource (auto-detects metadata)
    python cli.py add --note "text"  Add a text note
    python cli.py list               List all resources
    python cli.py search <query>     Search resources
    python cli.py delete <id>        Delete a resource
    python cli.py export             Export all resources to JSON
    python cli.py export <file>      Export to specific file
"""

import argparse
import json
import sys
from datetime import datetime

from db import add_resource, get_all_resources, delete_resource, search_resources
from fetcher import fetch_metadata, is_valid_url, FETCH_AVAILABLE


def cmd_add(args):
    """Add a new resource."""
    if args.note:
        # Adding a text note
        resource_id = add_resource(
            title=args.note[:50] + "..." if len(args.note) > 50 else args.note,
            resource_type="note",
            description=args.note,
            platform="note"
        )
        print(f"[+] Note saved (ID: {resource_id})")
        return

    if not args.url:
        print("[-] Please provide a URL or use --note for text")
        return

    url = args.url

    # Validate URL
    if not is_valid_url(url):
        # Maybe it's missing the scheme
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if not is_valid_url(url):
            print(f"[-] Invalid URL: {args.url}")
            return

    print(f"[*] Fetching metadata for: {url}")

    # Fetch metadata
    metadata = fetch_metadata(url)

    # Save to database
    resource_id = add_resource(
        title=metadata["title"],
        url=metadata["url"],
        resource_type=metadata["type"],
        platform=metadata["platform"],
        thumbnail=metadata["thumbnail"],
        description=metadata["description"]
    )

    print(f"[+] Resource saved!")
    print(f"    ID: {resource_id}")
    print(f"    Title: {metadata['title']}")
    print(f"    Platform: {metadata['platform']}")
    print(f"    Type: {metadata['type']}")


def cmd_list(args):
    """List all resources."""
    resources = get_all_resources()

    if not resources:
        print("[*] No resources saved yet. Use 'add <url>' to add one.")
        return

    print(f"\n{'='*60}")
    print(f" RESOURCES ({len(resources)} total)")
    print(f"{'='*60}\n")

    for r in resources:
        type_icon = {
            "video": "[VIDEO]",
            "link": "[LINK]",
            "note": "[NOTE]",
            "document": "[DOC]",
            "image": "[IMG]",
            "audio": "[AUDIO]",
        }.get(r["type"], "[LINK]")

        print(f"  {r['id']:3}. {type_icon:8} {r['title'][:50]}")
        if r["url"]:
            print(f"       {r['platform']:12} {r['url'][:60]}")
        if r["description"] and r["type"] == "note":
            print(f"       {r['description'][:60]}")
        print()


def cmd_search(args):
    """Search resources."""
    if not args.query:
        print("[-] Please provide a search query")
        return

    results = search_resources(args.query)

    if not results:
        print(f"[*] No resources found matching '{args.query}'")
        return

    print(f"\n[*] Found {len(results)} result(s) for '{args.query}':\n")

    for r in results:
        print(f"  {r['id']:3}. [{r['type'].upper():6}] {r['title'][:50]}")
        if r["url"]:
            print(f"       {r['url'][:60]}")
        print()


def cmd_delete(args):
    """Delete a resource."""
    if not args.id:
        print("[-] Please provide a resource ID to delete")
        return

    try:
        resource_id = int(args.id)
    except ValueError:
        print(f"[-] Invalid ID: {args.id}")
        return

    if delete_resource(resource_id):
        print(f"[+] Resource {resource_id} deleted")
    else:
        print(f"[-] Resource {resource_id} not found")


def cmd_export(args):
    """Export resources to JSON."""
    resources = get_all_resources()

    if not resources:
        print("[*] No resources to export")
        return

    # Convert datetime objects to strings
    for r in resources:
        if r.get("created_at"):
            r["created_at"] = str(r["created_at"])

    json_data = json.dumps(resources, indent=2)

    if args.file:
        with open(args.file, "w") as f:
            f.write(json_data)
        print(f"[+] Exported {len(resources)} resources to {args.file}")
    else:
        print(json_data)


def main():
    parser = argparse.ArgumentParser(
        description="Resources CLI - Save and organize your resources",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py add "https://youtube.com/watch?v=abc123"
  python cli.py add --note "Remember to check out this topic"
  python cli.py list
  python cli.py search "python"
  python cli.py export resources.json
  python cli.py delete 5
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a new resource")
    add_parser.add_argument("url", nargs="?", help="URL to add")
    add_parser.add_argument("--note", "-n", help="Add a text note instead of URL")

    # List command
    list_parser = subparsers.add_parser("list", help="List all resources")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search resources")
    search_parser.add_argument("query", help="Search query")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a resource")
    delete_parser.add_argument("id", help="Resource ID to delete")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export resources to JSON")
    export_parser.add_argument("file", nargs="?", help="Output file (prints to console if not specified)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Check for dependencies
    if args.command == "add" and not args.note and not FETCH_AVAILABLE:
        print("[!] Warning: requests/beautifulsoup4 not installed.")
        print("    Metadata fetching will be limited.")
        print("    Install with: pip install requests beautifulsoup4")
        print()

    # Run command
    commands = {
        "add": cmd_add,
        "list": cmd_list,
        "search": cmd_search,
        "delete": cmd_delete,
        "export": cmd_export,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
