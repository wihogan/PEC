# Research handoff — replacement sources for PEC's five multpl.com valuation series

Prepared 2026-11-24. Self-contained: it works in a fresh Claude session or another assistant (e.g. ChatGPT) with web browsing. **This is a research task only. Write no code and change nothing.** Return the report described in §6. The owner brings it back to the PEC build session.

---

## 1. Background

PEC is a personal macro dashboard. A Python job runs on **GitHub Actions Ubuntu runners (Microsoft Azure IP addresses)** several times each weekday and builds a static page. Five S&P 500 valuation series came from **multpl.com**, which has been **down for everyone since 2026-11-23**: its only IP address, `178.128.130.10`, refuses TCP connections on ports 80 and 443, both from GitHub runners and from the owner's home browser. The dashboard is carrying forward the last good values (October 2026).

Owner preferences:
- Free tiers first.
- Verified facts over inference.
- No automated logins. The owner subscribes to WSJ, Bloomberg (consumer) and FT, but those are for manual cross-checks only.
- No source change without the owner's approval.

## 2. The five series and what multpl actually published

Values below are the last good values in PEC (multpl data through 2026-10-30, captured 2026-11-20). The definitions were checked against multpl's own saved pages:

| PEC id | multpl page (slug) | Last value | History used | Definition (verified unless marked) |
|---|---|---|---|---|
| `sp500_dividend_yield` | `s-p-500-dividend-yield` | 1.05% (Oct 2026) | from 2010 | TTM dividends per share ÷ price |
| `sp500_pe` | `s-p-500-pe-ratio` | 25.86 (Oct 2026) | from 1980 | Price ÷ **TTM GAAP as-reported EPS**. Implied EPS: Dec 2022 = 172.73, Dec 2023 = 192.40, matching S&P DJI's as-reported calendar-year figures. |
| `sp500_earnings_yield` | `s-p-500-earnings-yield` | 3.87% (Oct 2026) | from 2010 | 1 ÷ P/E |
| `sp500_earnings` | `s-p-500-earnings` | 298.03 (Jun 2026) | from 2010 | **Inflation-adjusted** TTM as-reported EPS, expressed in current dollars, not nominal. Its history shifts every month as CPI updates. |
| `shiller_cape` | `shiller-pe` | 40.13 (Oct 2026) | from 1980 | CAPE = (monthly-average price in base-period dollars) ÷ mean(real EPS over the **120 months before** that month). This reproduces multpl's values exactly to 2 decimals. |

