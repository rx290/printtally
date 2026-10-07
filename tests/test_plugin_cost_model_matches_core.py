"""
Verifies cura_plugin/PrintTally/PrintTally/cost_model.py stays byte-for-byte
identical to core/cost_model.py below the module docstring (the only place
they're allowed to differ, since the plugin copy explains *why* it's a copy).

The two are separate files -- not one shared import -- because once
cura_plugin/PrintTally is installed into Cura's plugins directory it's not on
the same Python path as the repo root, and Cura plugin folders (unlike the
single-file post-processing scripts this replaced) *can* hold multiple
modules, so this copy is intentional, not a workaround.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_PATH = REPO_ROOT / "core" / "cost_model.py"
PLUGIN_PATH = REPO_ROOT / "cura_plugin" / "PrintTally" / "PrintTally" / "cost_model.py"

SPLIT_MARKER = "from dataclasses import dataclass, field"


def _code_after_docstring(path: Path) -> str:
    text = path.read_text()
    assert SPLIT_MARKER in text, f"{path} is missing the expected split marker"
    return text.split(SPLIT_MARKER, 1)[1]


def test_plugin_cost_model_code_matches_core_below_docstring():
    core_code = _code_after_docstring(CORE_PATH)
    plugin_code = _code_after_docstring(PLUGIN_PATH)
    assert core_code == plugin_code, (
        "cura_plugin/PrintTally/PrintTally/cost_model.py has drifted from "
        "core/cost_model.py -- copy core's file over it (keeping the plugin "
        "copy's own docstring) and re-run tests."
    )


def test_plugin_shop_code_matches_core_below_docstring():
    core = (REPO_ROOT / "core" / "shop.py").read_text().split(SPLIT_MARKER, 1)[1]
    plugin = (REPO_ROOT / "cura_plugin" / "PrintTally" / "PrintTally" / "shop.py").read_text().split(SPLIT_MARKER, 1)[1]
    assert core == plugin, "re-copy core/shop.py into the plugin (below its docstring)"
