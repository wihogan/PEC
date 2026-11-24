"""fetchers/factset_eps.py: FactSet Earnings Insight forward EPS (price / forward 12M P/E).

Offline: report text is synthesised from the sentence patterns quoted in
docs/eps-sources.md and docs/eps-backfill.md; PDFs are tiny hand-built files read by
the real pypdf; requests.get is replaced by a fake.
"""
import io
import json
import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from fetchers import factset_eps as fe
from fetchers.base import FetchError

PT = ZoneInfo("America/Los_Angeles")
BASE = "https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/"


def report_text(date_line="November 20, 2026", pe="17.1", price="7,175.83", target="9,099.54",
                pct="26.8", direction="above", extra=""):
    """Normalised text with the two sentences as FactSet prints them (P/E repeated on p.1, 7, 13)."""
    pe_s = f"The forward 12-month P/E ratio for the S&P 500 is {pe}. This P/E ratio is below the 5-year average (19.9)."
    price_s = (f"The bottom-up target price for the S&P 500 is {target}, which is {pct}% {direction} "
               f"the closing price of {price}.")
    return (f"Earnings Insight John Butters, VP {date_line} Key Metrics {pe_s} Topic of the week ... "
            f"{price_s} Forward Estimates & Valuation {pe_s} ... Targets & Ratings {pe_s} {extra}")


# Backfill reports (docs/eps-backfill.md): file, page-1 date line, price as printed, P/E, EPS
REPORTS = [
    ("EarningsInsight_052926A.pdf", "May 29, 2026", "7563.63", "21.2", 356.78, "2026-05-29"),
    ("EarningsInsight_062626.pdf", "June 26, 2026", "7,357.49", "20.1", 366.04, "2026-06-26"),
    ("EarningsInsight_073126.pdf", "July 31, 2026", "7437.63", "19.6", 379.47, "2026-07-31"),
    ("EarningsInsight_082826.pdf", "August 28, 2026", "7730.99", "19.6", 394.44, "2026-08-28"),
    ("EarningsInsight_092526.pdf", "September 25, 2026", "7,704.13", "19.2", 401.26, "2026-09-25"),
    ("EarningsInsight_103026.pdf?AccountID=13354688", "October 30, 2026", "7,720.39", "18.7", 412.86, "2026-10-30"),
    ("EarningsInsight_112026A.pdf", "November 20, 2026", "7,175.83", "17.1", 419.64, "2026-11-20"),
    ("EarningsInsight_052126.pdf", "May 21, 2026", "7,400.00", "21.0", 352.38, "2026-05-21"),  # Thursday issue
]


# ---------------------------------------------------------------------------
# parse_report
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fname, date_line, price, pe, eps, as_of", REPORTS)
def test_parse_report_backfill_reports(fname, date_line, price, pe, eps, as_of):
    out = fe.parse_report(report_text(date_line, pe=pe, price=price), BASE + fname)
    assert out == {"value": eps, "as_of": as_of, "pe": float(pe), "price": float(price.replace(",", ""))}


def test_parse_report_below_direction_and_case():
    t = report_text(direction="below", pct="3.1").upper().replace("S&P", "S&P")
    out = fe.parse_report(t, "EarningsInsight_112026A.pdf")
    assert out["value"] == 419.64


def test_parse_report_text_date_when_no_file_name():
    out = fe.parse_report(report_text("November 13, 2026"), "")
    assert out["as_of"] == "2026-11-13"


def test_parse_report_file_date_wins_on_mismatch(caplog):
    with caplog.at_level(logging.WARNING, logger=fe.log.name):
        out = fe.parse_report(report_text("November 19, 2026"), BASE + "EarningsInsight_112026A.pdf")
    assert out["as_of"] == "2026-11-20"
    assert "differs from text date 2026-11-19" in caplog.text


def test_parse_report_invalid_file_date_falls_back_to_text():
    out = fe.parse_report(report_text("November 20, 2026"), "EarningsInsight_023026.pdf")  # Feb 30
    assert out["as_of"] == "2026-11-20"


