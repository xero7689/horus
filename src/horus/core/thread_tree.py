"""Build and render threaded conversations from ScrapedItem lists."""

from __future__ import annotations

from dataclasses import dataclass, field

from horus.models import ScrapedItem


@dataclass
class ThreadNode:
    item: ScrapedItem
    children: list[ThreadNode] = field(default_factory=list)


def build_thread_trees(items: list[ScrapedItem]) -> list[ThreadNode]:
    """Group items into thread trees keyed by conversation.

    Items with ``extra.is_reply == False`` (or missing) are treated as roots.
    Replies are attached to their ``parent_post_id``; orphans whose parent is
    not in the input list are attached to the conversation root as a fallback.

    Returns roots sorted by timestamp ascending.
    """
    nodes: dict[str, ThreadNode] = {item.id: ThreadNode(item=item) for item in items}
    roots: list[ThreadNode] = []

    for item in items:
        node = nodes[item.id]
        is_reply = bool(item.extra.get("is_reply", False))
        parent_id = item.extra.get("parent_post_id")
        if not is_reply:
            roots.append(node)
            continue
        parent = nodes.get(parent_id) if parent_id else None
        if parent is None:
            conv_id = item.extra.get("conversation_id")
            parent = nodes.get(conv_id) if conv_id else None
        if parent is not None and parent is not node:
            parent.children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=lambda n: n.item.timestamp)
    roots.sort(key=lambda n: n.item.timestamp)
    return roots


def render_thread_md(root: ThreadNode) -> str:
    """Render a thread tree to markdown with indented replies."""
    item = root.item
    author = item.author_name or item.author_id or "unknown"
    date = item.timestamp.strftime("%Y-%m-%d %H:%M")
    lines = [f"# @{author} · {date}", ""]
    if item.text:
        lines.extend([item.text, ""])

    if root.children:
        lines.extend(["---", "## 留言", ""])
        for child in root.children:
            _render_reply(child, depth=0, lines=lines)

    return "\n".join(lines).rstrip() + "\n"


def _render_reply(node: ThreadNode, *, depth: int, lines: list[str]) -> None:
    item = node.item
    author = item.author_name or item.author_id or "unknown"
    time_str = item.timestamp.strftime("%H:%M")
    indent = "  " * depth
    text = (item.text or "").replace("\n", " ").strip()
    prefix = ""
    reply_to = item.extra.get("reply_to_username")
    if reply_to and depth > 0:
        prefix = f"> 回覆 @{reply_to} "
    lines.append(f"{indent}- @{author} · {time_str}：{prefix}{text}")
    for child in node.children:
        _render_reply(child, depth=depth + 1, lines=lines)
