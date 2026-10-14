import csv
import json
from datetime import date
from urllib.error import URLError

import pytest

from core import sheet_sync
from printtally import main

CSV = """DATE,Kind,Name,Price,density_g_cm3,weigh_factor,aliases,notes
2026-01-01,material,PLA,20,1.24,1.05,pla; generic pla,first
2026-10-01,material,PLA,25,,,,new price
2026-03-01,material,PLA,22,,,,
2027-01-01,material,PLA,30,,,,future
2026-02-01,part,Switch,2,,,,
"""


@pytest.fixture
def setup_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTTALLY_CONFIG", str(tmp_path))
    monkeypatch.setattr(sheet_sync, "download", lambda url: CSV)
    return tmp_path


def sync():
    return sheet_sync.sync_sheet("https://example.test/prices.csv", today=date(2026, 10, 15))


def test_latest_date_wins_and_future_ignored(setup_sheet):
    sync()
    materials = json.loads((setup_sheet / "materials.json").read_text())
    assert materials["PLA"]["price_per_kg"] == 25
    assert json.loads((setup_sheet / "parts.json").read_text())["Switch"]["price"] == 2
    assert len(sheet_sync.price_history("PLA")) == 4


def test_merge_preserves_entries_and_unspecified_fields(setup_sheet):
    local = {"PLA": {"price_per_kg": 18, "density_g_cm3": 1.3, "custom": True},
             "Local": {"price_per_kg": 9}}
    (setup_sheet / "materials.json").write_text(json.dumps(local))
    (setup_sheet / "parts.json").write_text(json.dumps({"Switch": 1, "Local part": 7}))
    messages = sync()
    result = json.loads((setup_sheet / "materials.json").read_text())
    assert result["Local"] == local["Local"]
    assert result["PLA"] == {**local["PLA"], "price_per_kg": 25, "notes": "new price"}
    assert json.loads((setup_sheet / "parts.json").read_text())["Local part"] == 7
    assert "PLA: 18 -> 25.0" in messages


def test_history_deduplicates_and_preserves_edits(setup_sheet, monkeypatch):
    sync()
    sync()
    monkeypatch.setattr(sheet_sync, "download", lambda url: CSV.replace(
        "material,PLA,25", "material,PLA,26"))
    sync()
    with (setup_sheet / "price_history.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6
    assert {float(r["price"]) for r in rows if r["date"] == "2026-10-01"} == {25, 26}
    assert all(r["synced_at"] for r in rows)


def test_offline_uses_cache(setup_sheet, monkeypatch):
    sync()
    def offline(url):
        raise URLError("offline")
    monkeypatch.setattr(sheet_sync, "download", offline)
    messages = sheet_sync.sync_sheet(today=date(2026, 10, 15))
    assert "using cached sheet" in messages[0]
    assert len(sheet_sync.price_history("pla")) == 4
    (setup_sheet / "sheet_cache.csv").unlink()
    with pytest.raises(ValueError, match="no cached sheet"):
        sync()


def test_cli_saved_url_and_history(setup_sheet, capsys):
    (setup_sheet / "settings.json").write_text('{"currency": "PKR"}')
    main(["sync-sheet", "https://example.test/prices.csv"])
    settings = json.loads((setup_sheet / "settings.json").read_text())
    assert settings == {"currency": "PKR", "sheet_csv_url": "https://example.test/prices.csv"}
    main(["sync-sheet"])
    main(["price-history", "PLA"])
    assert "2026-10-01 material PLA: 25.0" in capsys.readouterr().out


def test_optional_fields_and_invalid_csv_preserves_cache(setup_sheet, monkeypatch):
    sheet_sync.sync_sheet("url", today=date(2026, 1, 1))
    material = json.loads((setup_sheet / "materials.json").read_text())["PLA"]
    assert material["aliases"] == ["pla", "generic pla"]
    assert material["weigh_factor"] == 1.05
    assert material["density_g_cm3"] == 1.24
    monkeypatch.setattr(sheet_sync, "download", lambda url: "<html>not CSV</html>")
    with pytest.raises(ValueError, match="requires"):
        sync()
    assert (setup_sheet / "sheet_cache.csv").read_text() == CSV


def test_download_timeout(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return CSV.encode()
    def open_url(url, timeout):
        assert timeout == 10
        return Response()
    monkeypatch.setattr(sheet_sync, "urlopen", open_url)
    assert sheet_sync.download("url") == CSV
