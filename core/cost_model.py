"""
Shared 3D print costing formula, used identically by the Cura post-processing
script and the OrcaSlicer/PrusaSlicer post-processing script. Pure Python, no
slicer dependency, so both adapters can import it directly.

Formula source: a "How to Price your 3D Prints ACCURATELY" short-form video's
caption breakdown (six line items):

  1. Material Cost   = model_weight_g * (price_per_kg / 1000)
  2. Machine Cost     = machine_rate_per_hour * print_hours
  3. Electricity Cost = (printer_watts * print_hours / 1000) * electricity_rate_per_kwh
  4. Waste/Maintenance = waste_pct * (material + machine + electricity)
  5. Post-processing   = post_processing_rate_per_hour * post_processing_hours
  6. Selling costs     = packaging_cost + shipping_cost + (marketing_fee_pct * subtotal)
  Profit margin        = margin_pct * (sum of 1-6)

Final price = (1-6) + profit margin
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

    # 5. Post-processing (support removal, finishing, quality check)
    post_processing_hours: float = 0.0
    post_processing_rate_per_hour: float = 0.0

    # 6. Selling costs
    packaging_cost: float = 0.0
    shipping_cost: float = 0.0
    marketing_fee_pct: float = 0.0           # e.g. marketplace/referral fee

    # Profit margin, applied last, on top of everything above
    profit_margin_pct: float = 0.25          # video's stated range: 20-30%


@dataclass
class CostBreakdown:
    material_cost: float
    machine_cost: float
    electricity_cost: float
    waste_maintenance_cost: float
    post_processing_cost: float
    selling_cost: float
    subtotal: float
    profit_margin: float
    final_price: float


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

    material_cost = inputs.model_weight_g * (inputs.price_per_kg / 1000.0)
    machine_cost = inputs.machine_rate_per_hour * inputs.print_hours
    electricity_cost = (
        (inputs.printer_watts * inputs.print_hours) / 1000.0
    ) * inputs.electricity_rate_per_kwh
    post_processing_cost = (
        inputs.post_processing_rate_per_hour * inputs.post_processing_hours
    )

    # Waste/maintenance is a percentage of the three "running the printer"
    # costs -- the video says "5-10% of total cost," but applying it to the
    # already-final total (which includes profit margin) would double-count
    # margin on top of margin, so it's scoped to the direct production costs
    # instead, before post-processing/selling/margin are added.
    production_subtotal = material_cost + machine_cost + electricity_cost
    waste_maintenance_cost = inputs.waste_pct * production_subtotal

    selling_cost_fixed = inputs.packaging_cost + inputs.shipping_cost
    pre_marketing_subtotal = (
        production_subtotal
        + waste_maintenance_cost
        + post_processing_cost
        + selling_cost_fixed
    )
    marketing_fee = inputs.marketing_fee_pct * pre_marketing_subtotal
    selling_cost = selling_cost_fixed + marketing_fee

    subtotal = pre_marketing_subtotal + marketing_fee
    profit_margin = inputs.profit_margin_pct * subtotal
    final_price = subtotal + profit_margin

    return CostBreakdown(
        material_cost=round(material_cost, 2),
        machine_cost=round(machine_cost, 2),
        electricity_cost=round(electricity_cost, 2),
        waste_maintenance_cost=round(waste_maintenance_cost, 2),
        post_processing_cost=round(post_processing_cost, 2),
        selling_cost=round(selling_cost, 2),
        subtotal=round(subtotal, 2),
        profit_margin=round(profit_margin, 2),
        final_price=round(final_price, 2),
    )


def format_breakdown(breakdown: CostBreakdown, currency: str = "") -> str:
    def money(v: float) -> str:
        return f"{currency}{v:.2f}" if currency else f"{v:.2f}"

    lines = [
        f"Material cost:          {money(breakdown.material_cost)}",
        f"Machine cost:           {money(breakdown.machine_cost)}",
        f"Electricity cost:       {money(breakdown.electricity_cost)}",
        f"Waste/maintenance:      {money(breakdown.waste_maintenance_cost)}",
        f"Post-processing:        {money(breakdown.post_processing_cost)}",
        f"Selling costs:          {money(breakdown.selling_cost)}",
        f"Subtotal:               {money(breakdown.subtotal)}",
        f"Profit margin:          {money(breakdown.profit_margin)}",
        f"FINAL PRICE:            {money(breakdown.final_price)}",
    ]
    return "\n".join(lines)
