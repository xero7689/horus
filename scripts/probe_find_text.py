#!/usr/bin/env python3
"""Search all probe responses for any field containing 'thread_items', 'edges', or text content."""

import json
from pathlib import Path


def search_keys(obj: object, path: str = "") -> list[str]:
    """Recursively find all key paths in a nested structure."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_path = f"{path}.{k}" if path else k
            results.append(full_path)
            results.extend(search_keys(v, full_path))
    elif isinstance(obj, list) and obj:
        results.extend(search_keys(obj[0], f"{path}[0]"))
    return results


data = json.loads(Path("/tmp/threads_probe_responses.json").read_text())

print("=== All key paths per response ===\n")
for i, entry in enumerate(data):
    body = entry["body"]
    paths = search_keys(body)
    # Look for interesting paths
    interesting = [
        p for p in paths
        if any(kw in p.lower() for kw in [
            "thread", "edge", "node", "caption", "text", "user", "username",
            "taken_at", "media", "comment", "reply", "content", "post"
        ])
    ]
    if interesting:
        print(f"Response #{i+1} ({entry['url']}):")
        for p in interesting:
            print(f"  {p}")
        print()

print("\n=== Checking for responses with 'text' or 'caption' anywhere ===\n")
for i, entry in enumerate(data):
    raw = json.dumps(entry["body"])
    if '"text"' in raw or '"caption"' in raw or '"taken_at"' in raw:
        print(f"Response #{i+1} contains text/caption/taken_at")
        # Show what keys exist
        body = entry["body"]
        d = body.get("data", {})
        if isinstance(d, dict):
            print(f"  data keys: {list(d.keys())}")
            # Try to find the first 'text' value
            raw_body = json.dumps(body)
            idx = raw_body.find('"text"')
            if idx >= 0:
                print(f"  First 'text' context: ...{raw_body[max(0,idx-20):idx+100]}...")
        print()
