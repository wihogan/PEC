"""
fetchers/factset_eps.py - S&P 500 forward 12-month (NTM) EPS from FactSet Earnings Insight.

Design (docs/eps-sources.md, "Key finding" and "(1) Primary"):
    The weekly FactSet Earnings Insight PDF does NOT print the forward EPS dollar
    figure in its text layer. It prints, in text, both
        "The forward 12-month P/E ratio for the S&P 500 is 17.1."
        "The bottom-up target price for the S&P 500 is 9,099.54, which is 26.8%
         above the closing price of 7,175.83."
    so   EPS = closing price / forward 12M P/E   (accurate to about +/-0.3 percent
    because the P/E is printed to one decimal).

Flow
----
1. GET https://www.factset.com/earningsinsight (302 -> latest PDF); record the final URL.
2. Validate HTTP status, content-type and the %PDF magic.
3. Extract text with pypdf, normalise whitespace, apply STRICT regexes (fail closed:
   no loose "$ anywhere" matching).
4. Range checks (P/E 10-40, price 2000-20000, EPS 200-600) and a jump check against
   the last good value (8 percent) unless the manual override confirms the new value.
5. Cache the result (.cache/factset_eps_latest.json). A result fetched on the
   current Pacific calendar day is reused: FactSet only updates weekly.

Returns {"value", "as_of", "source": "factset", "pe", "price", "url"} or raises FetchError.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .base import FetchError

log = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")

_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_FACTSET_CACHE = _CACHE_DIR / "factset_eps_latest.json"

FACTSET_PDF_URL = "https://www.factset.com/earningsinsight"

PE_RANGE = (10.0, 40.0)
PRICE_RANGE = (2000.0, 20000.0)
EPS_RANGE = (200.0, 600.0)
MAX_JUMP = 0.08          # |EPS - last good| / last good
OVERRIDE_CONFIRM = 0.02  # a value within 2 percent of the owner's override is accepted regardless of jump
_MAX_PAGES = 20
_MAX_PDF_BYTES = 40 * 1024 * 1024

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
}

# Strict patterns (applied to whitespace-normalised text).
_PE_RE = re.compile(
    r"forward 12-month P/E ratio for the S&P 500 is (\d{1,2}\.\d)(?![\d])",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"bottom-up target price for the S&P 500 is [\d,]+(?:\.\d+)?, "
    r"which is \d{1,3}(?:\.\d+)?% (?:above|below) the closing price of ([\d,]+\.\d{2})",
    re.IGNORECASE,
)
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_TEXT_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s+(20\d{{2}})\b")
_FILE_DATE_RE = re.compile(r"EarningsInsight_(\d{2})(\d{2})(\d{2})[A-Z]?\.pdf", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _pt_today() -> date:
    return datetime.now(PT).date()


def _load_cache() -> Optional[dict]:
    try:
        with open(_FACTSET_CACHE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "value" in data and "as_of" in data:
            return data
    except Exception:
        pass
    return None


def _save_cache(result: dict, today: date) -> None:
    payload = dict(result)
    payload["fetched_at"] = datetime.now(PT).isoformat(timespec="seconds")
    payload["fetched_pt_date"] = today.isoformat()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_FACTSET_CACHE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    log.info("[factset_eps] Cached: $%.2f as_of=%s", result["value"], result["as_of"])


# ---------------------------------------------------------------------------
# Download and parse
# ---------------------------------------------------------------------------
def _download_latest() -> tuple[bytes, str]:
    """Follow the earningsinsight redirect; return (pdf_bytes, final_url)."""
    try:
        resp = requests.get(FACTSET_PDF_URL, headers=_HEADERS, timeout=60, allow_redirects=True)
    except requests.RequestException as exc:
        raise FetchError(f"FactSet download failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise FetchError(f"FactSet download returned HTTP {resp.status_code} at {resp.url}")
    ctype = resp.headers.get("Content-Type", "").lower()
    body = resp.content
    if body[:4] != b"%PDF":
        raise FetchError(
            f"FactSet URL did not return a PDF (content-type {ctype!r}, "
            f"first bytes {body[:8]!r}, final url {resp.url})"
        )
    if "pdf" not in ctype and "octet-stream" not in ctype:
        raise FetchError(f"FactSet response has unexpected content-type {ctype!r} at {resp.url}")
    if len(body) > _MAX_PDF_BYTES:
        raise FetchError(f"FactSet PDF unexpectedly large ({len(body)} bytes)")
    return body, resp.url


def _extract_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise FetchError("pypdf is not installed") from exc
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = [(p.extract_text() or "") for p in reader.pages[:_MAX_PAGES]]
    except Exception as exc:
        raise FetchError(f"PDF text extraction failed: {type(exc).__name__}: {exc}") from exc
    return re.sub(r"\s+", " ", " ".join(parts))


def _file_date(url: str) -> Optional[date]:
    m = _FILE_DATE_RE.search(url.rsplit("/", 1)[-1].split("?")[0])
    if not m:
        return None
    try:
        return date(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _text_date(text: str) -> Optional[date]:
    """First 'Month D, YYYY' in the first part of the report (the issue date line)."""
    m = _TEXT_DATE_RE.search(text[:3000])
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    except ValueError:
        return None


def parse_report(text: str, url: str = "") -> dict:
    """
    Parse normalised report text. Returns {"value","as_of","pe","price"}.
    Raises FetchError with a precise reason on any miss or inconsistency.
    """
    pes = {m.group(1) for m in _PE_RE.finditer(text)}
    if not pes:
        raise FetchError("forward 12-month P/E sentence not found in FactSet PDF text")
    if len(pes) > 1:
        raise FetchError(f"inconsistent forward 12-month P/E values in PDF: {sorted(pes)}")
    pe = float(next(iter(pes)))

    prices = {m.group(1) for m in _PRICE_RE.finditer(text)}
    if not prices:
        raise FetchError("S&P 500 closing-price sentence not found in FactSet PDF text")
    if len(prices) > 1:
        raise FetchError(f"inconsistent closing prices in PDF: {sorted(prices)}")
    price = float(next(iter(prices)).replace(",", ""))

    if not PE_RANGE[0] <= pe <= PE_RANGE[1]:
        raise FetchError(f"forward P/E {pe} outside {PE_RANGE}")
    if not PRICE_RANGE[0] <= price <= PRICE_RANGE[1]:
        raise FetchError(f"S&P 500 price {price} outside {PRICE_RANGE}")

    eps = round(price / pe, 2)
    if not EPS_RANGE[0] <= eps <= EPS_RANGE[1]:
        raise FetchError(f"derived EPS {eps} (price {price} / P/E {pe}) outside {EPS_RANGE}")

    f_date = _file_date(url)
    t_date = _text_date(text)
    as_of = f_date or t_date
    if as_of is None:
        raise FetchError("report date not found (neither file name nor first-page date line)")
    if f_date and t_date and f_date != t_date:
        log.warning("[factset_eps] file-name date %s differs from text date %s; using file-name date",
                    f_date, t_date)
    return {"value": eps, "as_of": as_of.isoformat(), "pe": pe, "price": price}


def _check_jump(eps: float, last_good: Optional[float], override: Optional[float]) -> None:
    if last_good is None or last_good <= 0:
        return
    change = abs(eps - last_good) / last_good
    if change <= MAX_JUMP:
        return
    if override and abs(eps - override) / override <= OVERRIDE_CONFIRM:
        return
    raise FetchError(
        f"big jump: derived EPS {eps} is {change:.1%} away from last good {last_good} "
        f"(limit {MAX_JUMP:.0%}) and not confirmed by the manual override ({override}); "
        "keeping previous value"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def fetch_forward_eps(
    last_good: Optional[float] = None,
    override: Optional[float] = None,
    use_cache: bool = True,
) -> dict:
    """
    Return {"value","as_of","source":"factset","pe","price","url"} or raise FetchError.

    A cached result fetched on today's Pacific date is returned without a network call.
    """
    today = _pt_today()
    if use_cache:
        cached = _load_cache()
        if cached and cached.get("fetched_pt_date") == today.isoformat():
            log.info("[factset_eps] Reusing today's cached result: $%.2f as_of=%s",
                     cached["value"], cached["as_of"])
            return {k: cached.get(k) for k in ("value", "as_of", "source", "pe", "price", "url")}

    log.info("[factset_eps] Fetching latest FactSet Earnings Insight PDF")
    pdf_bytes, final_url = _download_latest()
    log.info("[factset_eps] Final URL: %s (%d bytes)", final_url, len(pdf_bytes))
    parsed = parse_report(_extract_text(pdf_bytes), final_url)
    _check_jump(parsed["value"], last_good, override)

    result = {
        "value": parsed["value"],
        "as_of": parsed["as_of"],
        "source": "factset",
        "pe": parsed["pe"],
        "price": parsed["price"],
        "url": final_url,
    }
    _save_cache(result, today)
    log.info("[factset_eps] EPS %.2f = %.2f / %.1f (as of %s)",
             result["value"], result["price"], result["pe"], result["as_of"])
    return result
