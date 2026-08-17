# PrintTally

A print-cost calculator for Ultimaker Cura and OrcaSlicer/PrusaSlicer, built around a real six-factor pricing formula (material, machine time, electricity, waste/maintenance, post-processing labor, and selling costs, with profit margin on top) instead of the common mistake of pricing a print at filament cost alone.

## Why this isn't one plugin ported twice

OrcaSlicer and PrusaSlicer have no public plugin API for custom UI — confirmed directly by an OrcaSlicer maintainer-side discussion, not assumed. Cura does have a real plugin SDK (a documented `Script` base class for post-processing scripts). So this is one shared cost formula with two genuinely different front ends:

- **`cura_plugin/`** — a real Cura post-processing script. Install it, set your rates once in its settings panel, and every G-code export gets a cost breakdown appended automatically, using Cura's own real print-weight and print-time data (no G-code parsing needed on this side).
- **`orca_prusa_script/`** — a standalone Python script configured as an OrcaSlicer/PrusaSlicer post-processing script (Print Settings → Output options → Post-processing scripts). It reads the slicer's own G-code footer (filament weight, estimated print time — the exact comment format was confirmed directly against OrcaSlicer's source, not guessed) and runs the same formula.
- **`core/cost_model.py`** — the one place the actual math lives, fully unit-tested. The Cura script duplicates this formula (Cura scripts are distributed as single self-contained files, so it can't import a sibling module) but a fuzz test (`tests/test_cura_script_matches_core.py`) checks both implementations against 50 random inputs every time either changes, so they can't silently drift apart.

## The formula

```
1. Material cost      = model weight (g) x (price per kg / 1000)
2. Machine cost        = machine hourly rate x print hours
3. Electricity cost     = (printer watts x print hours / 1000) x electricity rate per kWh
4. Waste/maintenance    = waste % x (1 + 2 + 3)
5. Post-processing cost = labor rate x post-processing hours
6. Selling costs        = packaging + shipping + (marketing fee % x running subtotal)

Subtotal     = 1 + 2 + 3 + 4 + 5 + 6
Profit margin = margin % x Subtotal
Final price   = Subtotal + Profit margin
```

One judgment call worth flagging: the source material describes waste/maintenance as "5-10% of total cost," but at the point that line item is calculated, the full total isn't computed yet (post-processing, selling costs, and margin come after it). Applying a percentage to the *already-final* total would mean margin compounding on margin, so this implementation scopes the waste percentage to the three direct production costs (material, machine, electricity) instead. If you want it scoped differently, `core/cost_model.py` is a plain function — change `production_subtotal` to whatever base you want the percentage applied to.

## Installing the Cura plugin

Copy `cura_plugin/PrintTallyCostCalculator.py` into your Cura scripts folder (Help → Show Configuration Folder → `scripts/`), restart Cura, then add it from Extensions → Post Processing → Modify G-Code → Add a script.

## Using the OrcaSlicer/PrusaSlicer script

In Print Settings → Output options → Post-processing scripts, add:

```
/path/to/printtally_postprocess.py --price-per-kg 2500 --machine-rate 40 --watts 250 --electricity-rate 12 --waste-pct 0.07 --margin-pct 0.25
```

The slicer appends the G-code file path as the final argument automatically. The breakdown gets appended as a comment block at the end of the sliced file, and also printed to stdout (visible in the slicer's post-processing log).

## Tests

```
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

22 tests: the core formula (unit tests plus one hand-verified realistic scenario), the OrcaSlicer/PrusaSlicer script (against a footer shaped exactly like OrcaSlicer's real source output, plus error-path tests for a missing weight/time footer), and the Cura-script-vs-core parity fuzz test.

## Status

Core formula and both adapters built and tested (2026-08-18). The Cura plugin has been confirmed to load cleanly in a real Cura 5.13 installation with no exceptions. Not yet verified against a real OrcaSlicer CLI slice end-to-end (the CLI slicing in this dev environment needs a properly-typed printer/process profile pair that wasn't readily available — the footer-parsing logic is instead verified against OrcaSlicer's actual source code strings). If you hit a parsing error against a real slice, the exact real footer line is the thing to check first.
