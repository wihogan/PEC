"""tools/set_eps.py (plan §2, §6.1): edits only the two manual_override lines."""
import difflib
import shutil
import subprocess
import sys

import pytest
import yaml

import refresh_v2
from conftest import REAL_CONFIG, REPO

sys.path.insert(0, str(REPO / "tools"))
import set_eps  # noqa: E402

TODAY = "2026-11-20"


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "series_v2.yaml"
    shutil.copy(REAL_CONFIG, p)
    return p


def _run(cfg, value, date_, today=TODAY):
    return set_eps.main(["--value", value, "--date", date_, "--config", str(cfg), "--today", today])


def _override(cfg):
    return refresh_v2._load_config(cfg)["valuation_inputs"]["forward_eps_ntm"]["manual_override"]


def test_edit_changes_exactly_two_lines_and_preserves_everything_else(cfg):
    before = cfg.read_bytes()
    assert _run(cfg, "360.12", TODAY) == 0
    after = cfg.read_bytes()

    a, b = before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True)
    assert len(a) == len(b)
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    assert changed == [
        ("      value:    354.84\n", "      value:    360.12\n"),
        ('      effective_date: "2026-05-07"\n', '      effective_date: "2026-11-20"\n'),
    ]
    # all comments retained byte-for-byte
    assert [l for l in a if l.lstrip().startswith("#")] == [l for l in b if l.lstrip().startswith("#")]
    assert _override(cfg) == {"value": 360.12, "effective_date": "2026-11-20"}
    # rest of config parses identically
    old, new = yaml.safe_load(before), yaml.safe_load(after)
    old["valuation_inputs"]["forward_eps_ntm"]["manual_override"] = None
    new["valuation_inputs"]["forward_eps_ntm"]["manual_override"] = None
    assert old == new


def test_integer_value_written_as_float(cfg):
    assert _run(cfg, "360", TODAY) == 0
    ov = _override(cfg)
    assert ov["value"] == 360.0 and isinstance(ov["value"], float)
    assert "      value:    360.0\n" in cfg.read_text()


@pytest.mark.parametrize("value", ["200", "600", "200.0", "599.99", "3.6e2"])
def test_range_edges_accepted(cfg, value):
    assert _run(cfg, value, TODAY) == 0
    assert _override(cfg)["value"] == float(value)


@pytest.mark.parametrize("value", ["abc", "", "199.99", "600.01", "0", "-354.84", "nan", "inf", "354,84", "$354"])
def test_bad_value_rejected_file_unchanged(cfg, value, capsys):
    before = cfg.read_bytes()
    assert _run(cfg, value, TODAY) == 1
    assert cfg.read_bytes() == before
    assert "ERROR" in capsys.readouterr().err


@pytest.mark.parametrize("d", ["2026-11-21", "2027-01-01"])
def test_future_date_rejected(cfg, d):
    before = cfg.read_bytes()
    assert _run(cfg, "360", d) == 1
    assert cfg.read_bytes() == before


def test_today_and_past_dates_accepted(cfg):
    assert _run(cfg, "360", TODAY) == 0
    assert _run(cfg, "361", "2026-01-02") == 0
    assert _override(cfg) == {"value": 361.0, "effective_date": "2026-01-02"}


@pytest.mark.parametrize("d", ["2026-13-01", "2026-02-30", "11/20/2026", "2026-11-20T00:00", "20261120", "2026-1-5", ""])
def test_non_iso_date_rejected(cfg, d):
    before = cfg.read_bytes()
    assert _run(cfg, "360", d) == 1
    assert cfg.read_bytes() == before


def test_cli_subprocess(cfg):
    r = subprocess.run(
        [sys.executable, str(REPO / "tools" / "set_eps.py"), "--value", "355.5", "--date", "2026-11-13",
         "--config", str(cfg), "--today", TODAY],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert _override(cfg) == {"value": 355.5, "effective_date": "2026-11-13"}


def test_missing_override_block_fails(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("series: []\nvaluation_inputs:\n  forward_eps_ntm:\n    fetcher: spglobal\n")
    before = p.read_bytes()
    assert _run(p, "360", TODAY) == 1
    assert p.read_bytes() == before


def test_only_forward_eps_override_is_touched(tmp_path):
    """A `value:` line elsewhere (another input, or a comment) is never edited."""
    text = (
        "series: []\n"
        "valuation_inputs:\n"
        "  other_input:\n"
        "    manual_override:\n"
        "      value: 1.0\n"
        "      effective_date: '2020-01-01'\n"
        "  forward_eps_ntm:\n"
        "    fetcher: spglobal\n"
        "    # value: 999  (old note)\n"
        "    manual_override:\n"
        "      value: 354.84   # approved\n"
        "      effective_date: '2026-05-07'\n"
    )
    p = tmp_path / "c.yaml"
    p.write_text(text)
    assert _run(p, "360.5", TODAY) == 0
    diff = [l for l in difflib.unified_diff(text.splitlines(), p.read_text().splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert diff == [
        "-      value: 354.84   # approved",
        "-      effective_date: '2026-05-07'",
        "+      value: 360.5   # approved",
        "+      effective_date: '2026-11-20'",
    ]
