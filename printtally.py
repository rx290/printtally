#!/usr/bin/env python3
"""PrintTally command line.

    python printtally.py price model.gcode            price a sliced job (Cura or Orca/Prusa G-code)
    python printtally.py price model.gcode --model model.3mf
                                                       ... using the preference files of that model
    python printtally.py build-card                    generate YOUR per-gram rate card from your costs, then edit it
    python printtally.py price model.gcode --profile service --printer small --material PETG:original
    python printtally.py profiles list | show NAME | set NAME key=value | use NAME
    python printtally.py printers list | show NAME | set NAME key=value | use NAME
    python printtally.py materials                     list material profiles, batches and prices
    python printtally.py init                          write starter materials/parts/settings files

Your files live in ~/.config/printtally ($PRINTTALLY_CONFIG overrides). Preference files
(printtally.json) sit beside a model or in any folder above it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import shop, gcode_stats, sheet_sync, profiles  # noqa: E402

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
    if args.material:                    # --material PETG[:batch] prices every gram as that material
        prefs["material"] = args.material
    settings = shop.build_settings(profile=args.profile, printer=args.printer, heads=args.heads, prefs=prefs)
    if args.rates:
        settings["market_rates_path"] = args.rates
    if args.my_rates:
        settings["my_rates_path"] = args.my_rates
    if args.suggest:
        settings["suggest_mode"] = args.suggest
    if args.multiplier is not None:
        settings["cost_multiplier"] = args.multiplier
    materials = shop.load_materials()
    grams = gcode_stats.grams_by_material(stats, materials, shop.parse_choice(prefs.get("material", ""))[0],
                                          float(settings.get("filament_diameter_mm", 1.75)))
    if args.material:
        grams = {shop.parse_choice(args.material)[0]: sum(grams.values())}
    factors = {"layer_height": stats.layer_height, "line_width": stats.line_width}
    job = shop.price_job(grams, stats.hours, prefs, settings, materials, factors=factors)
    print(f"{gcode.name}: {stats.slicer}, {stats.hours:.2f} h")
    if used:
        print("Preferences: " + ", ".join(used))
    print(shop.format_job(job))


def _kv(text):
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise ValueError("Use key=value, e.g. profit_multiplier=1.8")
    return key.strip(), profiles.parse_value(value.strip())


def _show(data):
    for k, v in data.items():
        print(f"  {k:<28} {v}")


def _manage(kind, args):
    lister, loader, setter, user = (
        (profiles.list_profiles, profiles.load_profile, profiles.set_value, profiles.set_default)
        if kind == "profile" else
        (profiles.list_printers, profiles.load_printer, profiles.set_printer_value, profiles.set_default_printer))
    current = (profiles.configured_default() or profiles.DEFAULT_PROFILE) if kind == "profile" \
        else profiles.configured_printer()
    if args.action == "list":
        for n in lister():
            d = loader(n)
            print(f"{n:<14}{'*' if n == current else ' '} {d.get('description') or d.get('note', '')}")
        print(f"(* = default; change it with: printtally {kind}s use NAME)")
    elif args.action == "show":
        d = loader(args.name)
        _show(d)
        if kind == "printer":
            print("  -- worked out --")
            _show(profiles.printer_rates(d, args.heads))
    elif args.action == "set":
        if not args.pairs:
            raise ValueError("Give at least one key=value.")
        for pair in args.pairs:
            key, value = _kv(pair)
            print(f"saved    {setter(args.name, key, value)}  {key} = {value}")
    elif args.action == "use":
        print(f"saved    {user(args.name)}  default {kind} = {args.name}")


def cmd_profiles(args):
    _manage("profile", args)


def cmd_printers(args):
    _manage("printer", args)


def cmd_materials(_args):
    for m in shop.load_materials().values():
        if not m.batches and m.price_per_kg <= 0 and not m.price_required:
            continue
        head = f"{m.name:<6} remas key: {m.remas_key or '-'}"
        if m.price_required and m.price_per_kg <= 0:
            print(f"{head}  set a price for {m.name}")
            continue
        for b, spec in m.batches.items():
            price = shop._my_price(spec)
            tiers = spec.get("my_price")
            extra = "  tiers: " + ", ".join(f"{x['min_kg']:g}+ kg Rs {x['price']:,.0f}" for x in tiers) \
                if isinstance(tiers, list) else ""
            lp = f"  list Rs {spec['list_price']:,.0f} (reference)" if spec.get("list_price") else ""
            print(f"{head}  [{b}] my price Rs {price:,.0f}/kg{lp}{extra}{'  ESTIMATE' if m.price_status == 'estimate' else ''}")
            head = " " * len(head)
        if not m.batches:
            print(f"{head}  my price Rs {m.price_per_kg:,.0f}/kg")


def cmd_build_card(args):
    from core import market
    settings = shop.build_settings(profile=args.profile, printer=args.printer, heads=args.heads)
    for key in ("profit_multiplier", "grams_per_hour", "labour_rate_per_hour", "failure_rate",
                "electricity_rate_per_kwh", "machine_rate_per_hour", "reseller_discount"):
        if getattr(args, key) is not None:
            settings[key] = getattr(args, key)
    if args.tier_discounts:
        settings["tier_discounts"] = [float(x) for x in args.tier_discounts.split(",")]
    prices = {m.name: m.price_per_kg for m in shop.load_materials().values() if m.price_per_kg > 0}
    if not prices:
        raise ValueError("No material has a price yet: set price_per_kg in materials.json first.")
    card = market.build_card(settings, prices)
    out = Path(args.out) if args.out else shop.config_dir() / "my_rates.json"
    if out.exists() and not args.force:
        raise ValueError(f"{out} already exists (your edits): pass --force to overwrite it, or --out elsewhere.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(card, indent=1) + "\n")
    print(f"wrote    {out}")
    for name, spec in card["fdm"].items():
        print(f"  {name:<10} customer {spec['customer']}  reseller {spec['reseller']}  Rs/g (3 tiers)")
    print("Edit any number in that file; PrintTally prices from it.")


def cmd_sync_sheet(args):
    if args.url or not args.inventory:
        for message in sheet_sync.sync_sheet(args.url):
            print(message)
    for message in sheet_sync.sync_inventory(args.inventory):
        print(message)


def cmd_price_history(args):
    rows = sheet_sync.price_history(args.name)
    for row in rows:
        print(f"{row['date']} {row['kind']} {row['name']}: {row['price']} (synced {row['synced_at']})")
    if not rows:
        print(f"No price history for {args.name}.")


def _selectors(p, with_material):
    p.add_argument("--profile", help="pricing profile: service (custom jobs, lamps) or product (kits); see `profiles list`")
    p.add_argument("--printer", help="printer whose payback, watts and speed set the machine rate; see `printers list`")
    p.add_argument("--heads", type=int, help="heads printing at once on a multi-head printer (1 for a single part)")
    if with_material:
        p.add_argument("--material", metavar="NAME[:BATCH]",
                       help="price all filament as this material, e.g. PETG or PETG:original (default: the cheapest batch)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="printtally")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("price", help="price a sliced G-code file")
    p.add_argument("gcode")
    p.add_argument("--model", help="the model file whose printtally.json preferences apply")
    p.add_argument("--rates", help="Remas reference card JSON (default: data/market_rates_pk.json)")
    p.add_argument("--my-rates", help="your own rate card JSON (default: my_rates.json in your config folder)")
    p.add_argument("--suggest", choices=("mine", "reseller", "remas", "remas_reseller", "multiplier"),
                   help="how the suggested price is chosen (default: your card's customer price)")
    p.add_argument("--multiplier", type=float, help="cost multiplier for --suggest multiplier")
    _selectors(p, True)
    p.set_defaults(func=cmd_price)
    p = sub.add_parser("build-card", help="generate your own per-gram rate card from your costs")
    for flag, typ in (("profit-multiplier", float), ("grams-per-hour", float), ("labour-rate-per-hour", float),
                      ("failure-rate", float), ("electricity-rate-per-kwh", float),
                      ("machine-rate-per-hour", float), ("reseller-discount", float)):
        p.add_argument("--" + flag, type=typ, dest=flag.replace("-", "_"))
    p.add_argument("--tier-discounts", metavar="0,0.15,0.3", help="price drop for each weight tier")
    _selectors(p, False)
    p.add_argument("--out", help="where to write the card (default: my_rates.json in your config folder)")
    p.add_argument("--force", action="store_true", help="overwrite an existing card")
    p.set_defaults(func=cmd_build_card)
    for kind, fn in (("profiles", cmd_profiles), ("printers", cmd_printers)):
        p = sub.add_parser(kind, help=f"list, show, edit or choose a default {kind[:-1]}")
        p.add_argument("action", choices=("list", "show", "set", "use"))
        p.add_argument("name", nargs="?")
        p.add_argument("pairs", nargs="*", metavar="key=value")
        p.add_argument("--heads", type=int, help="(printers show) heads in use")
        p.set_defaults(func=fn)
    sub.add_parser("materials", help="list material profiles, batches and prices").set_defaults(func=cmd_materials)
    sub.add_parser("init", help="write starter config files").set_defaults(func=cmd_init)
    p = sub.add_parser("sync-sheet", help="sync prices from a published Google Sheet CSV")
    p.add_argument("url", nargs="?", metavar="URL", help="published CSV URL (saved for later syncs)")
    p.add_argument("--inventory", metavar="URL", help="published Inventory tab CSV URL (saved for later syncs)")
    p.set_defaults(func=cmd_sync_sheet)
    p = sub.add_parser("price-history", help="show a material or part's dated prices")
    p.add_argument("name", metavar="NAME")
    p.set_defaults(func=cmd_price_history)
    args = ap.parse_args(argv)
    if getattr(args, "action", "") in ("show", "set", "use") and not args.name:
        ap.exit(1, "Error: give a name (see `list`).\n")
    try:
        args.func(args)
    except (OSError, ValueError) as exc:
        ap.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
