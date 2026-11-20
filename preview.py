"""
preview.py — inject data.json into template2.html → dashboard2.html
Non-destructive: does not touch refresh.py, template.html, or data pipeline.
Usage: python preview.py
"""
import base64
import json
import pathlib
from datetime import datetime

ROOT = pathlib.Path(__file__).parent

data_path     = ROOT / "data.json"
template_path = ROOT / "template2.html"
output_path   = ROOT / "dashboard2.html"

# Load data (utf-8-sig strips BOM if present)
with open(data_path, "r", encoding="utf-8-sig") as f:
    raw = f.read()

# Validate JSON
try:
    parsed = json.loads(raw)
    print(f"✓ data.json loaded — {len(parsed.get('series', {}))} series")
except json.JSONDecodeError as e:
    print(f"✗ data.json parse error: {e}")
    raise

# Re-serialize as compact JSON and base64-encode so it is HTML-safe
# (avoids any </script> or other HTML-breaking characters in the payload)
compact = json.dumps(parsed, separators=(',', ':'), ensure_ascii=False)
encoded = base64.b64encode(compact.encode("utf-8")).decode("ascii")

# Load template
with open(template_path, "r", encoding="utf-8") as f:
    html = f.read()

# Timestamp
ts = parsed.get("metadata", {}).get("last_refreshed") or datetime.now().strftime("%Y-%m-%d %H:%M")

# Inject
if "__DASHBOARD_DATA__" not in html:
    raise ValueError("__DASHBOARD_DATA__ placeholder not found in template2.html")
if "__GENERATED_AT__" not in html:
    print("  ⚠ __GENERATED_AT__ placeholder not found — skipping timestamp injection")

html = html.replace("__DASHBOARD_DATA__", encoded)
html = html.replace("__GENERATED_AT__", ts)

# Verify replacement worked
if encoded not in html:
    raise RuntimeError("Replacement verification failed — encoded data not found in output")

# Write
with open(output_path, "w", encoding="utf-8") as f:
    f.write(html)

size_kb = output_path.stat().st_size / 1024
print(f"✓ {output_path.name} written ({size_kb:.0f} KB) — timestamp: {ts}")
print(f"  encoded payload: {len(encoded):,} chars ({len(encoded)//1024} KB base64)")
