"""
PrintTally 2.0 shop layer: prices a real job from what the slicer reports plus your own files.

  materials.json   your filaments: price per kg, density, and the names slicers call them
  parts.json       your parts (lights, switches, plugs, cables, magnets, screws, packaging) with prices
  settings.json    shop-wide rates (machine rate, printer watts, electricity, margin, waste, currency)
  printtally.json  preferences for one model (beside it, as <model>.printtally.json) or for a whole
                   folder (printtally.json in that folder or any folder above it): material, margin,
                   licence, bill of materials, packaging, stage. Nearer files override farther ones.

The first three live in ~/.config/printtally (or $PRINTTALLY_CONFIG). Nothing personal ships in the
repo: the built-in defaults have no prices, and a missing price is reported, never guessed.

Every job gets a price, prototypes included: a prototype shows what it cost to make (filament, machine
time, electricity, waste, parts), and the selling price it would need.
Licences: "own" (your design, no royalty), "commercial" (a royalty per sale and/or a share of the price),
"personal" (personal use only: priced, but flagged DO NOT SELL).
Pure Python, standard library only, so the Cura plugin can carry a copy (see tests).
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from .cost_model import CostInputs, calculate_cost  # noqa: F401  (plugin copy imports .cost_model too)
from . import market as market_mod

LICENCES = ("own", "commercial", "personal")
STAGES = ("prototype", "selling", "retired")

DEFAULT_MATERIALS = {
    # name: (density g/cm3, aliases slicers use). Prices come from your materials.json.
    "PLA": (1.24, ["pla", "pla+", "generic pla", "pla basic", "pla matte"]),
    "PETG": (1.27, ["petg", "generic petg", "petg basic", "pet-g"]),
    "PET": (1.38, ["pet"]),
    "ABS": (1.04, ["abs", "generic abs"]),
    "ASA": (1.07, ["asa", "generic asa"]),
    "TPU": (1.21, ["tpu", "flex", "generic tpu", "tpu 95a"]),
}
DEFAULT_SETTINGS = {
    "currency": "",
    "machine_rate_per_hour": 40.0,
    "printer_watts": 250.0,
    "electricity_rate_per_kwh": 0.0,
    "waste_pct": 0.07,
    "profit_margin_pct": 0.25,
    "filament_diameter_mm": 1.75,
    "market_rates_path": "",             # the Remas reference card; empty = the one shipped in data/
    "my_rates_path": "",                 # your own card; empty = my_rates.json in the config folder if it exists
    "suggest_mode": "mine",              # mine | reseller | remas | remas_reseller | multiplier
    "cost_multiplier": 2.0,              # used by the multiplier mode
    "failure_rate": 0.0,
    **market_mod.CARD_DEFAULTS,          # labour, grams per hour, profit multiplier, tier discounts, ...
}


def config_dir() -> Path:
    return Path(os.environ.get("PRINTTALLY_CONFIG") or Path.home() / ".config" / "printtally")


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ materials
@dataclass
class Material:
    name: str
    price_per_kg: float = 0.0          # 0 = not set yet: reported, never guessed
    density_g_cm3: float = 1.24
    aliases: list = field(default_factory=list)
    weigh_factor: float = 1.0          # scale grams / slicer grams, from printkit weigh (1 = trust the slicer)


def load_materials(directory: Path | None = None) -> dict:
    """{name: Material}: the built-in list (no prices) updated by materials.json."""
    lib = {n: Material(n, 0.0, d, list(a)) for n, (d, a) in DEFAULT_MATERIALS.items()}
    raw = _read_json((directory or config_dir()) / "materials.json", {})
    for name, spec in raw.items():
        base = lib.get(name, Material(name))
        lib[name] = Material(name, float(spec.get("price_per_kg", base.price_per_kg)),
                             float(spec.get("density_g_cm3", base.density_g_cm3)),
                             [a.lower() for a in spec.get("aliases", base.aliases)],
                             float(spec.get("weigh_factor", base.weigh_factor)) or 1.0)
    return lib


def match_material(slicer_name: str, library: dict) -> Material | None:
    """The library material a slicer name means: exact name, then an alias, then a name inside it."""
    key = (slicer_name or "").strip().lower()
    if not key:
        return None
    for m in library.values():
        if key == m.name.lower() or key in m.aliases:
            return m
    # "Generic PETG", "Sunlu PETG 1.75": the longest library name or alias contained in it wins
    best = None
    for m in library.values():
        for word in [m.name.lower(), *m.aliases]:
            if word and word in key and (best is None or len(word) > best[0]):
                best = (len(word), m)
    return best[1] if best else None


def grams_from_length(length_m: float, material: Material, diameter_mm: float = 1.75) -> float:
    """Cura reports metres of filament; turn them into grams for this material."""
    import math
    area_mm2 = math.pi * (diameter_mm / 2) ** 2
    return length_m * 1000.0 * area_mm2 / 1000.0 * material.density_g_cm3


# ---------------------------------------------------------------------- parts
def load_inventory(directory: Path | None = None) -> dict:
    """{material name: {"stock_g", "reorder_at_g"}} from inventory.json (written by sync-sheet --inventory)."""
    raw = _read_json((directory or config_dir()) / "inventory.json", {})
    return raw if isinstance(raw, dict) else {}


def load_parts(directory: Path | None = None) -> dict:
    """{part name: price per unit} from parts.json (empty until you add your parts)."""
    raw = _read_json((directory or config_dir()) / "parts.json", {})
    return {name: float(spec.get("price", 0.0) if isinstance(spec, dict) else spec) for name, spec in raw.items()}


def bom_cost(bom: list, parts: dict) -> tuple[float, list, list]:
    """(total, lines, missing) for a bill of materials [{"part": name, "qty": n}, ...]."""
    total, lines, missing = 0.0, [], []
    for item in bom or []:
        name, qty = item.get("part"), float(item.get("qty", 1))
        price = parts.get(name)
        if price is None or price <= 0:
            missing.append(name)
            continue
        total += price * qty
        lines.append((name, qty, price * qty))
    return total, lines, missing


# ---------------------------------------------------------------- preferences
def find_preferences(model_path) -> tuple[dict, list]:
    """(merged preferences, files used) for a model: printtally.json files from the farthest folder down to
    the model's own folder, then <model>.printtally.json beside it. Nearer files override farther ones."""
    model = Path(model_path).resolve()
    folders = [p for p in [model.parent, *model.parent.parents]]
    files = [f / "printtally.json" for f in reversed(folders) if (f / "printtally.json").is_file()]
    sidecar = model.with_name(model.name + ".printtally.json")
    stem_sidecar = model.with_name(model.stem + ".printtally.json")
    for s in (stem_sidecar, sidecar):
        if s.is_file() and s not in files:
            files.append(s)
    merged = {}
    for f in files:
        merged.update(_read_json(f, {}))
    return merged, [str(f) for f in files]


