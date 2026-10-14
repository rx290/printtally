#!/usr/bin/env python3
"""PrintTally command line.

    python printtally.py price model.gcode            price a sliced job (Cura or Orca/Prusa G-code)
    python printtally.py price model.gcode --model model.3mf
                                                       ... using the preference files of that model
    python printtally.py init                          write starter materials/parts/settings files

Your files live in ~/.config/printtally ($PRINTTALLY_CONFIG overrides). Preference files
(printtally.json) sit beside a model or in any folder above it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import shop, gcode_stats, sheet_sync  # noqa: E402

TEMPLATES = {
    "materials.json": {name: {"price_per_kg": 0, "density_g_cm3": d, "aliases": a}
                       for name, (d, a) in shop.DEFAULT_MATERIALS.items()},
    "parts.json": {"example_switch": {"price": 0, "note": "add your real parts and prices"}},
    "settings.json": shop.DEFAULT_SETTINGS,
}


def cmd_init(_args):
    d = shop.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name, data in TEMPLATES.items():
        path = d / name
        if path.exists():
            print(f"kept     {path}")
            continue
        path.write_text(json.dumps(data, indent=2) + "\n")
        print(f"wrote    {path}")


def cmd_price(args):
    gcode = Path(args.gcode)
    stats = gcode_stats.parse(gcode.read_text(errors="ignore"))
    prefs, used = shop.find_preferences(args.model or gcode)
    settings = {**shop.DEFAULT_SETTINGS, **shop._read_json(shop.config_dir() / "settings.json", {})}
    materials = shop.load_materials()
    grams = gcode_stats.grams_by_material(stats, materials, prefs.get("material", ""),
                                          float(settings.get("filament_diameter_mm", 1.75)))
    job = shop.price_job(grams, stats.hours, prefs, settings, materials)
    print(f"{gcode.name}: {stats.slicer}, {stats.hours:.2f} h")
    if used:
        print("Preferences: " + ", ".join(used))
    print(shop.format_job(job))


def cmd_sync_sheet(args):
    for message in sheet_sync.sync_sheet(args.url):
        print(message)


def cmd_price_history(args):
    rows = sheet_sync.price_history(args.name)
    for row in rows:
        print(f"{row['date']} {row['kind']} {row['name']}: {row['price']} (synced {row['synced_at']})")
    if not rows:
        print(f"No price history for {args.name}.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="printtally")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("price", help="price a sliced G-code file")
    p.add_argument("gcode")
    p.add_argument("--model", help="the model file whose printtally.json preferences apply")
    p.set_defaults(func=cmd_price)
    sub.add_parser("init", help="write starter config files").set_defaults(func=cmd_init)
    p = sub.add_parser("sync-sheet", help="sync prices from a published Google Sheet CSV")
    p.add_argument("url", nargs="?", metavar="URL", help="published CSV URL (saved for later syncs)")
    p.set_defaults(func=cmd_sync_sheet)
    p = sub.add_parser("price-history", help="show a material or part's dated prices")
    p.add_argument("name", metavar="NAME")
    p.set_defaults(func=cmd_price_history)
    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (OSError, ValueError) as exc:
        ap.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
