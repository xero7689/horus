import json

import httpx
import pytest

from horus.adapters.serper import SerperAdapter

_FIXTURE = {
    "searchParameters": {"q": "horus crawler", "gl": "tw", "hl": "zh-tw", "num": 10},
    "organic": [
        {
            "title": "Horus A",
            "link": "https://a.example/x",
            "snippet": "sa",
            "date": "2025年3月5日",
            "position": 1,
        },
        {"title": "Horus B", "link": "https://b.example/y", "snippet": "sb", "position": 2},
    ],
    "credits": 1,
}


def test_parse_basic_response():
    adapter = SerperAdapter()
    items = adapter.parse_response_json(_FIXTURE, query="horus crawler", gl="tw", hl="zh-tw")
    assert len(items) == 2
    first = items[0]
    assert first.site_id == "serper"
    assert first.url == "https://a.example/x"
    assert first.text == "Horus A"
    assert first.extra["query"] == "horus crawler"
    assert first.extra["rank"] == 1
    assert first.extra["snippet"] == "sa"
    assert first.extra["date"] == "2025年3月5日"
    assert first.extra["gl"] == "tw"
    assert first.extra["hl"] == "zh-tw"


def test_parse_skips_items_without_link_or_title():
    adapter = SerperAdapter()
    body = {"organic": [{"title": "no link", "position": 1}, {"link": "https://x", "position": 2}]}
    items = adapter.parse_response_json(body, query="q", gl="tw", hl="zh-tw")
    assert items == []


def test_parse_zero_results():
    adapter = SerperAdapter()
    items = adapter.parse_response_json({"credits": 1}, query="q", gl="tw", hl="zh-tw")
    assert items == []


def test_serper_registered():
    from horus.adapters import get_adapter

    assert get_adapter("serper").site_id == "serper"


def _ok_handler(n_results=10):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "k"
        payload = json.loads(request.content)
        organic = [
            {"title": f"t{i}", "link": f"https://e/{i}", "snippet": f"s{i}", "position": i}
            for i in range(1, payload.get("num", 10) + 1)
        ][:n_results]
        return httpx.Response(200, json={"organic": organic, "credits": 1})

    return handler


@pytest.mark.asyncio
async def test_fetch_items_happy(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    items = await adapter.fetch_items(query="horus", limit=5)
    assert len(items) == 5
    assert items[0].url == "https://e/1"
    assert items[0].extra["gl"] == "tw"  # default locale applied


@pytest.mark.asyncio
async def test_fetch_items_missing_key_raises(monkeypatch):
    monkeypatch.delenv("HORUS_SERPER_API_KEY", raising=False)
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    with pytest.raises(ValueError, match="HORUS_SERPER_API_KEY"):
        await adapter.fetch_items(query="horus")


@pytest.mark.asyncio
async def test_fetch_items_requires_query(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    adapter = SerperAdapter(transport=httpx.MockTransport(_ok_handler()))
    with pytest.raises(ValueError, match="--query"):
        await adapter.fetch_items()


@pytest.mark.asyncio
async def test_limit_zero_requests_serper_max(monkeypatch):
    # CLI documents --limit 0 = unlimited; for Serper that means the 100 hard cap, not 10
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    seen: dict[str, int] = {}

    def handler(request):
        seen["num"] = json.loads(request.content)["num"]
        return httpx.Response(200, json={"organic": [], "credits": 1})

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    await adapter.fetch_items(query="q", limit=0)
    assert seen["num"] == 100


@pytest.mark.asyncio
async def test_401_raises_no_retry(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"message": "Unauthorized"})

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="Invalid"):
        await adapter.fetch_items(query="q")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_5xx_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setenv("HORUS_SERPER_API_KEY", "k")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"organic": [{"title": "t", "link": "https://e/1", "position": 1}], "credits": 1},
        )

    adapter = SerperAdapter(transport=httpx.MockTransport(handler))
    items = await adapter.fetch_items(query="q", limit=1)
    assert len(items) == 1
    assert calls["n"] == 2
