"""Market price from the rate card: tiers, setup charges, missing materials, margin and suggestion."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from core import market, shop

REPO = Path(__file__).resolve().parent.parent


def test_tier_selection_at_the_edges():
    assert market.tier_index(499) == 0
    assert market.tier_index(500) == 1
    assert market.tier_index(1000) == 1
    assert market.tier_index(1001) == 2
    # PLA customer rates are 30 / 25 / 20 per gram
    assert market.market_price({"PLA": 499}).customer == pytest.approx(499 * 30)
    assert market.market_price({"PLA": 500}).customer == pytest.approx(500 * 25)
    assert market.market_price({"PLA": 1001}).customer == pytest.approx(1001 * 20)


def test_petg_200g_customer_6000_reseller_5000():
    m = market.market_price({"PETG": 200})
    assert m.customer == 6000
    assert m.reseller == 5000
    assert m.available


def test_setup_charge_added_for_pc():
    m = market.market_price({"PC 3dxtech": 100})
    assert m.customer == 100 * 120 + 1000
    assert m.reseller == 100 * 105 + 1000


def test_missing_material_says_so():
    m = market.market_price({"Wood PLA": 100})
    assert not m.available
    assert m.missing == ["Wood PLA"]
    assert "No rate for 'Wood PLA' on the rate card" in m.messages[0]
    assert "market (Remas): not available" in "\n".join(market.format_market(None, None, m, 300))


def test_material_name_mapping():
    assert market.market_price({"PLA": 10}).lines[0][0] == "PLA+"
    assert market.market_price({"TPU": 10}).lines[0][0] == "TPU 95A"
    assert market.market_price({"ABS": 10}).lines[0][0] == "ABS"


def test_no_reseller_rate_is_stated():
    m = market.market_price({"PET": 100})
    assert m.customer == 5000 and m.reseller is None
    assert "reseller not offered" in "\n".join(market.format_market(None, None, m, 300))


def test_suggested_price_modes():
    remas = market.market_price({"PETG": 210})          # Remas customer 6300, reseller 5250
    mine = market.market_price({"PETG": 210}, card={"PETG": {"customer": [10, 9, 8], "reseller": [8, 7, 6]}})
    assert market.suggest(mine, remas, 1000)[0] == 2100              # default: YOUR card, not Remas
    assert market.suggest(mine, remas, 1000, "reseller")[0] == 1700
    assert market.suggest(mine, remas, 1000, "remas")[0] == 6300     # Remas only when chosen
    assert market.suggest(mine, remas, 1000, "remas_reseller")[0] == 5250
    assert market.suggest(mine, remas, 1010, "multiplier", 2.5)[0] == 2550     # 2525 rounds up
    assert market.round_to(6024) == 6000 and market.round_to(6026) == 6050


def test_custom_rate_card_path(tmp_path):
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"fdm": {"PETG": {"customer": [1, 1, 1], "reseller": [1, 1, 1]}}}))
    assert market.market_price({"PETG": 10}, str(card)).customer == 10
    bad = market.market_price({"PETG": 10}, str(tmp_path / "nope.json"))
    assert not bad.available and "Cannot read" in bad.messages[0]


SETTINGS = {"electricity_rate_per_kwh": 90, "printer_watts": 250, "machine_rate_per_hour": 15,
            "failure_rate": 0.1}


def test_build_card_from_costs():
    s = {**SETTINGS, "grams_per_hour": 10, "labour_rate_per_hour": 120, "labour_min_per_100g": 5,
         "profit_multiplier": 2.0, "tier_discounts": [0, 0.2, 0.4], "reseller_discount": 0.1}
    card = market.build_card(s, {"PETG": 2500, "PLA": 3000, "Free": 0})
    assert set(card["fdm"]) == {"PETG", "PLA+"}          # unpriced material skipped, PLA mapped to PLA+
    # per gram: filament 2.5, electricity 0.25*90/10 = 2.25, wear 1.5, labour 120*5/6000 = 0.1; failure 10%
    base = (2.5 + 2.25 + 1.5 + 0.1) / 0.9
    petg = card["fdm"]["PETG"]
    assert petg["customer"] == [round(base * 2, 1), round(base * 2 * 0.8, 1), round(base * 2 * 0.6, 1)]
    assert petg["reseller"][0] == round(petg["customer"][0] * 0.9, 1)
    assert market.build_card({**s, "setup_charges": {"PC 3dxtech": 1000}}, {"PC 3dxtech": 9000})[
        "fdm"]["PC 3dxtech"]["setup"] == 1000


def test_job_prices_from_my_card_and_breakdown_sums_to_price(tmp_path):
    mats = shop.load_materials(directory=None)
    mats["PETG"].price_per_kg = 2500.0
    job = shop.price_job({"PETG": 200.0}, 4.0, settings={**SETTINGS, "my_rates_path": str(tmp_path / "none.json")},
                         materials=mats, parts={}, inventory={})
    assert job.mine.available and job.market.customer == 6000
    labels = [label for label, _ in job.parts]
    assert labels == ["Filament", "Electricity", "Machine wear", "Failure allowance", "Labour", "Profit"]
    assert sum(v for _, v in job.parts) == pytest.approx(job.mine.customer)
    amounts = dict(job.parts)
    assert amounts["Filament"] == pytest.approx(500) and amounts["Electricity"] == pytest.approx(90)
    text = shop.format_job(job)
    assert "YOUR COST:" in text and "= YOUR PRICE" in text
    assert "market (Remas): customer 6,000, reseller 5,000" in text and "Gap to market:" in text
    assert "(your price)" in text and "Cost to make:" in text      # Remas is not the suggestion; old lines kept


def test_edited_card_file_wins_over_generated(tmp_path):
    card = tmp_path / "my.json"
    card.write_text(json.dumps({"fdm": {"PETG": {"customer": [20, 20, 20], "reseller": [15, 15, 15]}}}))
    mats = shop.load_materials(directory=None)
    mats["PETG"].price_per_kg = 2500.0
    job = shop.price_job({"PETG": 100.0}, 2.0, settings={**SETTINGS, "my_rates_path": str(card)},
                         materials=mats, parts={}, inventory={})
    assert job.mine.customer == 2000 and job.mine.reseller == 1500 and job.mine.rates_path == str(card)
    assert shop.format_job(job).count("Suggested:     2,000  (your price)") == 1


def test_cli_build_card_and_price(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "materials.json").write_text(json.dumps({"PETG": {"price_per_kg": 2500}}))
    (cfg / "settings.json").write_text(json.dumps(SETTINGS))
    env = {"PRINTTALLY_CONFIG": str(cfg), "PATH": "/usr/bin:/bin"}
    cli = [sys.executable, str(REPO / "printtally.py")]
    built = subprocess.run(cli + ["build-card", "--profit-multiplier", "3"], capture_output=True, text=True, env=env)
    assert built.returncode == 0, built.stderr
    card = json.loads((cfg / "my_rates.json").read_text())
    assert card["assumptions"]["profit_multiplier"] == 3.0
    again = subprocess.run(cli + ["build-card"], capture_output=True, text=True, env=env)
    assert again.returncode != 0 and "already exists" in again.stderr      # never overwrites your edits
    gcode = tmp_path / "a.gcode"
    gcode.write_text("; generated by PrusaSlicer 2.7\n; filament used [g] = 200.0\n"
                     "; estimated printing time (normal mode) = 4h 0m 0s\n")
    (tmp_path / "printtally.json").write_text(json.dumps({"material": "PETG"}))
    out = subprocess.run(cli + ["price", str(gcode), "--suggest", "remas_reseller"],
                         capture_output=True, text=True, env=env)
    assert "= YOUR PRICE" in out.stdout and "market (Remas): customer 6,000, reseller 5,000" in out.stdout, out.stdout
    assert "Suggested:     5,000  (Remas reseller price)" in out.stdout


def test_orca_script_prints_breakdown_and_market_lines(tmp_path):
    gcode = tmp_path / "a.gcode"
    gcode.write_text("; total filament used [g] = 200.0\n; estimated printing time (normal mode) = 4h 0m 0s\n")
    out = subprocess.run([sys.executable, str(REPO / "orca_prusa_script" / "printtally_postprocess.py"),
                          "--price-per-kg", "2500", "--electricity-rate", "90", "--material", "PETG",
                          "--my-rates", str(tmp_path / "none.json"), str(gcode)],
                         capture_output=True, text=True)
    assert "market (Remas): customer 6,000, reseller 5,000" in out.stdout, out.stdout + out.stderr
    assert "YOUR PRICE" in out.stdout and "Failure allowance" in out.stdout and "FINAL PRICE:" in out.stdout
    assert "Profit" in gcode.read_text()


def test_plugin_market_copy_and_card_match_core():
    marker = "from dataclasses import dataclass, field"
    plugin = REPO / "cura_plugin" / "PrintTally" / "PrintTally"
    assert (REPO / "core" / "market.py").read_text().split(marker, 1)[1] == \
           (plugin / "market.py").read_text().split(marker, 1)[1]
    assert (REPO / "data" / "market_rates_pk.json").read_text() == (plugin / "market_rates_pk.json").read_text()
