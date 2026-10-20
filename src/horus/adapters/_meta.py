"""Shared Meta platform (Facebook / Instagram / Threads) media helpers."""

from typing import Any

_MEDIA_TYPE_MAP = {
    1: "IMAGE",
    2: "VIDEO",
    8: "CAROUSEL_ALBUM",
    19: "TEXT_POST",
}


def _extract_media_urls(post_data: dict[str, Any]) -> list[str]:
    """Extract media URLs from image_versions2 or carousel_media."""
    urls: list[str] = []

    # Single image/video
    image_versions = post_data.get("image_versions2", {})
    if image_versions:
        candidates = image_versions.get("candidates", [])
        if candidates:
            urls.append(candidates[0].get("url", ""))

    # Video
    video_versions = post_data.get("video_versions", [])
    if video_versions:
        urls.append(video_versions[0].get("url", ""))

    # Carousel replaces single image
    carousel = post_data.get("carousel_media")
    if carousel:
        urls.clear()
        for item in carousel:
            img = item.get("image_versions2", {})
            candidates = img.get("candidates", [])
            if candidates:
                urls.append(candidates[0].get("url", ""))

    return [u for u in urls if u]
