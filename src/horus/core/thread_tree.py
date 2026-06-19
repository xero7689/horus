"""Build and render threaded conversations from ScrapedItem lists."""

from __future__ import annotations

from dataclasses import dataclass, field

from horus.models import ScrapedItem


@dataclass
class ThreadNode:
    item: ScrapedItem
    children: list[ThreadNode] = field(default_factory=list)
    is_root: bool = False


def build_thread_trees(items: list[ScrapedItem]) -> list[ThreadNode]:
    """Group items into thread trees keyed by conversation.

    Items with ``extra.is_reply == False`` (or missing) are treated as true
    roots (``is_root=True``). Replies are attached to their
    ``parent_post_id``; orphan replies whose parent is missing from the input
    fall back to ``conversation_id``, and if that is also missing they are
    surfaced as roots with ``is_root=False`` so callers can distinguish them.

    Cycles in ``parent_post_id`` are broken by promoting the cycle's first
    visited node to a (synthetic) root, preventing infinite descent later.

    Returns roots sorted by timestamp ascending.
    """
    nodes: dict[str, ThreadNode] = {item.id: ThreadNode(item=item) for item in items}
    roots: list[ThreadNode] = []
    attached: set[str] = set()

    for item in items:
        node = nodes[item.id]
        is_reply = bool(item.extra.get("is_reply", False))
        if not is_reply:
            node.is_root = True
            roots.append(node)
            continue
        parent = _resolve_parent(item, nodes)
        if parent is None or parent is node or _creates_cycle(node, parent, nodes):
            roots.append(node)
            continue
        parent.children.append(node)
        attached.add(item.id)

    for node in nodes.values():
        node.children.sort(key=lambda n: n.item.timestamp)
    roots.sort(key=lambda n: n.item.timestamp)
    return roots


def _resolve_parent(item: ScrapedItem, nodes: dict[str, ThreadNode]) -> ThreadNode | None:
    parent_id = item.extra.get("parent_post_id")
    parent = nodes.get(parent_id) if parent_id else None
    if parent is not None:
        return parent
    conv_id = item.extra.get("conversation_id")
    return nodes.get(conv_id) if conv_id else None


def _creates_cycle(node: ThreadNode, parent: ThreadNode, nodes: dict[str, ThreadNode]) -> bool:
    """Return True if attaching ``node`` under ``parent`` would form a cycle."""
    visited: set[str] = set()
    current: ThreadNode | None = parent
    while current is not None:
        if current.item.id == node.item.id:
            return True
        if current.item.id in visited:
            return True
        visited.add(current.item.id)
        current = _resolve_parent(current.item, nodes)
    return False


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
        visited: set[str] = {root.item.id}
        for child in root.children:
            _render_reply(child, depth=0, lines=lines, visited=visited)

    return "\n".join(lines).rstrip() + "\n"


def _render_reply(node: ThreadNode, *, depth: int, lines: list[str], visited: set[str]) -> None:
    if node.item.id in visited:
        return
    visited.add(node.item.id)

    item = node.item
    author = item.author_name or item.author_id or "unknown"
    time_str = item.timestamp.strftime("%H:%M")
    indent = "  " * depth
    cont_indent = indent + "  "
    prefix = ""
    reply_to = item.extra.get("reply_to_username")
    if reply_to and depth > 0:
        prefix = f"> 回覆 @{reply_to} "

    text_lines = (item.text or "").strip().splitlines() or [""]
    head, *rest = text_lines
    lines.append(f"{indent}- @{author} · {time_str}：{prefix}{head}")
    for extra_line in rest:
        lines.append(f"{cont_indent}{extra_line}" if extra_line else "")

    for child in node.children:
        _render_reply(child, depth=depth + 1, lines=lines, visited=visited)
