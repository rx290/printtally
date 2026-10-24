# PrintTally

A print-cost calculator for Ultimaker Cura and OrcaSlicer/PrusaSlicer, built around a real seven-factor pricing formula (material, printing/machine time, electricity, maintenance, labor, design cost, and selling costs, with profit margin on top) instead of the common mistake of pricing a print at filament cost alone.

## Why this isn't one plugin ported twice

OrcaSlicer and PrusaSlicer have no public plugin API for custom UI — confirmed directly by an OrcaSlicer maintainer-side discussion, not assumed. Cura does have a real plugin SDK, with real Extension/QML support for a proper GUI. So this is one shared cost formula with two genuinely different front ends:

- **`cura_plugin/PrintTally/`** — a real Cura plugin (Extension), not a post-processing script. It adds a **PrintTally** entry to Cura's Extensions menu with a Settings dialog (your rates, persisted in Cura's own preferences) and a live price readout next to the Save-to-Disk button, using Cura's own real print-weight and print-time data as soon as you slice.
- **`orca_prusa_script/`** — a standalone Python script configured as an OrcaSlicer/PrusaSlicer post-processing script (Print Settings → Output options → Post-processing scripts). It reads the slicer's own G-code footer (filament weight, estimated print time — the exact comment format was confirmed directly against OrcaSlicer's source, not guessed) and runs the same formula.
- **`core/cost_model.py`** — the one place the actual math lives, fully unit-tested. `cura_plugin/PrintTally/PrintTally/cost_model.py` is a byte-for-byte copy of it (a Cura plugin folder isn't on the same Python path as the repo root once installed, so it can't import the sibling module directly) — `tests/test_plugin_cost_model_matches_core.py` diffs the two files below the docstring on every change, so they can't silently drift apart.

## The formula

```
1. Material cost   = model weight (g) x (price per kg / 1000)
2. Printing cost    = machine hourly rate x print hours
3. Electricity cost  = (printer watts x print hours / 1000) x electricity rate per kWh
4. Maintenance cost  = waste % x (1 + 2 + 3)
5. Labor cost        = labor rate x post-processing hours (support removal, finishing, QC)
6. Design cost       = design rate x design hours -- ONLY IF "include design cost" is on
7. Selling costs     = packaging + shipping + (marketing fee % x running subtotal)

Subtotal     = 1 + 2 + 3 + 4 + 5 + 6 + 7
Profit margin = margin % x Subtotal
Final price   = Subtotal + Profit margin
```

**Design cost is a real on/off switch, not just a rate to remember to zero out.** Most print jobs are repeats of a design you already have, and shouldn't carry a design charge — leave "include design cost" unchecked and `design_hours`/`design_rate_per_hour` have zero effect on the price no matter what they're set to. Turn it on only for a genuinely custom design job. Verified by a dedicated test (`test_design_cost_is_zero_when_toggle_is_off_even_with_nonzero_hours_and_rate`) that sets both fields to large nonzero values with the toggle off and asserts the price doesn't move.

One judgment call worth flagging: the source material describes maintenance cost as "5-10% of total cost," but at the point that line item is calculated, the full total isn't computed yet (labor, design, selling costs, and margin come after it). Applying a percentage to the *already-final* total would mean margin compounding on margin, so this implementation scopes the waste percentage to the three direct production costs (material, printing, electricity) instead. If you want it scoped differently, `core/cost_model.py` is a plain function — change `production_subtotal` to whatever base you want the percentage applied to.

## Installing the Cura plugin

Copy `cura_plugin/PrintTally/PrintTally/` into your Cura plugins folder (Help → Show Configuration Folder → `plugins/PrintTally/PrintTally/`, so `plugin.json` ends up double-nested the way Cura expects) and restart Cura. You'll get a **PrintTally** entry under the Extensions menu:

- **Extensions → PrintTally → Settings…** — set your rates once (material, machine, electricity, waste %, labor, design cost toggle, packaging/shipping/marketing, profit margin). Saved in Cura's own preferences.
- Slice a model and a live price shows up next to the Save-to-Disk button, pulling Cura's own real weight/print-time estimate. Hover it for the full breakdown, click it to reopen Settings.

## Using the OrcaSlicer/PrusaSlicer script

In Print Settings → Output options → Post-processing scripts, add:

```
/path/to/printtally_postprocess.py --price-per-kg 2500 --machine-rate 40 --watts 250 --electricity-rate 12 --waste-pct 0.07 --margin-pct 0.25 --include-design-cost --design-hours 1.5 --design-rate 500
```

Omit `--include-design-cost` (and the two design flags become no-ops) for a repeat print of an existing design.

