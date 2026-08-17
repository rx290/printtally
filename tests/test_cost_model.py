import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cost_model import CostInputs, calculate_cost


def test_material_cost_is_weight_times_price_per_gram():
    inputs = CostInputs(
        model_weight_g=100.0, price_per_kg=2000.0, print_hours=0.0,
        machine_rate_per_hour=0.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.0, profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    # 100g * (2000/1000) = 200.0
    assert result.material_cost == 200.0
    assert result.final_price == 200.0


def test_machine_cost_is_rate_times_hours():
    inputs = CostInputs(
        model_weight_g=0.0, price_per_kg=0.0, print_hours=3.0,
        machine_rate_per_hour=40.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.0, profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    assert result.machine_cost == 120.0
    assert result.final_price == 120.0


def test_electricity_cost_is_watts_hours_over_1000_times_rate():
    inputs = CostInputs(
        model_weight_g=0.0, price_per_kg=0.0, print_hours=4.0,
        machine_rate_per_hour=0.0, printer_watts=250.0, electricity_rate_per_kwh=10.0,
        waste_pct=0.0, profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    # (250 * 4) / 1000 * 10 = 10.0
    assert result.electricity_cost == 10.0
    assert result.final_price == 10.0


def test_waste_percentage_applies_to_production_costs_only():
    inputs = CostInputs(
        model_weight_g=100.0, price_per_kg=1000.0, print_hours=1.0,
        machine_rate_per_hour=50.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.10, profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    # material=100, machine=50, electricity=0 -> production=150 -> waste=15
    assert result.material_cost == 100.0
    assert result.machine_cost == 50.0
    assert result.waste_maintenance_cost == 15.0
    assert result.final_price == 165.0


def test_post_processing_cost_is_added_but_not_subject_to_waste_pct():
    inputs = CostInputs(
        model_weight_g=0.0, price_per_kg=0.0, print_hours=0.0,
        machine_rate_per_hour=0.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.10, post_processing_hours=2.0, post_processing_rate_per_hour=15.0,
        profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    assert result.post_processing_cost == 30.0
    # waste_pct applies only to production costs (all zero here), so waste
    # must be zero even though post-processing cost is nonzero
    assert result.waste_maintenance_cost == 0.0
    assert result.final_price == 30.0


def test_selling_cost_includes_fixed_packaging_shipping_and_marketing_fee():
    inputs = CostInputs(
        model_weight_g=0.0, price_per_kg=0.0, print_hours=0.0,
        machine_rate_per_hour=0.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.0, packaging_cost=5.0, shipping_cost=10.0,
        marketing_fee_pct=0.10, profit_margin_pct=0.0,
    )
    result = calculate_cost(inputs)
    # fixed selling = 15, pre-marketing subtotal = 15, marketing fee = 10% * 15 = 1.5
    assert result.selling_cost == 16.5
    assert result.final_price == 16.5


def test_profit_margin_applies_to_full_subtotal_including_selling_costs():
    inputs = CostInputs(
        model_weight_g=100.0, price_per_kg=1000.0, print_hours=0.0,
        machine_rate_per_hour=0.0, printer_watts=0.0, electricity_rate_per_kwh=0.0,
        waste_pct=0.0, profit_margin_pct=0.20,
    )
    result = calculate_cost(inputs)
    assert result.material_cost == 100.0
    assert result.subtotal == 100.0
    assert result.profit_margin == 20.0
    assert result.final_price == 120.0


def test_full_realistic_breakdown_matches_hand_calculation():
    # A representative real scenario: 45g print, $25/kg filament, 3.5hr print,
    # ₹40/hr machine rate, 250W printer, ₹12/kWh electricity, 7% waste,
    # 15 min post-processing at ₹500/hr, ₹8 packaging, ₹0 shipping (local),
    # 5% marketplace fee, 25% margin.
    inputs = CostInputs(
        model_weight_g=45.0, price_per_kg=2500.0, print_hours=3.5,
        machine_rate_per_hour=40.0, printer_watts=250.0, electricity_rate_per_kwh=12.0,
        waste_pct=0.07, post_processing_hours=0.25, post_processing_rate_per_hour=500.0,
        packaging_cost=8.0, shipping_cost=0.0, marketing_fee_pct=0.05,
        profit_margin_pct=0.25,
    )
    result = calculate_cost(inputs)

    expected_material = 45.0 * (2500.0 / 1000.0)          # 112.5
    expected_machine = 40.0 * 3.5                          # 140.0
    expected_electricity = (250.0 * 3.5 / 1000.0) * 12.0   # 10.5
    expected_production = expected_material + expected_machine + expected_electricity
    expected_waste = 0.07 * expected_production
    expected_post = 500.0 * 0.25                           # 125.0
    expected_fixed_selling = 8.0
    expected_pre_marketing = expected_production + expected_waste + expected_post + expected_fixed_selling
    expected_marketing_fee = 0.05 * expected_pre_marketing
    expected_subtotal = expected_pre_marketing + expected_marketing_fee
    expected_margin = 0.25 * expected_subtotal
    expected_final = expected_subtotal + expected_margin

    assert math.isclose(result.material_cost, expected_material, rel_tol=1e-6)
    assert math.isclose(result.machine_cost, expected_machine, rel_tol=1e-6)
    assert math.isclose(result.electricity_cost, expected_electricity, rel_tol=1e-6)
    assert math.isclose(result.waste_maintenance_cost, expected_waste, rel_tol=1e-3)
    assert math.isclose(result.post_processing_cost, expected_post, rel_tol=1e-6)
    assert math.isclose(result.subtotal, expected_subtotal, rel_tol=1e-3)
    assert math.isclose(result.profit_margin, expected_margin, rel_tol=1e-3)
    assert math.isclose(result.final_price, expected_final, rel_tol=1e-3)


@pytest.mark.parametrize("field,value", [
    ("model_weight_g", -1.0),
    ("price_per_kg", -1.0),
    ("print_hours", -1.0),
    ("waste_pct", 1.5),
    ("marketing_fee_pct", -0.1),
    ("profit_margin_pct", -0.05),
])
def test_invalid_inputs_raise_value_error(field, value):
    kwargs = dict(model_weight_g=10.0, price_per_kg=100.0, print_hours=1.0)
    kwargs[field] = value
    with pytest.raises(ValueError):
        calculate_cost(CostInputs(**kwargs))


def test_zero_weight_and_zero_hours_gives_zero_cost():
    inputs = CostInputs(
        model_weight_g=0.0, price_per_kg=100.0, print_hours=0.0,
        machine_rate_per_hour=40.0, printer_watts=250.0, electricity_rate_per_kwh=12.0,
        waste_pct=0.07, profit_margin_pct=0.25,
    )
    result = calculate_cost(inputs)
    assert result.final_price == 0.0