def test_parse_report_no_date_anywhere():
    t = report_text(date_line="")
    with pytest.raises(FetchError, match="report date not found"):
        fe.parse_report(t, "https://www.factset.com/earningsinsight")


def test_parse_report_text_date_only_in_first_3000_chars():
    t = report_text(date_line="") + " " * 3000 + "December 4, 2026"
    with pytest.raises(FetchError, match="report date not found"):
        fe.parse_report(t, "")


def test_parse_report_duplicate_identical_pe_is_fine():
    t = report_text()
    assert t.count("P/E ratio for the S&P 500 is 17.1") == 3
    assert fe.parse_report(t, "EarningsInsight_112026A.pdf")["pe"] == 17.1


def test_parse_report_disagreeing_pe():
    t = report_text(extra="The forward 12-month P/E ratio for the S&P 500 is 17.3.")
    with pytest.raises(FetchError, match=r"inconsistent forward 12-month P/E values.*17\.1.*17\.3"):
        fe.parse_report(t, "EarningsInsight_112026A.pdf")


def test_parse_report_disagreeing_price():
    extra = ("The bottom-up target price for the S&P 500 is 9,099.54, which is 26.8% above "
             "the closing price of 7,180.00.")
    with pytest.raises(FetchError, match="inconsistent closing prices"):
        fe.parse_report(report_text(extra=extra), "EarningsInsight_112026A.pdf")


def test_parse_report_same_price_with_and_without_comma_is_inconsistent():
    """Strings are compared before comma stripping: '7,175.83' vs '7175.83' count as two values
    (fails closed)."""
    extra = ("The bottom-up target price for the S&P 500 is 9,099.54, which is 26.8% above "
             "the closing price of 7175.83.")
    with pytest.raises(FetchError, match="inconsistent closing prices"):
        fe.parse_report(report_text(extra=extra), "EarningsInsight_112026A.pdf")


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda t: t.replace("forward 12-month P/E ratio", "forward 12-month PE ratio"), "P/E sentence not found"),
        (lambda t: t.replace("closing price of", "close of"), "closing-price sentence not found"),
        (lambda t: t.replace("is 17.1.", "is 17.15."), "P/E sentence not found"),        # 2 decimals: fail closed
        (lambda t: t.replace("is 17.1.", "is 17."), "P/E sentence not found"),
        (lambda t: t.replace("7,175.83", "7,175.8"), "closing-price sentence not found"),  # needs 2 decimals
        (lambda t: "", "P/E sentence not found"),
    ],
)
def test_parse_report_missing_or_malformed_sentences(mutate, match):
    with pytest.raises(FetchError, match=match):
        fe.parse_report(mutate(report_text()), "EarningsInsight_112026A.pdf")


@pytest.mark.parametrize(
    "pe, price, match",
    [
        ("9.9", "7,175.83", "forward P/E 9.9 outside"),
        ("40.1", "7,175.83", "forward P/E 40.1 outside"),
        ("17.1", "1,999.99", "price 1999.99 outside"),
        ("17.1", "20,000.01", "price 20000.01 outside"),
        ("10.0", "19,000.00", "derived EPS 1900.0"),       # EPS > 600
        ("39.0", "2,500.00", "derived EPS 64.1"),          # EPS < 200
    ],
)
def test_parse_report_range_checks(pe, price, match):
    with pytest.raises(FetchError, match=match):
        fe.parse_report(report_text(pe=pe, price=price), "EarningsInsight_112026A.pdf")


@pytest.mark.parametrize("pe, price", [("10.0", "2,000.00"), ("40.0", "20,000.00")])
def test_parse_report_range_edges_inclusive(pe, price):
    out = fe.parse_report(report_text(pe=pe, price=price), "EarningsInsight_112026A.pdf")
    assert 200 <= out["value"] <= 600