Conventions verified from the data:
- **Monthly price is the average of daily closes** (Shiller's convention), not the month-end close.
- Multpl's current-month row is an estimate that updates daily.
- CPI basis is presumably CPI-U not seasonally adjusted (FRED `CPIAUCNS`), as Shiller uses. **Unverified; please confirm.**

## 3. Questions to answer

1. **Is multpl.com back?** Load `https://www.multpl.com/shiller-pe/table/by-month` and report what happens, with date and time. Also check its DNS if you can, and any status note or social post about an outage or shutdown.
2. **For each of the five series,** find candidate replacement sources and rank them: primary automated source, fallback, and a manual sanity-check source.
3. **Can one source cover all five?** (Shiller's own dataset is the main candidate. See §4.)
4. **The "compute it ourselves" path.** PEC already has the S&P 500 daily price (Yahoo `^GSPC`) and FRED access. What's needed is a current, automatable source of **quarterly S&P 500 GAAP as-reported EPS** and **quarterly dividends per share**. Find the best options and confirm the formulas reproduce multpl's numbers within a small tolerance.
5. **Owner decision support:** briefly explain the difference between showing TTM earnings in **nominal dollars** (the common figure) and **inflation-adjusted** dollars (multpl's version), and which sources provide each.

## 4. Candidate sources to check (add others you find)

For each, actually load the page or file and record the latest value and its date. Mark anything you couldn't load as **UNVERIFIED**.

- **Shiller's dataset (upstream of multpl):** Yale `http://www.econ.yale.edu/~shiller/data/ie_data.xls` (reportedly stale after Sep 2023: confirm) and **`https://shillerdata.com/`**. Find the current download link and format (xls/xlsx/csv), the last data month, update cadence, and the columns: P, D, E, CPI, CAPE, real values. Note any terms of use.
- **S&P Dow Jones Indices EPS workbook:** `https://www.spglobal.com/spdji/en/documents/additional-material/sp-500-eps-est.xlsx` (Howard Silverblatt). It returns HTTP 403 to GitHub runners. Check: is there an alternate URL or a landing page that links it? Which sheets hold quarterly as-reported EPS and dividends per share? How current is it? Does a browser download work?
- **Nasdaq Data Link `MULTPL/*` datasets** (e.g. `MULTPL/SHILLER_PE_RATIO_MONTH`, `SP500_PE_RATIO_MONTH`, `SP500_DIV_YIELD_MONTH`, `SP500_EARNINGS_MONTH`): are they still live and current, or discontinued? Is a free API key enough?
- **FRED / ALFRED:** confirm there are no S&P 500 earnings, dividend, P/E or CAPE series (likely none). Confirm `CPIAUCNS` is current.
- **Yahoo Finance:** what `^GSPC` and `SPY` report for trailing P/E and dividend yield (e.g. via yfinance `.info`), and what basis they use. Can SPY's distribution history stand in for S&P 500 dividends per share (SPY dividends × index/SPY price ratio), and how close is that historically?
- **Valuation sites:** gurufocus Shiller P/E, worldperatio.com, currentmarketvaluation.com, macrotrends, longtermtrends.net, ycharts (free?), Siblis Research. For each: the metric basis (GAAP vs operating, their own estimates?), whether pages render server-side or need JavaScript, robots.txt and ToS on automated access, and Cloudflare or bot-protection signals.
- **Free-tier market data APIs:** Financial Modeling Prep, Finnhub, Alpha Vantage, EODHD, Twelve Data. Do any offer index-level S&P 500 P/E, earnings or dividend yield on a free tier?
- **Manual cross-check only:** WSJ "P/Es & Yields on Major Indexes" (`https://www.wsj.com/market-data/stocks/peyields`), Barron's Market Lab, FactSet Earnings Insight (trailing figures).

## 5. Rules for the research
- Load every source you rate. Quote at most short fragments (under 15 words) as evidence, and record the URL and the date you checked.
- Don't guess numbers. If you couldn't see a value, say so.
- Flag anything likely to block a cloud server: Cloudflare challenges, JavaScript-only rendering, robots disallow, or terms that forbid automated access.
- Prefer sources whose definitions match §2 (GAAP as-reported, monthly-average price, CPI-U NSA). Where a definition differs, state by how much, with an example month.

## 6. Deliverable (return this to the owner as one markdown document)

1. **multpl status**, with evidence and timestamp.
2. **Source table:** one row per candidate. Columns: URL; series covered; metric basis; latest value and date seen; format; free/auth; automation risk (blocking, ToS); history depth; verified yes/no.
3. **Recommendation per series:** primary, fallback, sanity check, each with a one-paragraph integration sketch: how to discover the file or URL, the parse method, expected value range, update cadence.
4. **"Compute ourselves" assessment:** recommended EPS and dividend source, formulas for all five series (including CAPE exactly as in §2), a validation plan against multpl's last good values, and how to splice onto existing history.
5. **Nominal vs inflation-adjusted earnings:** a short note with a recommendation.
6. **Overall recommendation:** confidence level, and open questions for the owner.

---

### Prompt to paste (Claude, ChatGPT or another browsing assistant)

> Please read the attached research brief, "Research handoff — replacement sources for PEC's five multpl.com valuation series", and carry it out using live web browsing. This is a research task only, with no code. Load each source you evaluate, record the latest value and date you actually saw, and mark anything you couldn't load as UNVERIFIED. Return a single markdown report structured exactly as section 6 of the brief.