# ------------------------------------------------------------------- pricing
@dataclass
class JobPrice:
    breakdown: object
    material_lines: list
    part_lines: list
    warnings: list
    licence: str
    stage: str
    sellable: bool
    currency: str = ""
    market: object = None            # Remas reference: market.MarketPrice, or None if not computed
    mine: object = None              # your own card's price for this part
    parts: list = field(default_factory=list)   # [(label, amount)] adding up to your price
    suggest_mode: str = "mine"
    cost_multiplier: float = 2.0
    hours: float = 0.0


def price_job(grams_by_material: dict, print_hours: float, prefs: dict | None = None,
              settings: dict | None = None, materials: dict | None = None, parts: dict | None = None,
              inventory: dict | None = None) -> JobPrice:
    """Price one job. grams_by_material: what the slicer reported, {slicer material name: grams}
    (an unnamed material comes in as "" and takes the preference file's material)."""
    prefs = dict(prefs or {})
    s = {**DEFAULT_SETTINGS, **(settings or {}), **prefs.get("settings", {})}
    materials = materials if materials is not None else load_materials()
    parts = parts if parts is not None else load_parts()
    inventory = inventory if inventory is not None else load_inventory()
    warnings, mlines = [], []
    material_cost, grams_total = 0.0, 0.0
    market_grams = {}
    for name, grams in grams_by_material.items():
        m = match_material(name or prefs.get("material", ""), materials)
        if m is None:
            m = match_material(prefs.get("material", ""), materials)
        if m is None:
            if not name and not prefs.get("material"):
                warnings.append(f"The slicer did not name the material ({grams:.0f} g): set \"material\" in a "
                                "printtally.json beside the model or in its folder (e.g. \"PETG\").")
            else:
                warnings.append(f"Unknown material '{name or prefs.get('material')}': add it to materials.json; "
                                f"{grams:.0f} g left out of the price.")
            continue
        if m.price_per_kg <= 0:
            warnings.append(f"No price for {m.name} yet: set price_per_kg in materials.json; "
                            f"{grams:.0f} g left out of the price.")
        grams = grams * m.weigh_factor          # what the scale says this filament really uses
        have = inventory.get(m.name) if isinstance(inventory.get(m.name), dict) else None
        if have and "stock_g" in have:
            left = float(have["stock_g"]) - grams
            if left < 0:
                warnings.append(f"Only {float(have['stock_g']):.0f} g of {m.name} in stock; this job needs {grams:.0f} g.")
            elif left < float(have.get("reorder_at_g", 0) or 0):
                warnings.append(f"{m.name} drops to {left:.0f} g after this job: below its reorder level.")
        cost = grams * m.price_per_kg / 1000.0
        material_cost += cost
        grams_total += grams
        mlines.append((m.name, grams, cost))
        market_grams[m.name] = market_grams.get(m.name, 0.0) + grams
    hw, plines, missing = bom_cost(prefs.get("bom", []), parts)
    if missing:
        warnings.append("No price for " + ", ".join(sorted(set(map(str, missing)))) + " in parts.json: "
                        "left out of the parts cost.")
    licence = prefs.get("licence", "own")
    if licence not in LICENCES:
        warnings.append(f"Unknown licence '{licence}'; treated as your own design.")
        licence = "own"
    royalty_pct = float(prefs.get("royalty_pct", 0.0)) if licence == "commercial" else 0.0
    royalty_fixed = float(prefs.get("royalty_fixed", 0.0)) if licence == "commercial" else 0.0
    sellable = licence != "personal"
    if not sellable:
        warnings.insert(0, "PERSONAL-USE LICENCE: do not sell this print (priced for your records only).")
    # Fixed monthly costs (a design subscription, a royalty) spread over the units expected per month.
    for item in prefs.get("monthly_fixed", []) or []:
        units = float(item.get("units_per_month") or 0)
        if units <= 0:
            warnings.append(f"Set units_per_month for '{item.get('name', 'monthly cost')}': not added to the price.")
            continue
        per_unit = float(item.get("amount") or 0) / units
        royalty_fixed += per_unit
        plines.append((item.get("name", "Monthly cost"), 1, per_unit))
    stage = prefs.get("stage", "prototype")
    if stage not in STAGES:
        stage = "prototype"
    if s.get("electricity_rate_per_kwh", 0) <= 0:
        warnings.append("Electricity rate not set (settings.json): electricity counted as 0.")
    inputs = CostInputs(
        model_weight_g=grams_total,
        price_per_kg=(material_cost / grams_total * 1000.0) if grams_total else 0.0,
        print_hours=print_hours,
        machine_rate_per_hour=float(s["machine_rate_per_hour"]),
        printer_watts=float(s["printer_watts"]),
        electricity_rate_per_kwh=float(s["electricity_rate_per_kwh"]),
        waste_pct=float(s["waste_pct"]),
        post_processing_hours=float(prefs.get("post_processing_hours", 0.0)),
        post_processing_rate_per_hour=float(s.get("post_processing_rate_per_hour", 0.0)),
        include_design_cost=bool(prefs.get("include_design_cost", False)),
        design_hours=float(prefs.get("design_hours", 0.0)),
        design_rate_per_hour=float(s.get("design_rate_per_hour", 0.0)),
        packaging_cost=float(prefs.get("packaging_cost", 0.0)),
        shipping_cost=float(prefs.get("shipping_cost", 0.0)),
        marketing_fee_pct=float(prefs.get("marketing_fee_pct", s.get("marketing_fee_pct", 0.0))),
        profit_margin_pct=float(prefs.get("profit_margin_pct", s["profit_margin_pct"])),
        hardware_cost=hw,
        royalty_fixed=royalty_fixed,
        royalty_pct=royalty_pct,
        failure_rate=float(prefs.get("failure_rate", s.get("failure_rate", 0.0))),
    )
    names = {m: prefs["market_material"] for m in market_grams} if prefs.get("market_material") else None
    remas = market_mod.market_price(market_grams, s.get("market_rates_path") or None, names,
                                    label="the Remas card")
    prices = {m.name: m.price_per_kg for m in materials.values() if m.price_per_kg > 0}
    mine, parts = None, []
    try:
        card, source = market_mod.my_card(
            s, prices, s.get("my_rates_path") or str(config_dir() / "my_rates.json"))
        mine = market_mod.market_price(market_grams, card=card, card_names=names, label="your rate card")
        mine.rates_path = source
    except ValueError as exc:
        warnings.append(str(exc))
    b = calculate_cost(inputs)
    if mine is not None and mine.available:
        labour_per_g = market_mod.cost_per_gram(0.0, s)["labour"]
        parts = market_mod.price_parts(
            grams_total, print_hours, b.material_cost, b.electricity_cost,
            b.machine_cost + b.waste_maintenance_cost, b.failure_cost,
            b.post_processing_cost + grams_total * labour_per_g, mine.customer, b.hardware_cost)
    return JobPrice(b, mlines, plines, warnings, licence, stage, sellable,
                    s.get("currency", ""), remas, mine, parts, str(s.get("suggest_mode", "mine")),
                    float(s.get("cost_multiplier", 2.0)), print_hours)


