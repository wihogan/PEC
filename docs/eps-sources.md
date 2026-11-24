# S&P 500 forward 12-month (NTM) operating EPS: source evaluation

Research date: 2026-11-23. Scope: find a source a GitHub Actions Ubuntu runner (Azure IPs) can fetch
automatically, at least weekly, free, for the `valuation_inputs.forward_eps_ntm` input.

## How things were checked (read this first)

- The research sandbox's own egress proxy blocks all of these hosts (CONNECT 403 from the agent
  proxy, not from the sites), so **nothing here was fetched from an Azure/GitHub runner IP**.
  "Verified" below means fetched through the WebFetch tool (which also runs on cloud
  infrastructure, so it is a fair but not exact stand-in for a runner) and the number was seen.
- Some URLs (yardeni.com chart page, the S&P DJI xlsx) could not be fetched at all in this session
  for tool-permission reasons. Those are marked **UNVERIFIED**.
- Bot-blocking statements for runners are inferences unless marked otherwise. The S&P DJI 403 is a
  known fact from this project's own spike (`docs/spike.md` §3).

## Key finding

The FactSet Earnings Insight PDF does **not** print the forward 12M EPS dollar figure in its text.
The dollar values live only in chart images (p. 6 bar chart and the "Bottom-Up EPS Estimates:
Current & Historical" chart, p. ~26). The PyPI package `eps-estimates-collector` exists precisely
because of this: it OCRs the chart page with Google Cloud Vision. So `fetchers/factset_eps.py`
would fail even with pdfplumber installed: its regexes look for "forward 12-month EPS of $X",
which does not appear. (WebFetch reported no dollar figure between $200 and $500 anywhere in the
2026-11-20 text.)

The PDF **does** print, in text, both:

- `The forward 12-month P/E ratio for the S&P 500 is 17.1.` (p. 1 Key Metrics, p. 7, p. 13)
- `The bottom-up target price for the S&P 500 is 9,099.54, which is 26.8% above the closing price of 7,175.83.`

So **NTM EPS = closing price / forward P/E**, both taken from the same document. That gives a
consistent snapshot. Because the P/E is rounded to one decimal, the result is accurate to about
±0.3% (about ±$1.2 at $420).

Verified values (all fetched and read via WebFetch):

| Report | URL file name | Closing price | Fwd 12M P/E | Implied NTM EPS (range for P/E ±0.05) |
|---|---|---|---|---|
| 2026-05-08 | `EarningsInsight_050826.pdf` | 7337.11 | 21.0 | **349.39** (348.56–350.22) |
| 2026-07-17 | `EarningsInsight_071726.pdf` | 7533.77 | 20.3 | **371.12** (370.21–372.04) |
| 2026-09-11 | `EarningsInsight_091126.pdf` | 7,591.70 | 19.1 | **397.47** (396.43–398.51) |
| 2026-10-09 | `EarningsInsight_100926.pdf` | 7,765.36 | 19.1 | **406.56** (405.50–407.63) |
| 2026-11-20 | `EarningsInsight_112026A.pdf` | 7,175.83 | 17.1 | **419.64** (418.42–420.87) |

Cross-checks:
- The 2026-11-20 issue says the index fell 6.2% and forward 12M EPS rose 4.4% since 9/30, when the
  P/E was 19.0. 19.0 × 0.938 / 1.044 = 17.07, which matches the 17.1 printed.
- The current manual override (354.84 on 2026-05-07, Yardeni 52-week forward) compares with the
  FactSet-derived 349.39 on 2026-05-08. That is a 1.5% gap, which fits a different provider and method.
- **The forward EPS is now about $420, not about $350.** Any hard-coded "about $350" range check
  would wrongly reject current values. The override has been stale since May. NTM EPS rose about
  20% from May to November.
- Number formatting varies between issues ("7337.11" in some, "7,591.70" in others). Strip commas.

---

## Candidate-by-candidate

### 1. FactSet Earnings Insight PDF (weekly): VERIFIED, recommended primary

- **Latest-PDF discovery (verified):** `https://www.factset.com/earningsinsight` returns a
  **302 redirect** straight to the latest PDF. On 2026-11-23 the target was
  `https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_112026A.pdf`.
  `requests`/`urllib` follow this automatically. The current `FACTSET_PDF_URL` in
  `fetchers/factset_eps.py` already points at that URL, so the download half was likely fine. The
  parse half is what's broken.
- **URL pattern:** `EarningsInsight_MMDDYY.pdf`, sometimes with an `A` suffix
  (`..._112026A.pdf`). Both `110626.pdf` and `110626A.pdf` exist, so `A` appears to be a revision.
  Published Fridays. The host has changed over the years (`www.factset.com/hubfs/...`,
  `go.factset.com/hubfs/...`, `insight.factset.com/hubfs/...`, now `advantage.factset.com/hubfs/Website/...`).
  **Do not hard-code the host. Use the redirect.**
- **Metric:** forward 12-month P/E, where FactSet's forward 12M EPS is bottom-up (aggregated
  constituent mean estimates) and on an operating/"adjusted" basis (FactSet uses the
  mean-estimate basis analysts follow, not GAAP). The dollar EPS is derived, as described above.
