"""Temporary validation probe (branch probe-shiller only): is Shiller's workbook current and
does it reproduce multpl's archived values? Read-only; prints findings."""
import io, re, sys
import pandas as pd, requests

UA = {"User-Agent": "Mozilla/5.0 (PEC dashboard data validation; contact via github.com/wihogan/PEC)"}
TARGETS = {"pe_2026-10": 25.86, "ey_2026-10_pct": 3.87, "dy_2026-10_pct": 1.05, "cape_2026-10": 40.13,
           "eps_2022-12": 172.73, "eps_2023-12": 192.40}

def fetch(url):
    r = requests.get(url, headers=UA, timeout=60, allow_redirects=True)
    print(f"GET {url} -> {r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes final={r.url}")
    return r

def analyse(label, content):
    print(f"\n===== {label} =====")
    try:
        raw = pd.read_excel(io.BytesIO(content), sheet_name="Data", header=None, engine="xlrd")
    except Exception as e:
        print("parse failed:", repr(e)[:300]); return
    hdr = next((i for i in range(25) if str(raw.iat[i, 0]).strip().lower() == "date"), None)
    print("header row:", hdr)
    cols = [str(c).strip() for c in raw.iloc[hdr].tolist()]
    print("columns:", cols[:25])
    df = raw.iloc[hdr + 1:].copy(); df.columns = cols
    df = df[pd.to_numeric(df["Date"], errors="coerce").notna()]
    df["Date"] = pd.to_numeric(df["Date"])
    def col(name):
        for c in cols:
            if c.upper() == name.upper(): return c
        for c in cols:
            if c.upper().startswith(name.upper()): return c
    P, D, E, CPI, CAPE = col("P"), col("D"), col("E"), col("CPI"), col("CAPE")
    print("mapped:", dict(P=P, D=D, E=E, CPI=CPI, CAPE=CAPE))
    show = [c for c in [ "Date", P, D, E, CPI, CAPE] if c]
    print("last 12 rows:\n", df[show].tail(12).to_string(index=False))
    def row(ym):
        key = round(float(ym), 2)
        m = df[(df["Date"].astype(float).round(2) == key)]
        return m.iloc[0] if len(m) else None
    out = {}
    r = row("2026.10")
    if r is not None:
        p, d, e = (pd.to_numeric(r[c], errors="coerce") for c in (P, D, E))
        if pd.notna(p) and pd.notna(e) and e: out["pe_2026-10"] = p / e; out["ey_2026-10_pct"] = 100 * e / p
        if pd.notna(p) and pd.notna(d): out["dy_2026-10_pct"] = 100 * d / p
        if CAPE: out["cape_2026-10"] = pd.to_numeric(r[CAPE], errors="coerce")
    for ym, k in (("2022.12", "eps_2022-12"), ("2023.12", "eps_2023-12")):
        r = row(ym)
        if r is not None: out[k] = pd.to_numeric(r[E], errors="coerce")
    print("\ncomparison vs multpl archive:")
    for k, tgt in TARGETS.items():
        v = out.get(k)
        print(f"  {k:16s} target={tgt:<8} shiller={'n/a' if v is None or pd.isna(v) else round(float(v), 4)}"
              + ("" if v is None or pd.isna(v) else f"  diff={round(float(v) - tgt, 4)}"))

land = fetch("https://shillerdata.com/")
links = sorted(set(re.findall(r'href="([^"]+?\.xlsx?(?:\?[^"]*)?)"', land.text, flags=re.I))) if land.ok else []
print("workbook links on landing page:", links)
for l in links[:2]:
    r = fetch(l if l.startswith("http") else "https://shillerdata.com" + l)
    if r.ok: analyse(f"shillerdata.com {l[:80]}", r.content)
y = fetch("http://www.econ.yale.edu/~shiller/data/ie_data.xls")
if y.ok: analyse("Yale ie_data.xls", y.content)
