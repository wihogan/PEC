"""Generate the synthetic Shiller workbooks used by tests/unit/test_shiller.py.

Run once with any Python that has `xlwt` installed (NOT a test dependency; the .xls
outputs are committed):   python tests/fixtures/shiller/make_fixtures.py

Layout reproduces the real ie_data.xls (Amendment C probe): sheets "Disclaimer" and
"Data"; seven title rows; header row index 7 with duplicate names (Price, Earnings) and
both "CAPE" and "TR CAPE"; decimal dates written as floats (October = 2026.1); D/E
blank for the latest two months; a trailing footnote row with no date. Values are
synthetic except the 2026.09-2026.11 rows, which use the figures quoted in docs/plan.md.
"""
from pathlib import Path

import xlwt

HERE = Path(__file__).parent
HEADER = ["Date", "P", "D", "E", "CPI", "Fraction", "Rate GS10", "Price", "Dividend", "Price",
          "Earnings", "Earnings", "CAPE", None, "TR CAPE"]


def months(y0, m0, y1, m1):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def row_for(i, n):
    """Smooth synthetic values rising linearly from 2009-like to 2026-like levels."""
    f = i / (n - 1)
    return 900.0 + 6700.0 * f, 25.0 + 56.0 * f, 60.0 + 234.0 * f, 211.0 + 126.0 * f, 15.0 + 25.0 * f


def write(path, last, blank_de=2, real_tail=True):
    wb = xlwt.Workbook()
    wb.add_sheet("Disclaimer").write(0, 0, "Synthetic test copy; not Shiller's data")
    ws = wb.add_sheet("Data")
    ws.write(1, 0, 'Stock Market Data Used in "Irrational Exuberance" (synthetic)')
    ws.write(2, 0, "Robert J. Shiller (layout only)")
    ws.write(6, 1, "Comp.")
    ws.write(6, 12, "P/E10 or")
    for c, h in enumerate(HEADER):
        if h is not None:
            ws.write(7, c, h)
    r = 8
    # two 19th-century rows (tiny nominal values; CAPE blank) - must not trip range checks
    for (y, m), vals in (((1871, 1), (4.44, 0.26, 0.4, 12.464061)), ((1871, 2), (4.5, 0.26, 0.4, 12.845056))):
        ws.write(r, 0, float(f"{y}.{m:02d}"))
        for c, v in enumerate(vals, start=1):
            ws.write(r, c, v)
        r += 1
    rows = list(months(2009, 1, *last))
    tail = {
        (2026, 9): (7669.414286, 81.9895, 295.5184, 337.511, 40.268315),
        (2026, 10): (7726.692727, None, None, 338.7765, 40.117401),
        (2026, 11): (7619.38, None, None, 339.40925, 39.374477),
    }
    for i, (y, m) in enumerate(rows):
        p, d, e, cpi, cape = row_for(i, len(rows))
        if real_tail and (y, m) in tail:
            p, d, e, cpi, cape = tail[(y, m)]
        if i >= len(rows) - blank_de:
            d = e = None
        ws.write(r, 0, float(f"{y}.{m:02d}"))           # 2026.10 -> 2026.1 (as in the real file)
        for c, v in ((1, p), (2, d), (3, e), (4, cpi), (5, y + (m - 0.5) / 12), (6, 4.5),
                     (7, p), (9, p * 700), (12, cape), (14, cape + 2.5)):
            if v is not None:
                ws.write(r, c, v)
        r += 1
    ws.write(r, 1, "Nov price is Nov 12th close")         # footnote row, no date
    ws.write(r, 4, "Oct/Nov CPI estimated")
    wb.save(str(path))


if __name__ == "__main__":
    write(HERE / "ie_data_synthetic.xls", (2026, 11))
    write(HERE / "ie_data_stale_yale.xls", (2023, 9), real_tail=False)   # Yale copy ends 2023.09