def format_job(job: JobPrice) -> str:
    c = job.currency
    money = (lambda v: f"{c}{v:,.2f}") if c else (lambda v: f"{v:,.2f}")
    b = job.breakdown
    out = [f"Stage: {job.stage}   Licence: {job.licence}" + ("" if job.sellable else "   ** DO NOT SELL **")]
    for name, grams, cost in job.material_lines:
        out.append(f"  {name:<10} {grams:8.1f} g   {money(cost)}")
    for name, qty, cost in job.part_lines:
        out.append(f"  {name:<24} x{qty:g}   {money(cost)}")
    if getattr(b, "failure_cost", 0):
        out.append(f"  {'Failed prints':<24}     {money(b.failure_cost)}")
    out += [f"Cost to make:  {money(b.cost_to_make)}",
            f"Selling price: {money(b.final_price)}" + ("" if job.sellable else "  (not for sale)")]
    if job.market is not None and job.sellable:
        cost = sum(v for _, v in job.parts[:-1]) if job.parts else b.cost_to_make
        grams = sum(g for _, g, _ in job.material_lines)
        out += market_mod.format_market(job.parts, job.mine, job.market, cost, job.suggest_mode,
                                        job.cost_multiplier, c, grams, job.hours)
    out += [f"! {w}" for w in job.warnings]
    return "\n".join(out)