- **Pages:** p. 1 Key Metrics (P/E), p. 7 Overview, p. 13 "Forward Estimates & Valuation"
  (P/E). The closing price is in the "Targets & Ratings" sentence. Chart-only EPS dollars are on p. 6
  and p. ~26.
- **Format:** PDF with a text layer (WebFetch extracted the sentences cleanly). Needs `pdfplumber`
  or `pypdf` in `requirements.txt`. pypdf is pure-Python, lighter, and good enough for sentence regexes.
- **Auth/limits:** none. **robots.txt (verified on advantage.factset.com):** no Disallow matches
  `/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/`. The disallowed `/hubfs/`
  rules cover `/hubfs/Resources%20Section/...` (White Papers, eBooks, and similar) only. ToS: FactSet's
  site terms were not reviewed (UNVERIFIED). This is a publicly distributed marketing PDF, and one
  fetch a week is low impact. Store only the two numbers, not the PDF.
- **Bot-blocking risk from Azure:** low to moderate (inference). It's a HubSpot-hosted file CDN,
  not Akamai-protected like spglobal.com. WebFetch (cloud IP) got it without issue. **Not yet
  tested from an actual runner.** Do that first.
- **History:** excellent. Every weekly issue back to at least 2018 stays at a guessable URL.
  May–Oct 2026 monthly backfill is directly fetchable (see table; June/August not fetched but the
  pattern holds).

### 2. Yardeni Research: UNVERIFIED (page fetch not permitted in this session)

- URL in use: `https://www.yardeni.com/charts/us-stock-market/stock-market-forward-metrics/sp-500-forward-revenues-earnings-margins`.
- The `/charts` index page fetched fine. It says the charts are "Interactive charts powered by
  Refinitiv" and shows a "Sign In" link. It does not say whether the forward-metrics charts are
  free. Interactive charts mean JS-rendered data. The legend text the current regex expects
  (`Forward EPS (Apr 30 = 346.19)`) probably isn't in the server HTML, which matches the spike's
  "returned nothing".
- Metric: 52-week forward operating EPS, a time-weighted blend of current and next CY consensus
  (LSEG/I/B/E/S data). Weekly.
- Quick Takes blog posts (yardeniquicktakes.com) give growth rates, not EPS dollars (verified on
  the 2026-05-06 post).
- **Verdict:** keep it as the human sanity check the owner already uses. Not a robust automated
  source.

### 3. S&P Dow Jones Indices `sp-500-eps-est.xlsx`: VERIFIED 403 from runners (spike); not refetchable here

- `https://www.spglobal.com/spdji/en/documents/additional-material/sp-500-eps-est.xlsx`. Historical
  mirror `https://us.spindices.com/documents/additional-material/sp-500-eps-est.xlsx` (UNVERIFIED,
  likely redirects to the same Akamai-fronted host).
- Metric: S&P's own bottom-up **operating** and as-reported EPS by quarter and CY, plus top-down
  estimates. NTM = sum of the next four unreported quarterly operating estimates. Updated about weekly.
- The 403 is Akamai bot management keyed on IP reputation and TLS fingerprint. Header tweaks
  (Referer and UA are already set in `spglobal.py`) don't fix datacenter-IP blocks (inference).
  Browser-impersonation libraries (`curl_cffi`) sometimes get past TLS fingerprinting but not IP
  reputation. Not recommended: fragile, and arguably circumvention.
