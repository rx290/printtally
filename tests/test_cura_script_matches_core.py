"""
Verifies cura_plugin/PrintTallyCostCalculator.py's duplicated _calculate_cost
stays byte-for-byte behaviorally identical to core/cost_model.py's
calculate_cost() -- the two are deliberately separate files (Cura scripts
must be single self-contained files) but must never drift apart.

Loads the real Cura script file directly (not a paraphrase of its logic),
stubbing out only the two Cura-specific imports it needs at import time
(UM.Application and the relative ..Script import) so it can run outside a
real Cura installation.
"""
import importlib.util
import random
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from core.cost_model import CostInputs, calculate_cost


def _load_cura_script_module():
    # Stub UM.Application before the script imports it.
    um_module = types.ModuleType("UM")
    um_application_module = types.ModuleType("UM.Application")

    class _FakeApplication:
        @staticmethod
        def getInstance():
            raise RuntimeError("not used directly in this test -- _calculate_cost is called standalone")

    um_application_module.Application = _FakeApplication
    sys.modules["UM"] = um_module
    sys.modules["UM.Application"] = um_application_module

    # Stub the parent package + its Script module for the "from ..Script import Script" line.
    fake_pkg = types.ModuleType("fake_postprocessing_pkg")
    fake_pkg.__path__ = []
    fake_script_module = types.ModuleType("fake_postprocessing_pkg.Script")

    class _FakeScript:
        def getSettingValueByKey(self, key):
            raise NotImplementedError

    fake_script_module.Script = _FakeScript
    sys.modules["fake_postprocessing_pkg"] = fake_pkg
    sys.modules["fake_postprocessing_pkg.Script"] = fake_script_module

    fake_scripts_subpkg = types.ModuleType("fake_postprocessing_pkg.scripts")
    fake_scripts_subpkg.__path__ = []
    sys.modules["fake_postprocessing_pkg.scripts"] = fake_scripts_subpkg

    script_path = REPO_ROOT / "cura_plugin" / "PrintTallyCostCalculator.py"
    spec = importlib.util.spec_from_file_location(
        "fake_postprocessing_pkg.scripts.PrintTallyCostCalculator", script_path
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "fake_postprocessing_pkg.scripts"
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cura_module():
    return _load_cura_script_module()


def _random_kwargs(rng):
    return dict(
        model_weight_g=rng.uniform(0, 500),
        price_per_kg=rng.uniform(0, 5000),
        print_hours=rng.uniform(0, 40),
        machine_rate_per_hour=rng.uniform(0, 100),
        printer_watts=rng.uniform(0, 500),
        electricity_rate_per_kwh=rng.uniform(0, 50),
        waste_pct=rng.uniform(0, 1),
        post_processing_hours=rng.uniform(0, 5),
        post_processing_rate_per_hour=rng.uniform(0, 1000),
        include_design_cost=rng.choice([True, False]),
        design_hours=rng.uniform(0, 10),
        design_rate_per_hour=rng.uniform(0, 2000),
        packaging_cost=rng.uniform(0, 50),
        shipping_cost=rng.uniform(0, 100),
        marketing_fee_pct=rng.uniform(0, 1),
        profit_margin_pct=rng.uniform(0, 1),
    )


def test_cura_script_and_core_module_agree_on_50_random_inputs(cura_module):
    rng = random.Random(42)
    for _ in range(50):
        kwargs = _random_kwargs(rng)

        core_result = calculate_cost(CostInputs(**kwargs))
        cura_result = cura_module.PrintTallyCostCalculator._calculate_cost(**kwargs)

        assert cura_result["material_cost"] == core_result.material_cost
        assert cura_result["machine_cost"] == core_result.machine_cost
        assert cura_result["electricity_cost"] == core_result.electricity_cost
        assert cura_result["waste_maintenance_cost"] == core_result.waste_maintenance_cost
        assert cura_result["post_processing_cost"] == core_result.post_processing_cost
        assert cura_result["design_cost"] == core_result.design_cost
        assert cura_result["selling_cost"] == core_result.selling_cost
        assert cura_result["subtotal"] == core_result.subtotal
        assert cura_result["profit_margin"] == core_result.profit_margin
        assert cura_result["final_price"] == core_result.final_price


def test_cura_script_design_cost_toggle_matches_core_when_off(cura_module):
    """The critical case in the Cura script specifically, not just core:
    design_hours/design_rate_per_hour must have zero effect when the
    include_design_cost checkbox is off, exactly like core.cost_model."""
    kwargs = _random_kwargs(random.Random(7))
    kwargs["include_design_cost"] = False
    kwargs["design_hours"] = 8.0
    kwargs["design_rate_per_hour"] = 5000.0

    core_result = calculate_cost(CostInputs(**kwargs))
    cura_result = cura_module.PrintTallyCostCalculator._calculate_cost(**kwargs)

    assert cura_result["design_cost"] == 0.0
    assert core_result.design_cost == 0.0
    assert cura_result["final_price"] == core_result.final_price


def test_cura_script_rejects_same_invalid_inputs_as_core(cura_module):
    base = _random_kwargs(random.Random(1))
    for field, bad_value in [
        ("model_weight_g", -1.0), ("price_per_kg", -1.0), ("print_hours", -1.0),
        ("waste_pct", 1.5), ("marketing_fee_pct", -0.1), ("profit_margin_pct", -0.05),
        ("design_hours", -1.0), ("design_rate_per_hour", -1.0),
    ]:
        kwargs = dict(base)
        kwargs[field] = bad_value
        with pytest.raises(ValueError):
            cura_module.PrintTallyCostCalculator._calculate_cost(**kwargs)
        with pytest.raises(ValueError):
            calculate_cost(CostInputs(**kwargs))