def price_from_slicer(weights_g: list, names: list, print_hours: float, model_path=None,
                      plugin_settings: dict | None = None, directory: Path | None = None) -> JobPrice:
    """What a slicer plugin calls: grams and material names per extruder, the print time and the loaded
    model's file (for its printtally.json preferences). The plugin's own settings fill in shop rates and
    act as the price for any material your materials.json has not priced yet."""
    plugin_settings = dict(plugin_settings or {})
    materials = load_materials(directory)
    fallback = float(plugin_settings.get("price_per_kg", 0) or 0)
    if fallback > 0:
        for m in materials.values():
            if m.price_per_kg <= 0:
                m.price_per_kg = fallback
    settings = {**DEFAULT_SETTINGS,
                **{k: v for k, v in plugin_settings.items() if k in DEFAULT_SETTINGS or k.endswith("_rate_per_hour")
                   or k in ("marketing_fee_pct",)},
                **_read_json((directory or config_dir()) / "settings.json", {})}
    prefs, _used = find_preferences(model_path) if model_path else ({}, [])
    for key in ("packaging_cost", "shipping_cost", "post_processing_hours", "include_design_cost", "design_hours"):
        if key in plugin_settings and key not in prefs:
            prefs[key] = plugin_settings[key]
    # a filament profile picked in the plugin (PETG A-grade, ABS...) overrides the slicer's material names
    chosen = str(plugin_settings.get("filament_profile", "") or "")
    grams = {}
    for i, g in enumerate(weights_g or []):
        if g and g > 0:
            name = names[i] if i < len(names or []) else ""
            if chosen in materials:
                name = chosen
            grams[name] = grams.get(name, 0.0) + float(g)
    return price_job(grams, print_hours, prefs, settings, materials, load_parts(directory))
