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
    python printtally.py price model.gcode --customer friends
                                                       price for a friend (under Rs 20/g) or a customer (at least Rs 20/g)
    python printtally.py customers                     list customer profiles
    python printtally.py parts                         list lights and hardware (BOM) with local prices
    python printtally.py price-folder ~/lamp-print-packs/home/01* --out home.csv
                                                       price whole packs (STL/3MF estimated, G-code exact) into a CSV
    python printtally.py prefs model.stl               show and check the printtally.json files that apply
    python printtally.py init                          write starter materials/parts/settings files

Your files live in ~/.config/printtally ($PRINTTALLY_CONFIG overrides). Preference files
(printtally.json) sit beside a model or in any folder above it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import shop, gcode_stats, sheet_sync, profiles, folder  # noqa: E402

TEMPLATES = {
    "materials.json": {name: {"price_per_kg": 0, "density_g_cm3": d, "aliases": a}
                       for name, (d, a) in shop.DEFAULT_MATERIALS.items()},
    "parts.json": json.loads((profiles.data_dir() / "parts.json").read_text()),
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
    _apply_night(args, prefs)
    if args.customer:
        prefs["customer"] = args.customer
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
    for problem in shop.check_preferences(prefs):
        print(f"! printtally.json: {problem}")


def _apply_night(args, prefs):
    """--night / --night-share: how much of the print runs on the night electricity rate."""
    if getattr(args, "night", False):
        prefs["night_share"] = 1.0
    if getattr(args, "night_share", None) is not None:
        prefs["night_share"] = args.night_share


def cmd_prefs(args):
    prefs, used = shop.find_preferences(Path(args.model))
    print("Files (farthest first, nearer wins):" if used else "No printtally.json applies to this model.")
    for f in used:
        print(f"  {f}")
    _show(prefs)
    problems = shop.check_preferences(prefs)
    for problem in problems:
        print(f"! {problem}")
    if not problems and used:
        print("OK")


def cmd_customers(_args):
    settings = shop.build_settings()
    default = settings.get("default_customer", "customer")
    for name, spec in shop.load_customers(None, settings).items():
        rules = ", ".join(f"{k} {spec[k]:g}" for k in shop.CUSTOMER_KEYS if float(spec.get(k, 0) or 0))
        print(f"{name:<12}{'*' if name == default else ' '} {rules or 'no rules':<34} {spec.get('description', '')}")
    print("(* = default_customer in settings.json; add your own in customers.json in your config folder)")


def cmd_price_folder(args):
    night = 1.0 if args.night else args.night_share
    rows = folder.price_folders(args.paths, args.out, profile=args.profile, printer=args.printer, heads=args.heads,
                                material=args.material, night_share=night, customer=args.customer)
    if not rows:
        raise ValueError("No STL, 3MF or G-code files found in " + ", ".join(args.paths))
    for r in rows:
        flag = "  ESTIMATE" if r["Estimate"] else ""
        price = f"Rs {r['Price']:>8,}" if r["Price"] != "" else "NOT FOR SALE"
        print(f"{r['Product'][:44]:<44} {r['Grams']:>8,.0f} g {r['Print hours']:>6.1f} h  cost Rs {r['Unit cost']:>7,}"
              f"  price {price}{flag}")
    print(f"wrote    {args.out}  ({len(rows)} products)")


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


def cmd_parts(args):
    parts = shop.load_parts()
    only_est = getattr(args, "estimates", False)
    if only_est:
        print("Estimated part prices to confirm (put the real price in your parts.json and set status to owner):")
    for name, price in sorted(parts.items(), key=lambda kv: (str(parts.specs[kv[0]].get("category", "")), kv[0])):
        if only_est and name not in parts.estimates:
            continue
        spec = parts.specs[name]
        flag = "printed" if name in parts.printed else ("ESTIMATE" if name in parts.estimates else "")
        print(f"{str(spec.get('category', '')):<12} {name:<20} Rs {price:>8,.0f} / {spec.get('unit', 'each'):<6} "
              f"{flag:<9} {spec.get('note', '')}")
    print("Edit prices in " + str(shop.config_dir() / "parts.json") + " (printtally init copies this list there).")


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
        p.add_argument("--customer", metavar="NAME",
                       help="customer profile: customer (default, at least Rs 20/g), friends (under Rs 20/g), none, "
                            "or your own; see `customers`")
        p.add_argument("--night", action="store_true",
                       help="the print runs at night: electricity at electricity_rate_night_per_kwh")
        p.add_argument("--night-share", type=float, metavar="0-1",
                       help="share of the print time on the night rate, e.g. 0.5 for a print that runs into the night")
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
    p = sub.add_parser("prefs", help="show the merged printtally.json preferences for a model and check them")
    p.add_argument("model", help="the model file (or a folder) the preferences are for")
    p.set_defaults(func=cmd_prefs)
    p = sub.add_parser("price-folder", help="price every product folder of STL/3MF/G-code files into a CSV")
    p.add_argument("paths", nargs="+", metavar="PATH", help="pack folders, a folder of packs, or single files")
    p.add_argument("--out", default="printtally_prices.csv", help="CSV to write (default: printtally_prices.csv)")
    _selectors(p, True)
    p.set_defaults(func=cmd_price_folder)
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
    sub.add_parser("customers", help="list customer profiles (friends, customer, ...)").set_defaults(
        func=cmd_customers)
    p = sub.add_parser("parts", help="list lights and hardware with prices (BOM parts)")
    p.add_argument("--estimates", action="store_true", help="list only parts whose price is still an estimate")
    p.set_defaults(func=cmd_parts)
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