The slicer appends the G-code file path as the final argument automatically. The breakdown gets appended as a comment block at the end of the sliced file, and also printed to stdout (visible in the slicer's post-processing log).

## Market price

Every print now shows two numbers: YOUR COST (filament, electricity, machine wear, failure allowance, parts and the other cost lines) and the MARKET PRICE a print shop would charge for the same part. The market price comes from a per-gram rate card (`data/market_rates_pk.json`, the Remas card from October 2026, PKR per gram of finished part). Price = grams x rate + setup charge. PrintTally then shows your margin (market minus your cost) and a suggested price.

```
YOUR COST:     Rs 773
MARKET PRICE:  customer Rs 6,000, reseller Rs 5,000  (under 500 g)
Margin:        Rs 5,227 vs customer, Rs 4,227 vs reseller
Suggested:     Rs 6,000  (customer price)
```

How to read it:

- A per-gram rate looks simple, but it hides machine time, failed prints, labour and profit. Your cost shows what is really behind the number, so you can see where a job is cheap or expensive for you.
- There are volume tiers: under 500 g, 500-1000 g and above 1000 g. The tier is picked by the part's total grams, and heavier parts get a lower rate.
- Resellers pay about 15-20% less than customers. Both prices are shown.
- Exotic materials (PC, nylon, CF-PC) carry a fixed setup charge, added once per material.
- Material names map onto the card: PETG is "PETG", PLA is "PLA+", TPU is "TPU 95A". Other names match the card by name (ABS, ASA, PET, PP, CF-PETG, ...). Set `"market_material": "CF-PETG"` in a model's `printtally.json` to pick a card entry yourself. If a material is not on the card, PrintTally says so and shows no market price.

Settings (in `settings.json`, a model's `printtally.json` under `"settings"`, or flags):

| Setting | Flag | Meaning |
|---|---|---|
| `market_rates_path` | `--rates FILE` (CLI), `--market-rates FILE` (Orca/Prusa script) | another rate card; `$PRINTTALLY_MARKET_RATES` also works |
| `suggest_mode` | `--suggest customer\|reseller\|multiplier` | how the suggested price is chosen, default customer price rounded to Rs 50 |
| `cost_multiplier` | `--multiplier N`, `--cost-multiplier N` | used by the multiplier mode (your cost x N) |

For your own electricity, set `electricity_rate_per_kwh` (for example 90 for Rs 90 per unit) and `printer_watts`. The Orca/Prusa script needs `--material PETG` to show the market lines. The Cura plugin shows the same lines in its text and widget, using the material Cura reports or the filament profile you picked.

## Tests

```
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -v
```

29 tests: the core formula (unit tests, the design-cost toggle's critical on/off edge case, plus one hand-verified realistic scenario), the OrcaSlicer/PrusaSlicer script (against a footer shaped exactly like OrcaSlicer's real source output, plus error-path tests for a missing weight/time footer and the design-cost flag), and the Cura-plugin-vs-core cost-model parity check.

## Status

Core formula and both adapters built and tested (2026-08-18), extended with a seventh factor (design cost, checkbox-gated) the same day. Rebuilt as a real Cura Extension plugin with a settings dialog and live price widget (2026-08-26), replacing the earlier post-processing-script approach — confirmed working end-to-end in a real Cura 5.13 installation, including catching and fixing two real QML bugs (`UM.ToolTip` needing the `UM 1.5` import, and `UM.Dialog`'s default `contents` property rejecting a non-visual `I18nCatalog` child) via Cura's own log, not guesswork. Not yet verified against a real OrcaSlicer CLI slice end-to-end (the CLI slicing in this dev environment needs a properly-typed printer/process profile pair that wasn't readily available — the footer-parsing logic is instead verified against OrcaSlicer's actual source code strings). If you hit a parsing error against a real slice, the exact real footer line is the thing to check first.

### Real grams (weigh_factor)
Set `"weigh_factor": 1.07` on a material in materials.json (or let printkit write it: `python -m printkit weigh --material PETG --slicer 120 --scale 128`) and PrintTally prices that filament at slicer grams x the factor.

## Prices from a Google Sheet

I keep dated prices in a sheet with these headers (case-insensitive): `date`
(`YYYY-MM-DD`), `kind` (`material` or `part`), `name`, `price` (per kg for
materials, per unit for parts). Optional columns: `density_g_cm3`,
`weigh_factor`, `aliases` (semicolon separated), `notes`.

In Google Sheets, choose **File > Share > Publish to web**, select the sheet
and **Comma-separated values (.csv)**, then click **Publish** and copy the URL.
Publishing makes the sheet public. Run:

```sh
python3 printtally.py sync-sheet 'https://docs.google.com/spreadsheets/d/e/.../pub?gid=0&single=true&output=csv'
python3 printtally.py sync-sheet
python3 printtally.py price-history 'PLA'
```

The URL is saved in `settings.json`; supplying another URL replaces it. The latest
date on or before today supplies each price (last row wins a date tie). Blank
optional cells preserve local fields, and unlisted local entries stay intact.
Network failures use `sheet_cache.csv` with a message. Every distinct dated price,
including future prices and revisions, is retained in `price_history.csv`.
Files live in `~/.config/printtally`, or `$PRINTTALLY_CONFIG` when set.


**Inventory.** Publish the Inventory tab too (columns `material, color, stock_g, reorder_at_g`) and run `printtally sync-sheet --inventory <CSV link>`. Stock is summed per material; a job that needs more filament than you have, or drops below the reorder level, gets a warning in the price and in Cura.

### Failed prints and monthly costs
Set `"failure_rate": 0.1` in settings.json (or a model's printtally.json) and every price adds the cost of the failed tries: cost to print / (1 - rate), so 10 % failures adds about 11 %. It shows as its own "Failed prints" line.

For a fixed monthly cost such as a design subscription, add to the model's printtally.json: `"monthly_fixed": [{"name": "Dino kit royalty", "amount": 1400, "units_per_month": 20}]`. Each kit then carries 1400 / 20 = Rs 70 as its own line.
