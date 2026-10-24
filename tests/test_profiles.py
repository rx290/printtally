"""Pricing profiles (service / product), material profiles with batches, printers, and the selection plumbing."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import profiles, shop, market

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "printtally.py"


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINTTALLY_CONFIG", str(tmp_path / "cfg"))
    for var in ("PRINTTALLY_PROFILE", "PRINTTALLY_PRINTER"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "cfg"


def price(profile, grams=200.0, hours=8.0, material="PETG", printer=None, prefs=None, heads=None):
    s = shop.build_settings(profile=profile, printer=printer, heads=heads, prefs=prefs)
    return shop.price_job({material: grams}, hours, prefs or {}, s, shop.load_materials(), parts={}, inventory={})


def test_both_profiles_load():
    assert profiles.list_profiles() == ["product", "service"]
    for name in ("product", "service"):
        assert profiles.load_profile(name)["name"] == name
    with pytest.raises(ValueError, match="No profile called 'nope'"):
        profiles.load_profile("nope")


def test_product_profile_equals_the_old_settings_and_output():
    old = {"machine_rate_per_hour": 40.0, "printer_watts": 250.0, "waste_pct": 0.07, "profit_margin_pct": 0.25,
           "failure_rate": 0.0, "labour_rate_per_hour": 150.0, "labour_min_per_100g": 5.0, "grams_per_hour": 12.0,
           "profit_multiplier": 2.0, "tier_discounts": [0.0, 0.15, 0.30], "reseller_discount": 0.17,
           "suggest_mode": "mine", "cost_multiplier": 2.0}
    s = shop.build_settings(profile="product", printer="none")
    for key, value in old.items():
        assert s[key] == value, key
    # snapshot of the old default output: 200 g PETG at 2,500, 5 h, electricity 90 (taken before profiles existed)
    job = price("product", hours=5.0, printer="none")
    b = job.breakdown
    assert (b.cost_to_make, b.final_price) == (869.38, 1086.72)
    assert round(job.mine.customer) == 3140 and round(job.mine.reseller) == 2600
    assert round(job.market.customer) == 6000 and round(job.market.reseller) == 5000
    text = shop.format_job(job)
    for line in ("Selling price: 1,086.72", "= YOUR PRICE                3,140   (reseller 2,600)",
                 "Gap to market: 2,860 below Remas (customer)", "Suggested:     3,150  (your price)"):
        assert line in text


def test_old_settings_json_values_move_into_the_product_profile(config):
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({"machine_rate_per_hour": 55, "currency": "Rs "}))
    assert profiles.load_profile("product")["machine_rate_per_hour"] == 55
    assert shop.build_settings(profile="product")["currency"] == "Rs "
    assert "machine_rate_per_hour" not in profiles.load_profile("service")


def test_service_costs_more_than_product_and_shows_remas():
    service, product = price("service"), price("product")
    assert service.mine.customer > product.mine.customer
    assert service.profile == "service" and product.profile == "product"
    text = shop.format_job(service)
    assert "market (Remas): customer 6,000" in text and "Gap to market" in text
    assert "about cost x 2.0" in text and "Profile: service" in text


def test_printer_machine_rates():
    rates = {n: profiles.printer_rates(profiles.load_printer(n))["machine_rate_per_hour"] for n in profiles.list_printers()}
    assert set(rates) == {"qalam-pro-400", "quadender-pro-400", "railu", "qalam-300-pro"}
    assert rates["qalam-pro-400"] == pytest.approx(400000 / 4800 + 10, abs=0.01)
    assert rates["qalam-300-pro"] == pytest.approx(250000 / 4800 + 10, abs=0.01)
    assert rates["railu"] == pytest.approx(100000 / 4800 + 10, abs=0.01)
    assert rates["quadender-pro-400"] == pytest.approx(375000 / 4800 + 10, abs=0.01)


def test_quadender_heads_divide_payback_per_gram():
    q = profiles.load_printer("quadender-pro-400")
    four, one = profiles.printer_rates(q), profiles.printer_rates(q, heads=1)
    assert four["heads_in_use"] == 4 and one["heads_in_use"] == 1
    assert four["grams_per_hour"] == 48 and one["grams_per_hour"] == 12
    assert one["payback_per_g"] == pytest.approx(4 * four["payback_per_g"], rel=0.01)
    assert four["printer_watts"] == 400 and one["printer_watts"] == 175


def test_service_price_orders_big_above_mid_above_small():
    names = ("qalam-pro-400", "qalam-300-pro", "railu")
    per_g = {p: price("service", printer=p).mine.customer / 200 for p in names}
    assert per_g[names[0]] > per_g[names[1]] > per_g[names[2]]
    assert 30 <= per_g[names[0]] <= 34 and 24 <= per_g[names[1]] <= 27 and 18 <= per_g[names[2]] <= 21
    quad = price("service", printer="quadender-pro-400").mine.customer / 200
    assert quad < per_g["railu"]
    assert price("service", printer="quadender-pro-400", heads=1).mine.customer / 200 > quad


def test_default_printer_per_profile_and_override():
    assert shop.build_settings(profile="product")["printer_name"] == "quadender-pro-400"
    assert shop.build_settings(profile="service")["printer_name"] == "qalam-pro-400"
    assert shop.build_settings(profile="service", printer="railu")["printer_name"] == "railu"
    assert "printer_name" not in shop.build_settings(profile="service", printer="none")
    assert "NOT YET IN SERVICE" in profiles.load_printer("qalam-300-pro")["note"]


def test_purge_waste_adds_filament():
    plain, purged = price("service"), price("service", prefs={"purge_waste_g": 20})
    assert purged.material_lines[0][1] == pytest.approx(plain.material_lines[0][1] + 20)
    assert any("purge waste" in n for n in purged.notes)


def test_cost_per_gram_comes_from_the_basis():
    basis = {**shop.DEFAULT_SETTINGS, **profiles.profile_settings(profiles.load_profile("service")),
             "machine_rate_per_hour": 100000 / 2400 + 10, "printer_watts": 150, "grams_per_hour": 12}
    cost = market.cost_per_gram(2500.0, basis)
    assert cost["cost"] == pytest.approx(11.5, abs=0.3)
    assert cost["labour"] == pytest.approx(400 * 20 / 60 / 100)


def test_minimum_order_applies_to_a_small_job():
    job = price("service", grams=10.0, hours=0.5)
    assert job.mine.customer == 400
    assert "Minimum order applied" in shop.format_job(job)
    assert "Suggested:     400" in shop.format_job(job)


def test_slow_fine_layer_print_costs_more():
    quick, slow = price("service", grams=50.0, hours=2.5), price("service", grams=50.0, hours=30.0)
    assert slow.mine.customer > quick.mine.customer
    assert "Slow print" in shop.format_job(slow)


def test_batches_default_to_cheapest_and_can_be_chosen():
    lib = shop.load_materials()
    assert lib["PETG"].price_per_kg == 2500 and lib["PETG"].batch == "A/B grade"
    assert set(lib["PETG"].batches) == {"A/B grade", "original"}
    job = price("product", prefs={"material": "PETG:original"})
    assert job.material_lines[0][2] == pytest.approx(600.0)            # 200 g at my price 3,000
    assert any("list price Rs 3,700" in n and "reference only" in n for n in job.notes)
    with pytest.raises(ValueError, match="no batch 'zzz'"):
        shop.select_batch(shop.load_materials()["PETG"], "zzz")


def test_material_prices_from_the_owner():
    lib = shop.load_materials()
    assert (lib["PET"].price_per_kg, lib["ABS"].price_per_kg, lib["PLA"].price_per_kg) == (2500, 3400, 3000)
    s = shop.build_settings(profile="product")
    s["order_kg"] = {"ABS": 12}
    job = shop.price_job({"ABS": 100.0}, 1.0, {}, s, lib, parts={}, inventory={})
    assert job.material_lines[0][2] == pytest.approx(325.0)             # more than 10 kg: 3,250 per kg


def test_pla_is_marked_estimate_and_remas_keys_follow_the_material():
    job = price("product", material="PLA")
    assert any("ESTIMATE" in n for n in job.notes)
    assert shop.load_materials()["PC-CF"].remas_key == "CF-PC 3dxtech"
    assert shop.load_materials()["TPU"].remas_key == "TPU 95A"


def test_missing_tpu_price_says_so_and_invents_nothing():
    job = price("service", material="TPU")
    text = shop.format_job(job)
    assert "set a price for TPU" in text
    assert job.breakdown.material_cost == 0
    cf = price("product", material="PC-CF")
    assert "set a price for PC-CF" in shop.format_job(cf)


def test_cli_profiles_list_show_set_use(config):
    def run(*args):
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True)
    assert "service" in run("profiles", "list").stdout and "product" in run("profiles", "list").stdout
    assert "minimum_price" in run("profiles", "show", "service").stdout
    r = run("profiles", "set", "service", "minimum_price=600", "include_design_cost=true")
    assert r.returncode == 0, r.stderr
    saved = json.loads((config / "profiles" / "service.json").read_text())
    assert saved["minimum_price"] == 600 and saved["include_design_cost"] is True
    assert json.loads((REPO / "data" / "profiles" / "service.json").read_text())["minimum_price"] == 400
    assert run("profiles", "set", "service", "bogus=1").returncode == 1
    assert run("profiles", "use", "service").returncode == 0
    assert shop.build_settings()["profile_name"] == "service"
    assert "qalam-pro-400" in run("printers", "list").stdout
    assert run("printers", "set", "railu", "cost=90000").returncode == 0
    assert profiles.printer_rates(profiles.load_printer("railu"))["machine_rate_per_hour"] == pytest.approx(90000 / 4800 + 10, abs=0.01)


def test_cli_price_with_profile_printer_and_material(tmp_path):
    gcode = tmp_path / "a.gcode"
    gcode.write_text("; filament used [g] = 200\n; layer_height = 0.16\n; outer_wall_line_width = 0.42\n"
                     "; filament_type = PETG\n; estimated printing time (normal mode) = 8h 0m 0s\n")
    r = subprocess.run([sys.executable, str(CLI), "price", str(gcode), "--profile", "service", "--printer", "railu",
                        "--material", "PETG:original"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for part in ("Profile: service", "layer 0.16 mm", "line 0.42 mm", "printer railu", "payback Rs", "[original]",
                 "market (Remas)"):
        assert part in r.stdout


def test_plugin_copies_match_core():
    plugin = REPO / "cura_plugin" / "PrintTally" / "PrintTally"
    assert (REPO / "core" / "profiles.py").read_text().split("import json", 1)[1] == \
           (plugin / "profiles.py").read_text().split("import json", 1)[1]
    for kind in ("profiles", "materials", "printers"):
        for f in (REPO / "data" / kind).glob("*.json"):
            assert f.read_text() == (plugin / "data" / kind / f.name).read_text(), f
