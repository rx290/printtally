"""
Shared 3D print costing formula, used identically by the Cura plugin and the
OrcaSlicer/PrusaSlicer post-processing script. Pure Python, no slicer
dependency, so both adapters can import it directly.

This file is a byte-for-byte copy of core/cost_model.py in the repo root --
kept in sync by tests/test_plugin_cost_model_matches_core.py. It's duplicated
(not imported across the two trees) because a Cura plugin folder and the repo
root aren't on the same Python path once installed into Cura's plugins
directory.

Formula source: a "How to Price your 3D Prints ACCURATELY" short-form video's
caption breakdown, extended with one more factor at the repo owner's explicit
request -- design cost, gated behind its own on/off switch since a repeat
print of an existing design shouldn't carry a design charge, but a genuinely
custom job should:

  1. Material Cost    = model_weight_g * (price_per_kg / 1000)
  2. Machine Cost      = machine_rate_per_hour * print_hours
  3. Electricity Cost  = (printer_watts * print_hours / 1000) * electricity_rate_per_kwh
  4. Waste/Maintenance = waste_pct * (material + machine + electricity)
  5. Labor cost        = post_processing_rate_per_hour * post_processing_hours
                         (support removal, finishing, quality check)
  6. Design cost       = design_rate_per_hour * design_hours,
                         ONLY IF include_design_cost is True -- otherwise 0,
                         regardless of what design_hours/design_rate_per_hour
                         are set to. This is a real on/off switch, not just a
                         rate you have to remember to zero out.
  7. Selling costs     = packaging_cost + shipping_cost + (marketing_fee_pct * subtotal)
  8. Parts            = hardware_cost (lights, switch, plug, cable, magnets, screws: the bill of
                         materials, priced from parts.json by core/parts.py)
  9. Royalty           = royalty_fixed per sale + royalty_pct of the final selling price
                         (licensed designs; 0 for your own designs)
  Profit margin        = margin_pct * (sum of 1-8 + fixed royalty)

Final price = (1-8 + fixed royalty + margin) / (1 - royalty_pct)
Cost to make = material + machine + electricity + waste + parts: what one print costs you, shown for
every print, prototypes included.
"""

from dataclasses import dataclass, field


@dataclass
class CostInputs:
    # 1. Material
    model_weight_g: float           # total filament weight the slicer reports,
                                     # including purge/support/skirt -- the
                                     # slicer's own total already includes this,
                                     # so no separate waste-weight field here
    price_per_kg: float

    # 2. Machine
    print_hours: float
    machine_rate_per_hour: float = 40.0     # video's stated range: 20-60/hr

    # 3. Electricity
    printer_watts: float = 250.0
    electricity_rate_per_kwh: float = 0.0    # user must set their real local rate

    # 4. Waste and maintenance
    waste_pct: float = 0.07                  # video's stated range: 5-10%

    # 5. Labor (support removal, finishing, quality check)
    post_processing_hours: float = 0.0
    post_processing_rate_per_hour: float = 0.0

    # 6. Design cost -- a real checkbox, not just a rate to remember to zero.
    # Off by default: most prints are repeats of an existing design and
    # shouldn't carry a design charge. Turn it on for a genuinely custom job.
    include_design_cost: bool = False
    design_hours: float = 0.0
    design_rate_per_hour: float = 0.0

    # 7. Selling costs
    packaging_cost: float = 0.0
    shipping_cost: float = 0.0
    marketing_fee_pct: float = 0.0           # e.g. marketplace/referral fee

    # Profit margin, applied last, on top of everything above
    profit_margin_pct: float = 0.25          # video's stated range: 20-30%

    # 8. Parts: electronics and hardware (bill of materials total)
    hardware_cost: float = 0.0

    # 9. Royalty for licensed designs: fixed per sale and/or a share of the selling price
    royalty_fixed: float = 0.0
    royalty_pct: float = 0.0
    failure_rate: float = 0.0               # share of prints that fail; cost / (1 - rate)


@dataclass
class CostBreakdown:
    material_cost: float
    machine_cost: float
    electricity_cost: float
    waste_maintenance_cost: float
    post_processing_cost: float
    design_cost: float
    selling_cost: float
    subtotal: float
    profit_margin: float
    final_price: float
    hardware_cost: float = 0.0
    royalty_cost: float = 0.0
    cost_to_make: float = 0.0
    failure_cost: float = 0.0


