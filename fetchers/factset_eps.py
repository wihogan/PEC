"""
fetchers/factset_eps.py — FactSet Earnings Insight forward EPS fetcher.

Fetches the S&P 500 NTM forward operating EPS from the publicly available
FactSet Earnings Insight PDF (no authentication required).

Fallback chain
--------------
1. Download this week's Earnings Insight PDF from factset.com
2. Parse the "forward 12-month EPS estimate" value from PDF text
3. If parse fails or PDF unavailable → return cached value (7-day TTL)
4. Caller (spglobal.py) ultimately falls back to manual_override

Scheduling
----------
Run weekly, ideally Friday evening after FactSet publishes (usually ~6pm ET).
The 7-day cache means daily refresh.py runs re-use the last successful fetch
without hitting FactSet again until the cache expires.

Sources / methodology
---------------------
FactSet's weekly Earnings Insight defines "forward 12-month EPS" as the
bottom-up aggregation of median analyst EPS estimates for all S&P 500
constituents across the next four rolling quarters. This is the most
authoritative free public index-level forward EPS source.

Yardeni cross-check
-------------------
As a sanity check, fetch the Yardeni forward EPS chart page and extract the
printed legend value. Yardeni's methodology is slightly different (time-weighted
average of current-year and next-year estimates) so expect a $1–5 difference
vs FactSet — that spread is normal.
"""

from __future__ import annotations

import io
import json
import logging
import re
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)
_FACTSET_CACHE = _CACHE_DIR / "factset_eps_latest.json"
_CACHE_TTL_DAYS = 7  # re-fetch after 7 days

# ---------------------------------------------------------------------------
# FactSet Earnings Insight PDF endpoint
# ---------------------------------------------------------------------------
FACTSET_PDF_URL = "https://www.factset.com/earningsinsight"

# Patterns to find forward EPS in the PDF text.
# FactSet uses various phrasings across different weekly issues.
_EPS_PATTERNS = [
    # e.g. "forward 12-month EPS estimate of $261.13"
    r"forward\s+12[\-\s]month\s+EPS\s+estimate\s+(?:of\s+)?\$?\s*([\d,]+\.?\d*)",
    # e.g. "forward 12-month EPS of $261.13"
    r"forward\s+12[\-\s]month\s+EPS\s+(?:of\s+)?\$?\s*([\d,]+\.?\d*)",
    # e.g. "NTM EPS estimate: $261.13"
    r"NTM\s+EPS\s+estimate[:\s]+\$?\s*([\d,]+\.?\d*)",
    # e.g. "bottom-up EPS estimate of $261.13 for the next 12 months"
    r"bottom[\-\s]up\s+EPS\s+estimate\s+of\s+\$?\s*([\d,]+\.?\d*)",
    # Fallback: any dollar figure that looks like an S&P EPS (150–600 range)
    # after the phrase "forward" or "NTM"
    r"(?:forward|NTM)[^$\n]{0,60}\$\s*((?:1[5-9]\d|[2-5]\d{2})(?:\.\d{1,2})?)",
]

# ---------------------------------------------------------------------------
# Yardeni chart page cross-check
# ---------------------------------------------------------------------------
YARDENI_PAGE_URL = (
    "https://www.yardeni.com/charts/us-stock-market/"
    "stock-market-forward-metrics/sp-500-forward-revenues-earnings-margins"
)
# Yardeni embeds the current value in the chart legend text, e.g.:
#   "Forward EPS (Apr 30 = 346.19)"
#   "52-Week Forward EPS (Apr 30 = 346.19)"
_YARDENI_PATTERN = re.compile(
    r"(?:52[\-\s]Week\s+)?Forward\s+EPS[^(]*\(\w+\s+\d+\s*=\s*([\d,.]+)\)",
    re.IGNORECASE,
)


def _cache_valid() -> bool:
    """Return True if the cache exists and is within the TTL window."""
    if not _FACTSET_CACHE.exists():
        return False
    mtime = datetime.fromtimestamp(_FACTSET_CACHE.stat().st_mtime)
    return (datetime.now() - mtime).days < _CACHE_TTL_DAYS


def _load_cache() -> dict | None:
    """Load cached result; return None if absent or malformed."""
    try:
        with open(_FACTSET_CACHE) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(value: float, source: str, as_of: str) -> None:
    payload = {
        "value": value,
        "source": source,
        "as_of": as_of,
        "fetched_at": datetime.now().isoformat(),
    }
    with open(_FACTSET_CACHE, "w") as f:
        json.dump(payload, f, indent=2)
    log.info("[factset_eps] Cached: $%.2f  source=%s  as_of=%s", value, source, as_of)