# ---------------------------------------------------------------------------
# Jump / override guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "eps, last_good, override",
    [
        (419.64, None, None),
        (419.64, 0.0, None),
        (432.0, 400.0, None),      # exactly +8 %
        (368.0, 400.0, None),      # exactly -8 %
        (419.64, 354.84, 419.64),  # +18 % jump confirmed by override (0 %)
        (419.64, 354.84, 411.5),   # within 2 % of override
    ],
)
def test_jump_guard_accepts(eps, last_good, override):
    fe._check_jump(eps, last_good, override)


@pytest.mark.parametrize(
    "eps, last_good, override",
    [
        (432.5, 400.0, None),
        (367.5, 400.0, None),
        (419.64, 354.84, None),
        (419.64, 354.84, 354.84),
        (419.64, 354.84, 0.0),
        (419.64, 354.84, 410.0),   # 2.35 % from override
    ],
)
def test_jump_guard_rejects(eps, last_good, override):
    with pytest.raises(FetchError, match="big jump"):
        fe._check_jump(eps, last_good, override)


# ---------------------------------------------------------------------------
# Download, PDF extraction and the once-per-PT-day cache
# ---------------------------------------------------------------------------
def make_pdf(lines):
    """Minimal one-page PDF (Helvetica text) that pypdf can extract."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 9 Tf 40 780 Td 12 TL " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


NOV20_LINES = [
    "Earnings Insight",
    "November 20, 2026",
    "The forward 12-month P/E ratio for the S&P 500 is 17.1. This P/E ratio is below",
    "the 5-year average (19.9).",
    "The bottom-up target price for the S&P 500 is 9,099.54, which is 26.8% above",
    "the closing price of 7,175.83.",
]
FINAL_URL = BASE + "EarningsInsight_112026A.pdf"


class FakeGet:
    def __init__(self, body=None, status=200, ctype="application/pdf", url=FINAL_URL, exc=None):
        self.body = make_pdf(NOV20_LINES) if body is None else body
        self.status, self.ctype, self.url, self.exc = status, ctype, url, exc
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return SimpleNamespace(status_code=self.status, headers={"Content-Type": self.ctype},
                               content=self.body, url=self.url)


@pytest.fixture
def pt_clock(freeze):
    def set_(y, mo, d, h=12, mi=0):
        freeze(datetime(y, mo, d, h, mi, tzinfo=PT), module=fe)
    set_(2026, 11, 23, 13, 20)
    return set_


@pytest.fixture
def fake_get(monkeypatch):
    g = FakeGet()
    monkeypatch.setattr(fe.requests, "get", g)
    return g


def test_extract_text_from_real_pdf():
    t = fe._extract_text(make_pdf(NOV20_LINES))
    assert "P/E ratio for the S&P 500 is 17.1." in t
    assert "  " not in t and "\n" not in t


def test_extract_text_garbage_raises():
    with pytest.raises(FetchError, match="PDF text extraction failed"):
        fe._extract_text(b"%PDF-1.4 not really a pdf")


def test_fetch_forward_eps_end_to_end(pt_clock, fake_get):
    out = fe.fetch_forward_eps(last_good=412.86, override=419.64)
    assert out == {"value": 419.64, "as_of": "2026-11-20", "source": "factset", "pe": 17.1,
                   "price": 7175.83, "url": FINAL_URL}
    assert fake_get.calls[0][0] == "https://www.factset.com/earningsinsight"
    assert fake_get.calls[0][1]["allow_redirects"] is True
    cache = json.loads(fe._FACTSET_CACHE.read_text())
    assert cache["fetched_pt_date"] == "2026-11-23" and cache["value"] == 419.64


def test_cache_reused_same_pt_day(pt_clock, fake_get):
    fe.fetch_forward_eps()
    pt_clock(2026, 11, 23, 23, 59)
    out = fe.fetch_forward_eps()
    assert len(fake_get.calls) == 1
    assert out["value"] == 419.64 and out["source"] == "factset" and out["url"] == FINAL_URL


def test_cache_expires_next_pt_day(pt_clock, fake_get):
    fe.fetch_forward_eps()
    pt_clock(2026, 11, 24, 0, 1)
    fe.fetch_forward_eps()
    assert len(fake_get.calls) == 2


def test_cache_day_is_pacific_not_utc(freeze, fake_get):
    freeze(datetime(2026, 11, 23, 23, 0, tzinfo=PT), module=fe)
    fe.fetch_forward_eps()
    freeze(datetime(2026, 11, 24, 7, 30, tzinfo=ZoneInfo("UTC")), module=fe)   # still Nov 23 PT
    fe.fetch_forward_eps()
    assert len(fake_get.calls) == 1


def test_use_cache_false_always_downloads(pt_clock, fake_get):
    fe.fetch_forward_eps()
    fe.fetch_forward_eps(use_cache=False)
    assert len(fake_get.calls) == 2


def test_cached_result_skips_jump_check(pt_clock, fake_get):
    """Documented behaviour: a result cached today is returned as-is; the jump guard runs only on download."""
    fe.fetch_forward_eps()
    out = fe.fetch_forward_eps(last_good=300.0, override=None)
    assert out["value"] == 419.64


def test_jump_rejection_raises_and_does_not_cache(pt_clock, fake_get):
    with pytest.raises(FetchError, match="big jump"):
        fe.fetch_forward_eps(last_good=354.84, override=354.84)
    assert not fe._FACTSET_CACHE.exists()


def test_malformed_cache_ignored(pt_clock, fake_get):
    fe._FACTSET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    fe._FACTSET_CACHE.write_text("{broken")
    assert fe.fetch_forward_eps()["value"] == 419.64
    assert len(fake_get.calls) == 1


def test_stale_cache_from_previous_day_not_used_on_failure(pt_clock, monkeypatch):
    """A cache from an earlier day is not a fallback: a failed download raises."""
    monkeypatch.setattr(fe.requests, "get", FakeGet())
    fe.fetch_forward_eps()
    pt_clock(2026, 11, 24, 9, 0)
    monkeypatch.setattr(fe.requests, "get", FakeGet(exc=requests.ConnectionError("down")))
    with pytest.raises(FetchError, match="download failed"):
        fe.fetch_forward_eps()


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"exc": requests.Timeout("slow")}, "download failed: Timeout"),
        ({"status": 404}, "HTTP 404"),
        ({"body": b"<!DOCTYPE html><html>", "ctype": "text/html"}, "did not return a PDF"),
        ({"ctype": "text/html; charset=utf-8"}, "unexpected content-type"),
    ],
)
def test_download_failures(pt_clock, monkeypatch, kwargs, match):
    monkeypatch.setattr(fe.requests, "get", FakeGet(**kwargs))
    with pytest.raises(FetchError, match=match):
        fe.fetch_forward_eps()
    assert not fe._FACTSET_CACHE.exists()


def test_download_octet_stream_accepted(pt_clock, monkeypatch):
    monkeypatch.setattr(fe.requests, "get", FakeGet(ctype="application/octet-stream"))
    assert fe.fetch_forward_eps()["value"] == 419.64


def test_download_size_cap(pt_clock, monkeypatch):
    monkeypatch.setattr(fe.requests, "get", FakeGet())
    monkeypatch.setattr(fe, "_MAX_PDF_BYTES", 100)
    with pytest.raises(FetchError, match="unexpectedly large"):
        fe.fetch_forward_eps()


def test_pdf_without_sentences_fails_closed(pt_clock, monkeypatch):
    monkeypatch.setattr(fe.requests, "get", FakeGet(body=make_pdf(["Earnings Insight", "November 20, 2026",
                                                                   "Forward EPS $419.64"])))
    with pytest.raises(FetchError, match="P/E sentence not found"):
        fe.fetch_forward_eps()
