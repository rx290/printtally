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

## Your price and the market price

Every print shows how YOUR price is built, then the market (Remas) price for comparison:

```
YOUR COST:     Rs 798
YOUR PRICE (200 g, 4.00 h, under 500 g):
    Filament                   Rs 500
  + Electricity                 Rs 90
  + Machine wear               Rs 106
  + Failure allowance           Rs 77
  + Labour                      Rs 25
  + Profit                   Rs 1,762
  = YOUR PRICE             Rs 2,560   (reseller Rs 2,120)
market (Remas): customer Rs 6,000, reseller Rs 5,000
Gap to market: Rs 3,440 below Remas (customer)
Suggested:     Rs 2,550  (your price)
```

The suggested price comes from your own card (rounded to Rs 50). The Remas card (`data/market_rates_pk.json`, October 2026, PKR per gram of finished part) is only a reference. It becomes the suggestion only if you choose `--suggest remas` or `remas_reseller`.

### How a per-gram price is built

A shop quotes one number per gram, but that number has to cover everything it costs to make a gram of part. For one gram:

1. Filament: your price per kg divided by 1000 (Rs 2,500/kg is Rs 2.5 per gram).
2. Electricity: printer kW x your Rs per unit, spread over the grams printed each hour. A 0.4 mm nozzle makes roughly 10-15 g per hour (`grams_per_hour`, default 12), so one hour of power is shared across those grams.
3. Machine wear: your machine rate per hour, shared the same way.
4. Labour: your Rs per hour x the minutes you spend per 100 g (support removal, finishing, packing).
5. Failure allowance: failed prints are paid for by the good ones, so the sum above is divided by (1 - failure rate). A 10% failure rate adds about 11%.
6. Profit: the cost is multiplied by `profit_multiplier` (2.0 means the price is twice the cost).

Then the tiers: heavier parts get a lower rate (`tier_discounts`, default 0, 15% and 30% off for under 500 g, 500-1000 g, above 1000 g), resellers pay `reseller_discount` less (default 17%), and exotic materials can carry a fixed `setup_charges` entry. To tune your prices, change one of these numbers and rebuild, or edit the card by hand. If a part prints much slower or faster than 12 g per hour, the per-gram rate is a little off for it; the breakdown above uses the real print time, so compare the two.

### Build and edit your card

```
python printtally.py build-card                      # writes ~/.config/printtally/my_rates.json
python printtally.py build-card --profit-multiplier 2.5 --grams-per-hour 10 --tier-discounts 0,0.1,0.25 --out my.json
```

It needs your material prices in `materials.json` and uses your `settings.json` (electricity, printer watts, machine rate, failure rate). All the numbers above can also sit in `settings.json` (`labour_rate_per_hour`, `labour_min_per_100g`, `grams_per_hour`, `profit_multiplier`, `tier_discounts`, `reseller_discount`, `setup_charges`). The card has the same shape as the Remas card (material -> `customer` and `reseller` rates for three tiers, optional `setup`), so edit any value. It never overwrites an existing card without `--force`. Without a card file, PrintTally builds the same card on the fly from your settings.

### Settings and flags

| Setting | Flag | Meaning |
|---|---|---|
| `my_rates_path` | `--my-rates FILE` (CLI and Orca/Prusa script) | your card; default `my_rates.json` in the config folder |
| `market_rates_path` | `--rates FILE` (CLI), `--market-rates FILE` (script) | the reference card; `$PRINTTALLY_MARKET_RATES` also works |
| `suggest_mode` | `--suggest mine\|reseller\|remas\|remas_reseller\|multiplier` | how the suggested price is chosen, default `mine` |
| `cost_multiplier` | `--multiplier N`, `--cost-multiplier N` | used by the multiplier mode (cost x N) |

Material names map onto the cards: PETG is "PETG", PLA is "PLA+", TPU is "TPU 95A"; other names match by name. Set `"market_material": "CF-PETG"` in a model's `printtally.json` to pick a card entry yourself. A material missing from a card is reported and that price is not shown. Set `electricity_rate_per_kwh` (for example 90 for Rs 90 per unit) and `printer_watts` for real electricity. The Orca/Prusa script needs `--material PETG` for these lines; the Cura plugin shows them in its text and widget.

## Profiles

Three things decide a price, and each one is a small file you can list, show and edit:

- a **pricing profile** (how you charge),
- a **printer** (what the machine costs to run),
- a **material** (what the filament costs you).

**When to use which pricing profile.** Lamps and custom jobs: use `service`. Product lines such as the dino kits: use `product`.

