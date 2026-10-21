from horus.mcp.tools_crawl import build_adapter_kwargs


def test_omits_none_and_stringifies() -> None:
    kw = build_adapter_kwargs(
        user="nasa", url=None, query=None, mode=None, with_comments=False, gl=None, hl=None
    )
    assert kw == {"user": "nasa"}


def test_with_comments_true_becomes_string_true() -> None:
    kw = build_adapter_kwargs(
        user="nasa", url=None, query=None, mode="posts", with_comments=True, gl=None, hl=None
    )
    assert kw == {"user": "nasa", "mode": "posts", "with_comments": "true"}


def test_serper_locale() -> None:
    kw = build_adapter_kwargs(
        user=None, url=None, query="fastapi", mode=None, with_comments=False, gl="tw", hl="zh-tw"
    )
    assert kw == {"query": "fastapi", "gl": "tw", "hl": "zh-tw"}
