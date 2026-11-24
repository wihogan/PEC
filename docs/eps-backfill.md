# S&P 500 NTM forward EPS backfill: May–Oct 2026 (FactSet Earnings Insight)

Method: EPS = closing price ÷ forward 12-month P/E, both as printed in the text of the same weekly
FactSet Earnings Insight PDF. I fetched each PDF with WebFetch on 2026-11-23 and read the quoted
fragments myself. The P/E is printed to one decimal, so each value is accurate to about ±0.3%
(the "range" column shows the result for P/E ±0.05).

Base URL: `https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/`

| Month-end | Report (file) | Price fragment (verbatim) | P/E fragment (verbatim) | EPS | Range |
|---|---|---|---|---|---|
| 2026-05-31 | May 29 (`EarningsInsight_052926A.pdf`) | "above the closing price of 7563.63" | "P/E ratio for the S&P 500 is 21.2" | **356.78** | 355.94–357.62 |
| 2026-06-30 | Jun 26 (`EarningsInsight_062626.pdf`) | "On June 25 … above the closing price of 7,357.49" | "P/E ratio for the S&P 500 is 20.1" | **366.04** | 365.14–366.96 |
| 2026-07-31 | Jul 31 (`EarningsInsight_073126.pdf`) | "above the closing price of 7437.63" | "P/E ratio for the S&P 500 is 19.6" | **379.47** | 378.51–380.44 |
| 2026-08-31 | Aug 28 (`EarningsInsight_082826.pdf`) | "above the closing price of 7730.99" | "P/E ratio for the S&P 500 is 19.6" | **394.44** | 393.43–395.45 |
| 2026-09-30 | Sep 25 (`EarningsInsight_092526.pdf`) | "above the closing price of 7,704.13" | "P/E ratio for the S&P 500 is 19.2" | **401.26** | 400.21–402.30 |
| 2026-10-31 | Oct 30 (`EarningsInsight_103026.pdf`) | "above the closing price of 7,720.39" | "P/E ratio for the S&P 500 is 18.7" | **412.86** | 411.75–413.96 |
| (check) | Nov 20 (`EarningsInsight_112026A.pdf`) | "above the closing price of 7,175.83" | "P/E ratio for the S&P 500 is 17.1" | **419.64** | 418.42–420.87 |

The Nov 20 value matches the approved override of 419.64.

## Notes and caveats

- **Data date vs report date:** only the June issue states its data date ("On June 25"), which is
  the Thursday before the Friday report. The other dates are assumed to follow the same
  Thursday-close pattern; this is inferred, not stated in those issues. In every case the report
  is the last weekly issue on or before the month-end. The issue after each month-end was not
  checked. Sep 30 (a Wednesday) is 5 days after the Sep 25 issue; Oct 2 would be 2 days after,
  but it falls after the month-end, so the earlier issue was used, as requested.
- **May file name:** `EarningsInsight_052926.pdf` returned a client error. Only the `A`-suffixed
  file exists. The automation must try both the `A` and plain file names. The May 22 week was
  published Thursday as `EarningsInsight_052126.pdf` (Memorial Day week), so the date in a file
  name is not always a Friday.
- **Number format:** some issues use thousands commas and some do not. Strip commas before parsing.
- The Oct 30 PDF was fetched at the URL with `?AccountID=13354688` appended, as search returned it.
  The plain URL without that parameter was not fetched; it is presumably the same file.
- **Mid-month issues were not used:** May 8 = 349.39, Jul 17 = 371.12, Sep 11 = 397.47 and
  Oct 9 = 406.56, all computed from issues fetched earlier. They sit between the month-end values
  and the series rises every week, which supports consistency.
- **Change of source within the series:** seed entries through 2026-04-30 are Yardeni/LSEG-based
  (Apr 30 = 345.14). These rows are FactSet-derived. In May 2026 Yardeni ran about 1.5% above
  FactSet (354.84 on May 7 vs 349.39 on May 8). The May 31 value (356.78) is a +3.4% month-on-month
  step from 345.14, of which part may come from the switch in provider. Consider adding a comment
  line in the seed marking the switch.

## Seed file convention (`config/forward_eps_seed.yaml`)

- Header: "date is last business/calendar day of month (YYYY-MM-DD)". In practice almost every
  entry uses the **calendar** month-end (for example "2026-01-31", which was a Saturday, and
  "2026-02-28"). The one exception is "2021-01-29".
- Inline style: `  - {date: "YYYY-MM-DD", value: NNN.NN}`, two-space indent, with an optional
  trailing `# comment`. Group comments sit above each year.
- The last entry is currently `  - {date: "2026-04-30", value: 345.14}  # Yardeni anchor Apr 30 = $346.19; FactSet-consistent`.
  There are no entries for May 2026 or later.
- **Caution:** `spglobal._write_back_to_seed()` rewrites everything after `entries:` and drops
  all inline and group comments. Comments survive only until the next automated write-back.
  Also check that the automation hasn't already added a 2026-05-31 or later entry before appending.

### Lines to append (after the 2026-04-30 entry)

```yaml

  # 2026 May–Oct: FactSet Earnings Insight, EPS = closing price / fwd 12M P/E (last weekly issue on/before month-end)
  - {date: "2026-05-31", value: 356.78}  # FactSet 2026-05-29: 7563.63 / 21.2
  - {date: "2026-06-30", value: 366.04}  # FactSet 2026-06-26: 7357.49 / 20.1
  - {date: "2026-07-31", value: 379.47}  # FactSet 2026-07-31: 7437.63 / 19.6
  - {date: "2026-08-31", value: 394.44}  # FactSet 2026-08-28: 7730.99 / 19.6
  - {date: "2026-09-30", value: 401.26}  # FactSet 2026-09-25: 7704.13 / 19.2
  - {date: "2026-10-31", value: 412.86}  # FactSet 2026-10-30: 7720.39 / 18.7
```

Also consider updating the header's "Current live override" lines, which still say $346.19
(2026-04-30), to 419.64 (2026-11-20, FactSet).