- `service` is a fair, payback-based price. The cost of one gram is worked out from the printer's payback and upkeep, electricity, filament (with 10% waste), 10% failed prints, 20 minutes of labour per order at Rs 400 an hour spread over a typical 100 g order, and 10% overhead for packaging, internet and marketing. The price is that cost times 2, so the extra pays for the next printer. About Rs 11.6 of cost per gram on a mid-priced printer becomes about Rs 24 to 32 to the customer. There is a minimum order of Rs 400. A slow, fine-layer print is never priced below its real time cost times 1.5. The Remas line is always shown next to it as the market reference. Remas is a reference only: the service price is never copied from it.
- `product` keeps the original PrintTally settings exactly as they were (machine Rs 40 an hour, 7% waste, 25% margin, own card at cost x 2 on a 12 g per hour basis, no minimum). It is cheap on purpose, for kits. If you want a product card nearer cost x 1.4, generate one yourself: `printtally.py build-card --profile product --profit-multiplier 1.4 --out product_rates.json`. Your own settings are never overwritten.

```sh
python3 printtally.py profiles list
python3 printtally.py profiles show service
python3 printtally.py profiles set service minimum_price=500 labour_rate_per_hour=450
python3 printtally.py profiles use service          # your default when you pass nothing
python3 printtally.py price job.gcode --profile service --printer railu --material PETG:original
```

Shipped defaults live in `data/profiles/`. Your edits go to `~/.config/printtally/profiles/<name>.json` (a file there only needs the numbers you changed). An old `settings.json` with pricing values moves into the `product` profile automatically until you save your own. Pick a profile with `--profile`, `$PRINTTALLY_PROFILE`, `"profile"` in a `printtally.json` beside the model, or the default above. The OrcaSlicer script takes `--profile`, `--printer`, `--heads` and `--material`; any other flag overrides one value of the profile. The Cura plugin has Profile and Printer dropdowns in its settings (left on "(default)" it reads your default and the result says which profile it used; with nothing chosen anywhere it keeps using the plugin's own fields).

### Printers

`printtally.py printers list | show NAME | set NAME key=value | use NAME`. A printer file holds its cost, payback years, paid hours per year (2,400), grams per hour per head (12), heads, watts and maintenance per hour (Rs 10). The machine rate is `cost / (payback years x paid hours per year) + maintenance`. To re-derive the rates, change the cost, `payback_years` or `paid_hours_per_year` (hours per day x 365) of a printer and the price follows. Each profile names a default printer (`product`: `quadender-pro-400`, `service`: `qalam-pro-400`); `printers use NAME` or `--printer NAME` overrides it, and `--printer none` uses the profile's own machine values.

The Quadender Pro 400 prints with up to 4 heads at once, so its throughput is 4 x 12 g per hour and the payback per gram is divided by the heads in use. Use `--heads 1` for a single-part job. On the Railu, set `purge_waste_g` for multi-colour jobs, because purged filament is paid for. The Qalam 300 Pro is not yet in service but can be selected.

| Printer | Cost (Rs) | Payback per g at 1 year | at 2 years | Service price per g (PETG, 200 g, 8 h) |
|---|---|---|---|---|
| Qalam Pro 400 | 400,000 | 13.9 | 6.9 | about 32 |
| Qalam 300 Pro (not yet in service) | 250,000 | 8.7 | 4.3 | about 25 |
| Quadender Pro 400 (4 heads) | 375,000 | 3.3 | 1.6 | about 16 |
| Railu | 100,000 | 3.5 | 1.7 | about 19 |

(The Quadender prices low per gram because four heads share one machine rate; with one head in use it is about 31 Rs per gram.)

### Materials

Material files live in `data/materials/` (your copies in `~/.config/printtally/materials/`). Each has batches with `my_price` (what you pay through your reference, used in all the maths) and optionally `list_price` (the open-market price, shown only as a note). Pick a batch with `--material PETG:original`; the default is the cheapest. ABS has quantity tiers on `my_price` (Rs 3,400 up to 10 kg, Rs 3,250 above), chosen with `"order_kg": {"ABS": 12}` in settings.json. PLA is marked ESTIMATE (Rs 3,000 within a 2,500 to 3,500 range). TPU, PC-CF and ASA have no price yet: PrintTally prints "set a price for TPU" and leaves it out rather than inventing one. Each material carries its key on the Remas card (PC-CF uses `CF-PC 3dxtech`). Electricity is shop-wide in `settings.json` (default Rs 90 a unit). `printtally.py materials` lists everything.

Every price starts with the real print factors from the G-code: print time, layer height, line width, grams, and electricity from the printer's watts times the time.

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
