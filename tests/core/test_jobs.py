"""core.jobs: UI-agnostic background crawl jobs shared by serve and mcp."""

from horus.core.jobs import CrawlJob, JobStatus, strip_markup
from horus.core.orchestrator import CrawlOutcome


def test_strip_markup_removes_rich_tags() -> None:
    assert strip_markup("Crawling [cyan]https://e.com[/cyan]...") == "Crawling https://e.com..."


def test_strip_markup_falls_back_on_broken_markup() -> None:
    raw = "bad [/oops] closing tag"
    assert strip_markup(raw) == raw


def test_counts_read_live_from_outcome() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={"url": "https://e.com"})
    job.outcome.total_found = 3
    job.outcome.total_new = 2
    assert (job.items_found, job.items_new) == (3, 2)


def test_to_dict_matches_serve_fields() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={"url": "u"}, outcome=CrawlOutcome(5, 4))
    job.log.extend(f"line {i}" for i in range(25))
    d = job.to_dict()
    assert set(d) == {
        "job_id",
        "site",
        "kwargs",
        "status",
        "started_at",
        "finished_at",
        "items_found",
        "items_new",
        "error",
        "log",
    }
    assert d["status"] == JobStatus.PENDING
    assert d["items_found"] == 5 and d["items_new"] == 4
    assert d["log"] == [f"line {i}" for i in range(5, 25)]


def test_finished_flag() -> None:
    job = CrawlJob(job_id="j1", site="web", kwargs={})
    assert job.finished is False
    for status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
        job.status = status
        assert job.finished is True
