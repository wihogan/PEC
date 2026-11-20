import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import spike_summary  # noqa: E402

KEY = "abcd1234secretkey"


def _setup(tmp_path, with_data=True):
    cfg = tmp_path / "series.yaml"
    cfg.write_text(yaml.safe_dump({"series": [
        {"id": "a", "source": "fred"},
        {"id": "b", "source": "fred"},
        {"id": "c", "source": "yahoo"},
    ]}))
    data = tmp_path / "data.json"
    if with_data:
        data.write_text(json.dumps({
            "metadata": {"statuses": {
                "a": {"status": "ok"},
                "b": {"status": "error", "error": f"boom key={KEY} " + "x" * 300},
                "c": {"status": "ok"},
            }},
            "valuation_inputs": {"forward_eps_ntm": {
                "value": 354.84, "effective_date": "2026-05-07", "source": "override"}},
        }))
    log = tmp_path / "refresh.log"
    log.write_text(f"hello\nspglobal failed https://x?api_key={KEY}\nother\n")
    out = tmp_path / "line.json"
    return cfg, data, log, out


def _run(cfg, data, log, out):
    return spike_summary.main(["99", "schedule", "42", "0", "--config", str(cfg),
                               "--data", str(data), "--log", str(log), "--out", str(out)])


def test_mask_key_and_pattern(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", KEY)
    assert spike_summary.mask(f"x {KEY} y") == "x *** y"
    assert spike_summary.mask("u?api_key=ZZ9zz&b=1") == "u?***&b=1"


def test_counts_and_masking(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FRED_API_KEY", KEY)
    cfg, data, log, out = _setup(tmp_path)
    assert _run(cfg, data, log, out) == 0
    line = json.loads(out.read_text())
    assert line["per_source"]["fred"] == {
        "attempted": 2, "ok": 1, "fail": 1, "failed_ids": ["b"]}
    assert line["per_source"]["yahoo"]["ok"] == 1
    assert len(line["errors"]["b"]) <= 160
    assert line["eps"] == {"source": "override", "value": 354.84,
                           "effective_date": "2026-05-07"}
    assert line["elapsed_s"] == 42 and line["refresh_rc"] == 0
    assert line["fred_key_present"] is True
    assert len(line["eps_log"]) == 1
    assert KEY not in out.read_text()
    assert KEY not in capsys.readouterr().out
    assert KEY not in log.read_text()  # masked in place


def test_missing_data(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    cfg, data, log, out = _setup(tmp_path, with_data=False)
    _run(cfg, data, log, out)
    line = json.loads(out.read_text())
    assert line["per_source"]["fred"]["fail"] == 2
    assert line["per_source"]["yahoo"]["failed_ids"] == ["c"]
    assert line["errors"]["a"] == "no output"
    assert line["fred_key_present"] is False
    assert line["eps"]["value"] is None
