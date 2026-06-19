"""Pure metrics for comparing two ranked SERP URL lists.

Used to validate that `serper` results approximate the browser's Google results.
overlap_at_k = set overlap (coverage); rbo = top-weighted rank similarity.
"""

from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

# utm_* is a real family prefix; the rest are exact keys (so we don't drop
# meaningful params like `reference`/`refresh` that merely start with "ref").
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = frozenset({"gclid", "fbclid", "ref", "ref_src"})


def normalize_url(url: str) -> str:
    """Canonicalize for comparison: lowercase host, drop www/scheme-diff/tracking/trailing slash.

    Also unwraps Google redirect links (google.com/url?q=<target>), which manual
    browser SERP collection frequently produces — without this, every redirect
    URL would falsely mismatch the real target.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Unwrap Google redirect wrappers before anything else
    if host.endswith("google.com") and parts.path == "/url":
        qs = parse_qs(parts.query)
        target = (qs.get("q") or qs.get("url") or [None])[0]
        if target:
            return normalize_url(unquote(target))
    path = parts.path.rstrip("/")
    # drop tracking query params, keep meaningful ones, sorted for stability
    kept = []
    for pair in parts.query.split("&"):
        if not pair:
            continue
        key = pair.split("=", 1)[0]
        if key in _TRACKING_EXACT or any(key.startswith(p) for p in _TRACKING_PREFIXES):
            continue
        kept.append(pair)
    query = "&".join(sorted(kept))
    # urlunsplit prepends "//" when netloc is set but scheme is empty (RFC 3986
    # authority delimiter); strip it so the canonical form is host/path?query.
    return urlunsplit(("", host, path, query, "")).removeprefix("//")


def overlap_at_k(list1: list[str], list2: list[str], k: int = 10) -> float:
    """Fraction of top-k items of list1 whose normalized URL also appears in top-k of list2."""
    if k <= 0:
        return 0.0
    s1 = [normalize_url(u) for u in list1[:k]]
    s2 = {normalize_url(u) for u in list2[:k]}
    if not s1:
        return 0.0
    return sum(1 for u in s1 if u in s2) / len(s1)


def rbo(list1: list[str], list2: list[str], p: float = 0.9) -> float:
    """Rank-Biased Overlap in [0, 1]. Top-weighted; p=0.9 ≈ top-10 emphasis.

    Note: for identical *finite* lists of length n this is < 1 (truncation), a
    known RBO property. Use it for *relative* comparison, not an absolute pass mark.
    """
    n1 = [normalize_url(u) for u in list1]
    n2 = [normalize_url(u) for u in list2]
    depth = max(len(n1), len(n2))
    if depth == 0:
        return 1.0
    seen1: set[str] = set()
    seen2: set[str] = set()
    score = 0.0
    for d in range(depth):
        if d < len(n1):
            seen1.add(n1[d])
        if d < len(n2):
            seen2.add(n2[d])
        agreement = len(seen1 & seen2) / (d + 1)
        score += (p**d) * agreement
    return (1 - p) * score