- **Works from a residential IP** (the owner's always-on iMac). Good as a Mac-side fallback or
  sanity check (see ranking).
- YCharts republishes the quarterly values (`ycharts.com/indicators/sandp_500_earnings_per_share_forward_estimate`,
  verified: Sep-26 73.86, Dec-26 76.00, source "Standard and Poor's"). But it shows only through
  Dec 2026, is quarterly, and the YCharts ToS is a paid product's. Not usable.

### 4. WSJ "P/Es & Yields on Major Indexes": UNVERIFIED

- `https://www.wsj.com/market-data/stocks/peyields`. It used to show S&P 500 trailing P/E and a
  forward "estimate" P/E (I/B/E/S-based). The URL did not surface in search, so it couldn't be
  fetched here. WSJ/Dow Jones uses aggressive bot protection (DataDome-class) and the ToS prohibits
  scraping. Expected to fail from Azure. **Skip.**

### 5. Barron's / MarketWatch: not pursued

Same Dow Jones bot stack and ToS as WSJ. Barron's market-lab data is paywalled. Skip.

### 6. Yahoo Finance / yfinance: verified unsuitable

- The SPY analysis page (`uk.finance.yahoo.com/quote/SPY/analysis/`) shows **no** estimates, only a
  trailing P/E (22.98). `^GSPC` has no estimates. In yfinance, `Ticker("SPY").info` usually lacks
  `forwardPE`/`forwardEps` for ETFs, and where a value appears it isn't a documented index
  consensus (knowledge-based; yfinance itself not run here because egress is blocked).
- **Keep Yahoo for price**: `^GSPC` close is already fetched and can be used for the "price ÷ P/E"
  fallback.

### 7. API vendors (free tiers)

| Vendor | Finding | Verified? |
|---|---|---|
| Financial Modeling Prep | `/stable/analyst-estimates?symbol=` is per-company (AAPL examples). No index aggregate documented. Free tier is 250 calls/day. Building a bottom-up from 500 names isn't feasible on free, and coverage of the estimate endpoints on free isn't stated. | Docs page read |
| Twelve Data | `earning_estimate` needs the **Ultra/Enterprise** plan, 20 credits/symbol. | Docs page read |
| Finnhub | Estimates are a separate paid add-on ("Estimates" pricing page). No index-level estimate. | Partially, since the pricing page gave no detail |
| EODHD | The SPY summary page shows no forward P/E. ETF fundamentals ("Price/Prospective Earnings", Morningstar) require a paid fundamentals plan (UNVERIFIED). | Page read |
| Alpha Vantage | `EARNINGS_ESTIMATES` is per company. No index aggregate (knowledge-based). | No |
| Nasdaq Data Link | The old `MULTPL/*` datasets are trailing only and deprecated (knowledge-based). | No |

None gives S&P 500 NTM EPS on a free tier.

### 8. Others

- **multpl.com:** trailing 12-month reported earnings and P/E only. No forward (knowledge-based).
- **GuruFocus "S&P 500 PE Ratio with Forward Estimate":** quarterly, "as of 2025-12-31" in search
  snippets. Stale and quarterly.
- **YCharts forward P/E (I:SP500PNQ):** verified quarterly (Sep-30-2026 21.08). After 2025 it's
  computed from the SPY proxy. Quarterly and paid. Not usable.
- **MacroMicro:** JS-rendered. Downloads need a paid membership (knowledge-based). Skip.
- **worldperatio.com:** didn't surface in search. Shows trailing and Shiller P/E (knowledge-based). Skip.
- **LSEG "S&P 500 Earnings Scorecard" / "This Week in Earnings":** no public 2026 URL found. LSEG
  moved it behind registration (UNVERIFIED). Brian Gilmartin republishes the weekly LSEG forward
  4-quarter EPS on Investing.com / Seeking Alpha (verified: $319.98 on 2026-03-20, P/E 20.3x).
  This is a human sanity check only: post timing and format vary, and Seeking Alpha blocks bots.
- **FRED:** no forward EPS series (search found only YCharts/FactSet. FRED's S&P data is the price
  index). Confirmed as far as search can confirm a negative.

---

## Ranked recommendation

### (1) Primary, on the runner: FactSet Earnings Insight PDF, EPS = closing price / forward 12M P/E

Integration sketch (rewrite of `_parse_pdf_for_eps` in `fetchers/factset_eps.py`):

```python
URL = "https://www.factset.com/earningsinsight"      # 302 -> latest PDF
r = requests.get(URL, headers=UA, timeout=60, allow_redirects=True)
assert r.content[:4] == b"%PDF"
pdf_name = r.url.rsplit("/", 1)[-1]                   # EarningsInsight_112026A.pdf -> as_of date
text = " ".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(r.content)).pages[:15])
text = re.sub(r"\s+", " ", text)
pe    = re.search(r"forward 12-month P/E ratio (?:for the S&P 500 )?is (\d{1,2}\.\d)", text)
price = re.search(r"closing price of ([\d,]+\.\d{2})", text)
eps   = float(price[1].replace(",", "")) / float(pe[1])
```

- **as_of:** parse MMDDYY from the redirected file name, or the "November 20, 2026" date line on p. 1.
- **Checks:** 250 ≤ eps ≤ 600. P/E between 10 and 35. Price within ±5% of the `^GSPC` close of the
  prior business day from the existing Yahoo series. Week-over-week change ≤ 5% vs the last stored
  value (otherwise flag, keep the old value, and alert Telegram). Do **not** use a $300–$400 band.
- **Cadence:** Saturday morning PT run (FactSet posts Friday). The 7-day cache stays. Store
  `{value, pe, price, pdf_name, as_of}` in the state branch.
- **Backfill May–Oct 2026:** for each target Friday, try `EarningsInsight_MMDDYYA.pdf` then
  `..._MMDDYY.pdf` on `advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/`
  (and Thursday if Friday 404s, for holiday weeks). Five months are already verified in the table above.
- **Risks:** (a) not yet tested from a runner IP, so run one spike job first; (b) wording drift breaks
  the regex, so fail closed to cache or override, never to the loose `$`-anywhere regex (delete
  that pattern); (c) ±0.3% rounding noise from the one-decimal P/E; (d) FactSet could gate the PDF
  behind a form (search results show ungated issue URLs from 2018 through 2026); (e) ToS not reviewed.

### (2) Fallback: the always-on iMac, via the existing `set_eps` dispatch

When (1) fails, a launchd job on the owner's iMac (residential IP, already planned as the workflow
trigger) runs the same FactSet parse. If that also fails, it downloads the S&P DJI xlsx, which isn't
blocked from residential IPs (inference: the 403 is runner-specific per the spike), and computes NTM
as the sum of the next four unreported quarterly operating estimates. It then calls the existing
`set_eps` workflow_dispatch with `{value, effective_date, source}`. This uses existing plumbing and
adds no secrets beyond a GitHub token already on the Mac.

