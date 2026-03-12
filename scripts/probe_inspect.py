#!/usr/bin/env python3
"""Inspect saved probe responses for comment-related data."""

import json
from pathlib import Path

data = json.loads(Path("/tmp/threads_probe_responses.json").read_text())

for i, entry in enumerate(data):
    body = entry["body"]
    d = body.get("data", {})
    if not isinstance(d, dict):
        continue
    # Focus on responses with data.data.posts
    inner = d.get("data", {})
    if not isinstance(inner, dict):
        continue
    posts = inner.get("posts")
    if not posts:
        continue

    print(f"\n{'='*60}")
    print(f"Response #{i+1}: {entry['url']}")
    print(f"Number of posts: {len(posts)}")
    for j, post in enumerate(posts):
        print(f"\n  Post #{j+1}:")
        print(f"    pk: {post.get('pk')}")
        print(f"    id: {post.get('id')}")
        print(f"    like_count: {post.get('like_count')}")
        tpai = post.get("text_post_app_info", {}) or {}
        if isinstance(tpai, dict):
            print(f"    text_post_app_info keys: {list(tpai.keys())}")
            print(f"      direct_reply_count: {tpai.get('direct_reply_count')}")
            print(f"      reply_to_author: {tpai.get('reply_to_author')}")
            print(f"      share_count: {tpai.get('share_count')}")
        # All keys
        print(f"    all post keys: {list(post.keys())}")
        # Full post
        print(f"    full post:")
        print(json.dumps(post, indent=6, ensure_ascii=False, default=str))
