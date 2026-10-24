#!/usr/bin/env python3
"""
PrintTally post-processing script for OrcaSlicer / PrusaSlicer.

Configure in Print Settings -> Output options -> Post-processing scripts,
pointing at this file's absolute path plus your cost parameters, e.g.:

    /path/to/printtally_postprocess.py --price-per-kg 2500 --machine-rate 40 \
        --watts 250 --electricity-rate 12 --waste-pct 0.07 --margin-pct 0.25 \
        --include-design-cost --design-hours 1.5 --design-rate 500

--include-design-cost is a real on/off switch: omit it for a repeat print of
an existing design, add it (along with --design-hours/--design-rate) only
for a genuinely custom job. --design-hours/--design-rate have no effect at
all unless --include-design-cost is also passed.

The slicer appends the G-code file's path as the final argument automatically
-- do not add it yourself. This script reads the slicer's own footer comments
(weight and estimated print time, already computed by the slicer), runs them
through the same core.cost_model formula the Cura plugin uses, and prepends a
readable cost breakdown as a comment block at the start of the G-code file.

Footer comment formats parsed here were confirmed directly against OrcaSlicer's
own source (src/libslic3r/GCode.cpp and GCode/GCodeProcessor.cpp), not assumed:
    ; total filament used [g] = 45.23
    ; total filament cost = 1.13
    ; estimated printing time (normal mode) = 3h 12m 45s
PrusaSlicer uses the same format (OrcaSlicer is a PrusaSlicer/Bambu Studio
fork and kept this footer convention). If a future slicer version changes
this format, this parser will raise a clear error rather than silently
computing a wrong price -- see `_parse_weight_g` / `_parse_print_hours`.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cost_model import CostInputs, calculate_cost, format_breakdown
from core import market as market_mod, shop, gcode_stats

WEIGHT_RE = re.compile(r"^;\s*total filament used \[g\]\s*=\s*([\d.]+)", re.MULTILINE)
TIME_RE = re.compile(
    r"^;\s*estimated printing time \(normal mode\)\s*=\s*"
    r"(?:(\d+)d\s*)?(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?",
    re.MULTILINE,
)


def _parse_weight_g(gcode_text: str) -> float:
    match = WEIGHT_RE.search(gcode_text)
    if not match:
        raise ValueError(
            "Could not find '; total filament used [g] = ...' in the G-code footer. "
            "This script was written against OrcaSlicer/PrusaSlicer's footer format -- "
            "if you're on a different slicer or a version that changed this format, "
            "the parser needs updating rather than guessing a weight."
        )
    return float(match.group(1))


def _parse_print_hours(gcode_text: str) -> float:
    match = TIME_RE.search(gcode_text)
    if not match:
        raise ValueError(
            "Could not find '; estimated printing time (normal mode) = ...' in the "
            "G-code footer. Enable time estimation in Print Settings, or update this "
            "parser if the footer format has changed."
        )
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total_seconds / 3600.0


def format_report(breakdown, inputs, currency="", market_args=None, header_lines=()):
    """Presentation only; keep calculation and legacy output keys intact."""
    rows = [("Material used:", f"{inputs.model_weight_g:,.1f} g"),
            ("Machine time:", f"{inputs.print_hours:,.2f} h")]
    for line in format_breakdown(breakdown).splitlines():
        label, value = line.rsplit(":", 1)
        rows.append((label + ":", f"{currency}{float(value):,.2f}"))
    value_width = max(len(value) for _, value in rows)
    report = ["PrintTally cost breakdown", *header_lines, "-" * (25 + value_width)]
    report += [f"{label:<24} {value:>{value_width}}" for label, value in rows]
    report.append("-" * (25 + value_width))
    # Existing consumers read these literal, ungrouped output keys.
    if not currency:
        report += ["Compatibility values:",
                   f"Design cost:            {breakdown.design_cost:.2f}",
                   f"FINAL PRICE:            {breakdown.final_price:.2f}"]
    if market_args is not None:
        report.append("")
        report += market_mod.format_market(
            market_args["parts"], market_args["mine"], market_args["remas"], market_args["cost"],
            market_args["mode"], market_args["multiplier"], currency, inputs.model_weight_g, inputs.print_hours,
            market_args.get("step", 50.0), market_args.get("minimum", 0.0))
    warnings = []
    if inputs.electricity_rate_per_kwh == 0:
        warnings.append("Electricity rate not set: electricity counted as 0.")
    if inputs.price_per_kg == 0:
        warnings.append("Material price is zero: material counted as 0.")
    report += [f"! {warning}" for warning in warnings]
    return "\n".join(report)


def _market_args(args, weight_g, print_hours, breakdown):
    s = args.settings
    card, source = market_mod.my_card(s, {args.material: args.price_per_kg},
                                      args.my_rates or str(shop.config_dir() / "my_rates.json"))
    mine = market_mod.market_price({args.material: weight_g}, card=card, label="your rate card")
    mine.rates_path = source
    found = shop.match_material(args.material, shop.load_materials())
    names = {args.material: found.remas_key} if found is not None and found.remas_key else None
    remas = market_mod.market_price({args.material: weight_g}, args.market_rates or None, names, label="the Remas card")
    minimum = float(s.get("minimum_price", 0) or 0)
    if mine.available and minimum > mine.customer:
        mine.customer = minimum
    b = breakdown
    labour = b.post_processing_cost + weight_g * market_mod.cost_per_gram(0.0, s)["labour"]
    parts = market_mod.price_parts(weight_g, print_hours, b.material_cost, b.electricity_cost,
                                   b.machine_cost + b.waste_maintenance_cost, b.failure_cost, labour,
                                   mine.customer, b.hardware_cost) if mine.available else []
    cost = sum(v for _, v in parts[:-1]) if parts else b.cost_to_make
    return {"mine": mine, "remas": remas, "parts": parts, "cost": cost, "mode": args.suggest,
            "multiplier": args.cost_multiplier, "step": float(s.get("rounding_step", 50.0) or 50.0),
            "minimum": minimum}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="",
                        help="Pricing profile: service (custom jobs, lamps) or product (kits). Default: "
                             "$PRINTTALLY_PROFILE, else your default in ~/.config/printtally/settings.json, else product.")
    parser.add_argument("--printer", default="", help="Printer name (see `printtally.py printers list`): sets machine rate, watts, speed.")
    parser.add_argument("--heads", type=int, default=None, help="Heads printing at once on a multi-head printer.")
    parser.add_argument("--price-per-kg", type=float, default=None,
                        help="Default: the price of --material (cheapest batch, or NAME:BATCH) from your material profiles.")
    parser.add_argument("--machine-rate", type=float, default=None)
    parser.add_argument("--watts", type=float, default=None)
    parser.add_argument("--electricity-rate", type=float, default=None)
    parser.add_argument("--waste-pct", type=float, default=None)
    parser.add_argument("--post-processing-hours", type=float, default=0.0)
    parser.add_argument("--post-processing-rate", type=float, default=None)
    parser.add_argument("--include-design-cost", action="store_true",
                         help="Only add design cost for a genuinely custom job -- "
                              "leave this off for a repeat print of an existing design.")
    parser.add_argument("--design-hours", type=float, default=0.0)
    parser.add_argument("--design-rate", type=float, default=None)
    parser.add_argument("--packaging-cost", type=float, default=None)
    parser.add_argument("--shipping-cost", type=float, default=None)
    parser.add_argument("--marketing-fee-pct", type=float, default=None)
    parser.add_argument("--margin-pct", type=float, default=None)
    parser.add_argument("--currency", type=str, default="")
    parser.add_argument("--material", type=str, default="",
                         help="Material name for the price and the market price (PLA, PETG, PETG:original, ABS, TPU, ...).")
    parser.add_argument("--market-rates", type=str, default="",
                         help="Remas reference card JSON (default: data/market_rates_pk.json).")
    parser.add_argument("--my-rates", type=str, default="",
                         help="Your own rate card JSON (default: my_rates.json in ~/.config/printtally, "
                              "else built from the profile).")
    parser.add_argument("--suggest", choices=("mine", "reseller", "remas", "remas_reseller", "multiplier"), default=None)
    parser.add_argument("--cost-multiplier", type=float, default=None)
    parser.add_argument("--failure-rate", type=float, default=None)
    parser.add_argument("--labour-rate", type=float, default=None)
    parser.add_argument("--grams-per-hour", type=float, default=None)
    parser.add_argument("--profit-multiplier", type=float, default=None)
    parser.add_argument("gcode_path", type=str)
    args = parser.parse_args()

    gcode_file = Path(args.gcode_path)
    gcode_text = gcode_file.read_text(encoding="utf-8", errors="replace")

    weight_g = _parse_weight_g(gcode_text)
    print_hours = _parse_print_hours(gcode_text)

    # profile and printer first, then any flag you pass overrides one value of it
    s = shop.build_settings(profile=args.profile or None, printer=args.printer or None, heads=args.heads)
    for key, flag in (("machine_rate_per_hour", args.machine_rate), ("printer_watts", args.watts),
                      ("electricity_rate_per_kwh", args.electricity_rate), ("waste_pct", args.waste_pct),
                      ("post_processing_rate_per_hour", args.post_processing_rate),
                      ("design_rate_per_hour", args.design_rate), ("packaging_cost", args.packaging_cost),
                      ("shipping_cost", args.shipping_cost), ("marketing_fee_pct", args.marketing_fee_pct),
                      ("profit_margin_pct", args.margin_pct), ("suggest_mode", args.suggest),
                      ("cost_multiplier", args.cost_multiplier), ("failure_rate", args.failure_rate),
                      ("labour_rate_per_hour", args.labour_rate), ("grams_per_hour", args.grams_per_hour),
                      ("profit_multiplier", args.profit_multiplier)):
        if flag is not None:
            s[key] = flag
    material_name, batch = shop.parse_choice(args.material)
    price_per_kg = args.price_per_kg
    notes = []
    if price_per_kg is None:
        library = shop.load_materials()
        found = shop.match_material(material_name, library)
        if found is None:
            parser.error("pass --price-per-kg, or --material with a name from your material profiles")
        if batch or found.batches:
            shop.select_batch(found, batch)
        if found.price_per_kg <= 0:
            parser.error(f"set a price for {found.name} (my_price in its material file), or pass --price-per-kg")
        price_per_kg = found.price_per_kg
        notes.append(f"Material {found.name}{' [' + found.batch + ']' if found.batch else ''}: my price Rs "
                     f"{price_per_kg:,.0f}/kg" + (f" (list price Rs {found.list_price:,.0f}, reference only)"
                                                  if found.list_price else "")
                     + ("  ** ESTIMATE **" if found.price_status == "estimate" else ""))
    args.material = material_name
    args.price_per_kg = price_per_kg
    args.watts, args.electricity_rate = s["printer_watts"], s["electricity_rate_per_kwh"]
    args.machine_rate, args.failure_rate = s["machine_rate_per_hour"], s["failure_rate"]
    args.labour_rate, args.grams_per_hour = s["labour_rate_per_hour"], s["grams_per_hour"]
    args.profit_multiplier, args.suggest = s["profit_multiplier"], s["suggest_mode"]
    args.cost_multiplier = s["cost_multiplier"]
    args.settings = s

    inputs = CostInputs(
        model_weight_g=weight_g,
        price_per_kg=price_per_kg,
        print_hours=print_hours,
        machine_rate_per_hour=s["machine_rate_per_hour"],
        printer_watts=s["printer_watts"],
        electricity_rate_per_kwh=s["electricity_rate_per_kwh"],
        waste_pct=s["waste_pct"],
        post_processing_hours=args.post_processing_hours,
        post_processing_rate_per_hour=s["post_processing_rate_per_hour"],
        include_design_cost=args.include_design_cost,
        design_hours=args.design_hours,
        design_rate_per_hour=s["design_rate_per_hour"],
        packaging_cost=s["packaging_cost"],
        shipping_cost=s["shipping_cost"],
        marketing_fee_pct=s["marketing_fee_pct"],
        profit_margin_pct=s["profit_margin_pct"],
        failure_rate=s["failure_rate"],
    )
    breakdown = calculate_cost(inputs)

    market_args = None
    if args.material:
        market_args = _market_args(args, weight_g, print_hours, breakdown)
    printer = s.get("printer_name", "")
    layer, line = gcode_stats._print_factors(gcode_text[:20000] + "\n" + gcode_text[-60000:])
    header_lines = [f"Profile: {s['profile_name']}" + (f"   Printer: {printer}" if printer else ""),
                    f"Factors: {print_hours:.2f} h, {weight_g:,.0f} g"
                    + (f", layer {layer:g} mm" if layer else "") + (f", line {line:g} mm" if line else "")
                    + f", {s['printer_watts']:g} W = "
                    f"{s['printer_watts'] * print_hours / 1000:.2f} kWh x {s['electricity_rate_per_kwh']:g}"
                    + (f", payback Rs {s['payback_per_g']:.2f}/g" if s.get("payback_per_g") is not None else ""),
                    *notes]
    report = format_report(breakdown, inputs, currency=args.currency, market_args=market_args,
                           header_lines=header_lines)
    if market_args is None:
        report += "\n! Market price not shown: pass --material (e.g. PETG)."

    header = "; ----- PrintTally cost breakdown -----\n"
    header += "".join(f"; {line}\n" for line in report.splitlines())
    header += "; --------------------------------------\n"
    # Reprocessing a file replaces our report instead of stacking stale prices.
    gcode_text = re.sub(
        r"(?:^|\n); ----- PrintTally cost breakdown -----\n.*?; --------------------------------------\n",
        "", gcode_text, flags=re.DOTALL,
    )
    gcode_file.write_text(header + gcode_text, encoding="utf-8")

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