def calculate_cost(inputs: CostInputs) -> CostBreakdown:
    if inputs.model_weight_g < 0:
        raise ValueError("model_weight_g cannot be negative")
    if inputs.price_per_kg < 0:
        raise ValueError("price_per_kg cannot be negative")
    if inputs.print_hours < 0:
        raise ValueError("print_hours cannot be negative")
    if not (0 <= inputs.waste_pct <= 1):
        raise ValueError("waste_pct must be between 0 and 1 (e.g. 0.07 for 7%)")
    if not (0 <= inputs.marketing_fee_pct <= 1):
        raise ValueError("marketing_fee_pct must be between 0 and 1")
    if inputs.profit_margin_pct < 0:
        raise ValueError("profit_margin_pct cannot be negative")
    if inputs.design_hours < 0:
        raise ValueError("design_hours cannot be negative")
    if inputs.design_rate_per_hour < 0:
        raise ValueError("design_rate_per_hour cannot be negative")
    if inputs.hardware_cost < 0 or inputs.royalty_fixed < 0:
        raise ValueError("hardware_cost and royalty_fixed cannot be negative")
    if not (0 <= inputs.failure_rate <= 0.5):
        raise ValueError("failure_rate must be between 0 and 0.5 (e.g. 0.1 for 10%)")
    if not (0 <= inputs.royalty_pct < 1):
        raise ValueError("royalty_pct must be at least 0 and below 1 (e.g. 0.1 for 10%)")

    material_cost = inputs.model_weight_g * (inputs.price_per_kg / 1000.0)
    machine_cost = inputs.machine_rate_per_hour * inputs.print_hours
    electricity_cost = (
        (inputs.printer_watts * inputs.print_hours) / 1000.0
    ) * inputs.electricity_rate_per_kwh
    post_processing_cost = (
        inputs.post_processing_rate_per_hour * inputs.post_processing_hours
    )
    design_cost = (
        inputs.design_rate_per_hour * inputs.design_hours
        if inputs.include_design_cost else 0.0
    )

    # Waste/maintenance is a percentage of the three "running the printer"
    # costs -- the video says "5-10% of total cost," but applying it to the
    # already-final total (which includes profit margin) would double-count
    # margin on top of margin, so it's scoped to the direct production costs
    # instead, before post-processing/selling/margin are added.
    production_subtotal = material_cost + machine_cost + electricity_cost
    waste_maintenance_cost = inputs.waste_pct * production_subtotal
    # Failed prints: every good part also pays for the failed tries (industry method: divide by 1 - failure rate,
    # so 10 % failures adds about 11 %), on the cost to print it, before selling costs and margin.
    made = production_subtotal + waste_maintenance_cost + inputs.hardware_cost
    failure_cost = made / (1.0 - inputs.failure_rate) - made

    selling_cost_fixed = inputs.packaging_cost + inputs.shipping_cost
    pre_marketing_subtotal = (
        production_subtotal
        + waste_maintenance_cost
        + failure_cost
        + inputs.hardware_cost
        + post_processing_cost
        + design_cost
        + selling_cost_fixed
        + inputs.royalty_fixed
    )
    marketing_fee = inputs.marketing_fee_pct * pre_marketing_subtotal
    selling_cost = selling_cost_fixed + marketing_fee

    subtotal = pre_marketing_subtotal + marketing_fee
    profit_margin = inputs.profit_margin_pct * subtotal
    # A royalty that is a share of the selling price is paid out of that price: gross it up so the
    # margin is still what was asked for after the designer's share.
    final_price = (subtotal + profit_margin) / (1.0 - inputs.royalty_pct)
    royalty_cost = inputs.royalty_fixed + inputs.royalty_pct * final_price
    cost_to_make = made + failure_cost

    return CostBreakdown(
        material_cost=round(material_cost, 2),
        machine_cost=round(machine_cost, 2),
        electricity_cost=round(electricity_cost, 2),
        waste_maintenance_cost=round(waste_maintenance_cost, 2),
        post_processing_cost=round(post_processing_cost, 2),
        design_cost=round(design_cost, 2),
        selling_cost=round(selling_cost, 2),
        subtotal=round(subtotal, 2),
        profit_margin=round(profit_margin, 2),
        final_price=round(final_price, 2),
        hardware_cost=round(inputs.hardware_cost, 2),
        royalty_cost=round(royalty_cost, 2),
        cost_to_make=round(cost_to_make, 2),
        failure_cost=round(failure_cost, 2),
    )


def format_breakdown(breakdown: CostBreakdown, currency: str = "") -> str:
    def money(v: float) -> str:
        return f"{currency}{v:.2f}" if currency else f"{v:.2f}"

    lines = [
        f"Material cost:          {money(breakdown.material_cost)}",
        f"Printing cost:          {money(breakdown.machine_cost)}",
        f"Electricity cost:       {money(breakdown.electricity_cost)}",
        f"Maintenance cost:       {money(breakdown.waste_maintenance_cost)}",
        f"Failed prints:          {money(breakdown.failure_cost)}",
        f"Parts cost:             {money(breakdown.hardware_cost)}",
        f"Labor cost:             {money(breakdown.post_processing_cost)}",
        f"Design cost:            {money(breakdown.design_cost)}",
        f"Selling costs:          {money(breakdown.selling_cost)}",
        f"Royalty:                {money(breakdown.royalty_cost)}",
        f"Subtotal:               {money(breakdown.subtotal)}",
        f"Profit margin:          {money(breakdown.profit_margin)}",
        f"FINAL PRICE:            {money(breakdown.final_price)}",
        f"Cost to make (1 print): {money(breakdown.cost_to_make)}",
    ]
    return "\n".join(lines)