def _http_get(url: str, timeout: int = 30) -> bytes | None:
    """Simple HTTP GET; returns raw bytes or None on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as exc:
        log.warning("[factset_eps] HTTP GET failed for %s: %s", url, exc)
        return None


def _parse_pdf_for_eps(pdf_bytes: bytes) -> float | None:
    """
    Extract forward EPS from a FactSet PDF's text layer.
    Requires pdfplumber (preferred) or pdfminer; gracefully skips if unavailable.
    """
    # Try pdfplumber first (cleaner text extraction)
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            full_text = "\n".join(
                page.extract_text() or "" for page in pdf.pages[:10]
            )
        return _search_patterns(full_text)
    except ImportError:
        pass  # pdfplumber not installed

    # Fall back to pdfminer
    try:
        from pdfminer.high_level import extract_text_to_fp  # type: ignore
        from pdfminer.layout import LAParams

        buf = io.StringIO()
        extract_text_to_fp(
            io.BytesIO(pdf_bytes), buf, laparams=LAParams(), output_type="text"
        )
        return _search_patterns(buf.getvalue())
    except ImportError:
        log.warning(
            "[factset_eps] Neither pdfplumber nor pdfminer installed. "
            "Install one: pip install pdfplumber --break-system-packages"
        )
        return None


def _search_patterns(text: str) -> float | None:
    """Apply EPS regex patterns to extracted text; return first plausible match."""
    for pattern in _EPS_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
                if 150 < val < 700:  # sanity-check S&P 500 EPS range
                    log.info("[factset_eps] Parsed EPS $%.2f via pattern: %s", val, pattern[:50])
                    return round(val, 2)
            except ValueError:
                continue
    return None


def _fetch_from_yardeni() -> tuple[float | None, str]:
    """
    Fetch Yardeni's forward EPS page and extract the chart legend value.
    Returns (value, as_of_label) or (None, '').

    Note: The numeric value lives in a PNG chart image legend, not in the HTML
    body text. However, Yardeni sometimes includes it in the page's structured
    data or in readable chart alt-text. This function reads the HTML and applies
    the pattern — it works when Yardeni exposes the number in text; otherwise
    returns None and the caller falls through to the manual override.
    """
    html = _http_get(YARDENI_PAGE_URL)
    if html is None:
        return None, ""
    try:
        text = html.decode("utf-8", errors="replace")
        m = _YARDENI_PATTERN.search(text)
        if m:
            val = float(m.group(1).replace(",", ""))
            if 150 < val < 700:
                return round(val, 2), f"Yardeni chart page {date.today()}"
    except Exception as exc:
        log.warning("[factset_eps] Yardeni parse error: %s", exc)
    return None, ""


def fetch_forward_eps() -> dict | None:
    """
    Main entry point. Returns:
        {"value": float, "source": str, "as_of": str}
    or None if all paths fail.

    Call order:
        1. Return cache if valid and fresh
        2. Try FactSet PDF → parse EPS
        3. Try Yardeni page → parse chart legend
        4. Return stale cache if present (better than nothing)
    """
    # 1. Fresh cache
    if _cache_valid():
        cached = _load_cache()
        if cached:
            log.info(
                "[factset_eps] Using cache: $%.2f  source=%s  fetched=%s",
                cached["value"], cached["source"], cached.get("fetched_at", "?")
            )
            return cached

    as_of = date.today().isoformat()

    # 2. FactSet PDF
    log.info("[factset_eps] Fetching FactSet Earnings Insight PDF...")
    pdf_bytes = _http_get(FACTSET_PDF_URL)
    if pdf_bytes and pdf_bytes[:4] == b"%PDF":
        eps = _parse_pdf_for_eps(pdf_bytes)
        if eps:
            result = {"value": eps, "source": "factset_pdf", "as_of": as_of}
            _save_cache(eps, "factset_pdf", as_of)
            return result
        log.warning("[factset_eps] Downloaded PDF but could not parse EPS value")
    else:
        log.warning("[factset_eps] FactSet URL did not return a PDF (may redirect to HTML)")

    # 3. Yardeni page
    log.info("[factset_eps] Trying Yardeni chart page...")
    yval, ylabel = _fetch_from_yardeni()
    if yval:
        result = {"value": yval, "source": "yardeni_html", "as_of": as_of}
        _save_cache(yval, "yardeni_html", as_of)
        return result

    # 4. Stale cache (better than nothing)
    stale = _load_cache()
    if stale:
        log.warning(
            "[factset_eps] All live fetches failed — returning stale cache "
            "(fetched %s, value $%.2f)",
            stale.get("fetched_at", "?"), stale["value"]
        )
        return stale

    log.warning("[factset_eps] All paths failed — no EPS available from factset_eps fetcher")
    return None
