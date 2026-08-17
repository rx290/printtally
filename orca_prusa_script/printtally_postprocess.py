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
through the same core.cost_model formula the Cura plugin uses, and appends a
readable cost breakdown as a comment block at the end of the G-code file.

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--price-per-kg", type=float, required=True)
    parser.add_argument("--machine-rate", type=float, default=40.0)
    parser.add_argument("--watts", type=float, default=250.0)
    parser.add_argument("--electricity-rate", type=float, default=0.0)
    parser.add_argument("--waste-pct", type=float, default=0.07)
    parser.add_argument("--post-processing-hours", type=float, default=0.0)
    parser.add_argument("--post-processing-rate", type=float, default=0.0)
    parser.add_argument("--include-design-cost", action="store_true",
                         help="Only add design cost for a genuinely custom job -- "
                              "leave this off for a repeat print of an existing design.")
    parser.add_argument("--design-hours", type=float, default=0.0)
    parser.add_argument("--design-rate", type=float, default=0.0)
    parser.add_argument("--packaging-cost", type=float, default=0.0)
    parser.add_argument("--shipping-cost", type=float, default=0.0)
    parser.add_argument("--marketing-fee-pct", type=float, default=0.0)
    parser.add_argument("--margin-pct", type=float, default=0.25)
    parser.add_argument("--currency", type=str, default="")
    parser.add_argument("gcode_path", type=str)
    args = parser.parse_args()

    gcode_file = Path(args.gcode_path)
    gcode_text = gcode_file.read_text(encoding="utf-8", errors="replace")

    weight_g = _parse_weight_g(gcode_text)
    print_hours = _parse_print_hours(gcode_text)

    inputs = CostInputs(
        model_weight_g=weight_g,
        price_per_kg=args.price_per_kg,
        print_hours=print_hours,
        machine_rate_per_hour=args.machine_rate,
        printer_watts=args.watts,
        electricity_rate_per_kwh=args.electricity_rate,
        waste_pct=args.waste_pct,
        post_processing_hours=args.post_processing_hours,
        post_processing_rate_per_hour=args.post_processing_rate,
        include_design_cost=args.include_design_cost,
        design_hours=args.design_hours,
        design_rate_per_hour=args.design_rate,
        packaging_cost=args.packaging_cost,
        shipping_cost=args.shipping_cost,
        marketing_fee_pct=args.marketing_fee_pct,
        profit_margin_pct=args.margin_pct,
    )
    breakdown = calculate_cost(inputs)

    report = format_breakdown(breakdown, currency=args.currency)
    with gcode_file.open("a", encoding="utf-8") as f:
        f.write("\n; ----- PrintTally cost breakdown -----\n")
        for line in report.splitlines():
            f.write(f"; {line}\n")
        f.write("; --------------------------------------\n")

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