- Cheaper runner-only alternative: the FactSet Insight blog HTML (`insight.factset.com`, weekly
  "Earnings Season Update" / topic posts) states the forward 12M P/E (verified: "17.7" in the
  2026-11-06 post) but **not** the price. Pair it with the `^GSPC` close of the business day before
  the post date. This is noisier (±1%) and the same provider, so it covers a PDF-parse failure but
  not a FactSet outage.
- **Risks:** depends on the Mac being up. The xlsx layout has changed before, and `spglobal.py`
  already handles it.

### (3) Sanity check: S&P DJI xlsx NTM (monthly, from the Mac) and the Yardeni chart (by eye)

- Compare the FactSet-derived value with the S&P DJI bottom-up operating NTM monthly. Expect S&P
  to run a few percent **below** FactSet (S&P's operating definition is stricter; this is an inference,
  not verified this session). Flag if the gap
  is above 6%. That threshold is a judgement call, not a verified historical spread. Yardeni's
  52-week forward ran about 1.5% above FactSet in May 2026 (354.84 vs 349.39).
- Keep `manual_override` as the last resort. Reset it now: it says 354.84 (May), while FactSet
  implies about 419.6 as of 2026-11-20.

## Computing NTM from calendar-year estimates

If a source gives only CY bottom-up estimates (CY0 = current year, CY1 = next year):

    w     = (days remaining in current year) / (days in year)      # e.g. 2026-11-23 -> 38/365 = 0.104
    NTM  ≈ w * EPS_CY0 + (1 - w) * EPS_CY1

This is Yardeni's "52-week forward" method (he uses month weights: (12 − m)/12 on CY0 and m/12 on
CY1, where m = months elapsed). It assumes earnings are spread evenly through the year. It is
smoothest mid-quarter. Around January it jumps if CY0 actuals aren't yet final.

More precise when quarterly estimates exist (the S&P DJI xlsx has them):

    NTM = Σ next four quarterly operating EPS estimates, starting with the first quarter not yet
          fully reported

During earnings season, FactSet treats a quarter as "current" until its reporting finishes. Expect
±$1–3 differences between methods around quarter turns.
