from horus.serp_eval import normalize_url, overlap_at_k, rbo


def test_normalize_collapses_host_scheme_slash_and_drops_tracking():
    # www / scheme / trailing-slash differences collapse; tracking params dropped
    assert normalize_url("https://www.Example.com/Path/?utm_source=x") == normalize_url(
        "http://example.com/Path"
    )


def test_normalize_keeps_meaningful_query_params():
    # non-tracking params are identity-bearing (e.g. youtube watch?v=) and kept
    assert normalize_url("https://example.com/p?id=1&utm_source=x") == "example.com/p?id=1"


def test_normalize_unwraps_google_redirect():
    # manual browser SERP copies often yield google redirect wrappers
    assert (
        normalize_url("https://www.google.com/url?q=https%3A%2F%2Ftarget.com%2Fa&sa=U")
        == "target.com/a"
    )


def test_overlap_at_k_counts_shared_normalized_urls():
    a = ["https://x.com/1", "https://y.com/2", "https://z.com/3"]
    b = ["http://www.x.com/1/", "https://q.com/9", "https://z.com/3"]
    assert overlap_at_k(a, b, k=3) == 2 / 3  # x and z shared, normalized


def test_rbo_identical_is_high_and_disjoint_is_zero():
    a = [f"https://e/{i}" for i in range(10)]
    assert rbo(a, a) > 0.6  # finite-truncation: identical 10-lists ~0.65 at p=0.9
    b = [f"https://other/{i}" for i in range(10)]
    assert rbo(a, b) == 0.0


def test_rbo_top_swaps_hurt_more_than_tail_swaps():
    a = [f"https://e/{i}" for i in range(10)]
    swap_top = a.copy()
    swap_top[0], swap_top[1] = swap_top[1], swap_top[0]
    swap_tail = a.copy()
    swap_tail[8], swap_tail[9] = swap_tail[9], swap_tail[8]
    assert rbo(a, swap_tail) > rbo(a, swap_top)  # tail swaps matter less (top-weighted)
