"""
Copy of core/shop.py for the installed Cura plugin (a plugin folder is not on the repo path).
tests/test_plugin_cost_model_matches_core.py keeps it identical below this docstring.
"""
from dataclasses import dataclass, field
import copy
import json
import os
from pathlib import Path

from .cost_model import CostInputs, calculate_cost  # noqa: F401  (plugin copy imports .cost_model too)
from . import market as market_mod
from . import profiles as profiles_mod

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
    "electricity_rate_per_kwh": 90.0,       # Rs per unit (kWh): change it in settings.json
    "waste_pct": 0.07,
    "profit_margin_pct": 0.25,
    "filament_diameter_mm": 1.75,
    "market_rates_path": "",             # the Remas reference card; empty = the one shipped in data/
    "my_rates_path": "",                 # your own card; empty = my_rates.json in the config folder if it exists
    "suggest_mode": "mine",              # mine | reseller | remas | remas_reseller | multiplier
    "cost_multiplier": 2.0,              # used by the multiplier mode
    "failure_rate": 0.0,
    "post_processing_rate_per_hour": 0.0,
    "design_rate_per_hour": 0.0,
    "packaging_cost": 0.0,
    "shipping_cost": 0.0,
    "marketing_fee_pct": 0.0,
    "include_design_cost": False,
    "allow_royalty": True,               # a profile switch: False ignores any royalty
    "labour_min_per_order": 0.0,         # minutes of handling per order, added once to your price
    "rounding_step": 50.0,               # the suggested price is rounded to this
    "minimum_price": 0.0,                # no suggested price below this (minimum order)
    "purge_waste_g": 0.0,                # extra filament purged on a multi-colour job (BMCU, AMS), grams
    "batch": {},                         # {"PETG": "original"}: which batch of a material to price from
    "order_kg": {},                      # {"ABS": 12}: order size, picks a quantity tier of my_price
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
    price_per_kg: float = 0.0          # what you pay (my_price); 0 = not set yet: reported, never guessed
    density_g_cm3: float = 1.24
    aliases: list = field(default_factory=list)
    weigh_factor: float = 1.0          # scale grams / slicer grams, from printkit weigh (1 = trust the slicer)
    batches: dict = field(default_factory=dict)   # name -> {"my_price": number or [{"min_kg", "price"}], "list_price", "note"}
    batch: str = ""                    # the batch price_per_kg comes from
    list_price: float = 0.0            # open-market price without your reference: a note, never used in the maths
    remas_key: str = ""                # this material's name on the Remas rate card
    price_status: str = ""             # "estimate" when the price is a guess
    price_required: bool = False       # price unknown: nothing is invented until you set one
    note: str = ""


def _my_price(spec: dict, order_kg: float = 0.0) -> float:
    price = spec.get("my_price", spec.get("price_per_kg", 0))
    if isinstance(price, list):                   # quantity tiers: the highest tier the order reaches
        best = None
        for tier in sorted(price, key=lambda x: float(x.get("min_kg", 0))):
            if float(tier.get("min_kg", 0)) <= order_kg:
                best = tier
        return float((best or price[0]).get("price", 0))
    return float(price or 0)


def parse_choice(text: str) -> tuple:
    """"PETG:original" -> ("PETG", "original"); "PETG" -> ("PETG", "")."""
    name, _, batch = (text or "").partition(":")
    return name.strip(), batch.strip()


def select_batch(m: Material, choice: str = "", order_kg: float = 0.0) -> Material:
    """Point m at one of its batches (default: the cheapest). Raises ValueError for an unknown batch."""
    if not m.batches:
        if choice:
            raise ValueError(f"{m.name} has no batches (asked for '{choice}').")
        return m
    if choice:
        key = next((b for b in m.batches if b.lower() == choice.lower()), None)
        if key is None:
            raise ValueError(f"{m.name} has no batch '{choice}'. Batches: {', '.join(m.batches)}.")
    else:
        priced = {b: _my_price(s, order_kg) for b, s in m.batches.items() if _my_price(s, order_kg) > 0}
        key = min(priced, key=priced.get) if priced else next(iter(m.batches))
    spec = m.batches[key]
    m.batch, m.price_per_kg = key, _my_price(spec, order_kg)
    m.list_price = float(spec.get("list_price", 0) or 0)
    return m


def _material_files(directory: Path | None) -> list:
    """Material profiles: the ones shipped in data/materials, then your editable copies in
    <config>/materials/ (same name = yours replaces the shipped one)."""
    files = {}
    for folder in (profiles_mod.data_dir() / "materials", (directory or config_dir()) / "materials"):
        for f in sorted(folder.glob("*.json")):
            spec = _read_json(f, None)
            if isinstance(spec, dict):
                files[spec.get("name", f.stem)] = spec
    return list(files.values())


def load_materials(directory: Path | None = None) -> dict:
    """{name: Material}: built-in names, the material profiles (shipped, then yours), then the old materials.json."""
    lib = {n: Material(n, 0.0, d, list(a)) for n, (d, a) in DEFAULT_MATERIALS.items()}
    for spec in _material_files(directory):
        name = spec["name"]
        base = lib.get(name, Material(name))
        m = Material(name, 0.0, float(spec.get("density_g_cm3", base.density_g_cm3)),
                     [a.lower() for a in spec.get("aliases", base.aliases)],
                     float(spec.get("weigh_factor", base.weigh_factor)) or 1.0,
                     dict(spec.get("batches", {})), "", 0.0, str(spec.get("remas_key", "")),
                     str(spec.get("price_status", "")), bool(spec.get("price_required", False)),
                     str(spec.get("note", "")))
        if "my_price" in spec or "price_per_kg" in spec:
            m.batches = {"default": {k: spec[k] for k in ("my_price", "price_per_kg", "list_price") if k in spec}}
        lib[name] = select_batch(m)
    raw = _read_json((directory or config_dir()) / "materials.json", {})
    for name, spec in raw.items():
        base = lib.get(name, Material(name))
        lib[name] = Material(name, float(spec.get("price_per_kg", base.price_per_kg)),
                             float(spec.get("density_g_cm3", base.density_g_cm3)),
                             [a.lower() for a in spec.get("aliases", base.aliases)],
                             float(spec.get("weigh_factor", base.weigh_factor)) or 1.0,
                             base.batches, "materials.json" if "price_per_kg" in spec else base.batch,
                             base.list_price, base.remas_key, base.price_status, base.price_required, base.note)
        if "price_per_kg" in spec and float(spec["price_per_kg"]) > 0:
            lib[name].price_required = False
            lib[name].price_status = str(spec.get("price_status", ""))
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
    profile: str = ""                # the pricing profile this price used
    notes: list = field(default_factory=list)   # facts, not problems: batch prices, minimum order, ...
    factors: dict = field(default_factory=dict)  # print factors shown in the header
    rounding_step: float = 50.0
    minimum_price: float = 0.0


def build_settings(directory: Path | None = None, profile: str | None = None, printer: str | None = None,
                   heads: int | None = None, prefs: dict | None = None, base: dict | None = None) -> dict:
    """The settings one price is made from. Layers, later wins: built-in defaults, settings.json (shop-wide:
    currency, electricity, default profile and printer), `base`, the pricing profile, then the printer's machine
    rate, watts and speed. Profile: argument, $PRINTTALLY_PROFILE, the model's preferences, settings.json
    "default_profile", else "product" (your original settings). Printer: argument, the model's preferences,
    $PRINTTALLY_PRINTER, settings.json "default_printer", else the profile's own "printer", else none."""
    prefs = prefs or {}
    shop_wide = _read_json((directory or config_dir()) / "settings.json", {})
    name = (profile or os.environ.get("PRINTTALLY_PROFILE") or prefs.get("profile")
            or shop_wide.get("default_profile") or profiles_mod.DEFAULT_PROFILE)
    prof = profiles_mod.load_profile(name, directory)
    settings = {**DEFAULT_SETTINGS, **shop_wide, **(base or {}), **profiles_mod.profile_settings(prof)}
    settings["profile_name"] = prof["name"]
    if printer and printer.lower() == "none":      # "none": the profile's own machine values, no printer
        printer = None
        chosen = ""
    else:
        chosen = (printer or prefs.get("printer") or profiles_mod.configured_printer(directory)
                  or prof.get("printer") or "")
    if chosen:
        rates = profiles_mod.printer_rates(profiles_mod.load_printer(chosen, directory),
                                           heads or prefs.get("heads_in_use") or settings.get("heads_in_use"))
        settings.update(rates)
    return settings


def price_job(grams_by_material: dict, print_hours: float, prefs: dict | None = None,
              settings: dict | None = None, materials: dict | None = None, parts: dict | None = None,
              inventory: dict | None = None, factors: dict | None = None) -> JobPrice:
    """Price one job. grams_by_material: what the slicer reported, {slicer material name: grams}
    (an unnamed material comes in as "" and takes the preference file's material)."""
    prefs = dict(prefs or {})
    s = {**DEFAULT_SETTINGS, **(settings or {}), **prefs.get("settings", {})}
    materials = materials if materials is not None else load_materials()
    parts = parts if parts is not None else load_parts()
    inventory = inventory if inventory is not None else load_inventory()
    notes = []
    choices = dict(s.get("batch") or {})
    order_kg = dict(s.get("order_kg") or {})
    chosen_name, chosen_batch = parse_choice(prefs.get("material", ""))
    if chosen_batch:
        prefs["material"] = chosen_name
        found = match_material(chosen_name, materials)
        choices[found.name if found else chosen_name] = chosen_batch
    warnings, mlines = [], []
    if choices or order_kg:                 # batches are picked on copies: the caller's library stays as it was
        materials = {k: copy.copy(v) for k, v in materials.items()}
        for key, m in materials.items():
            if key in choices or (m.batches and key in order_kg):
                select_batch(m, choices.get(key, ""), float(order_kg.get(key, 0) or 0))
    purge = float(prefs.get("purge_waste_g", s.get("purge_waste_g", 0)) or 0)
    if purge > 0 and grams_by_material:        # multi-colour purge: extra filament, charged to the biggest material
        grams_by_material = dict(grams_by_material)
        biggest = max(grams_by_material, key=grams_by_material.get)
        grams_by_material[biggest] += purge
        notes.append(f"Includes {purge:g} g of purge waste (multi-colour job).")
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
            warnings.append(f"No price for {m.name} yet: set a price for {m.name} (my_price in its material file, "
                            f"or price_per_kg in materials.json); {grams:.0f} g left out of the price.")
        else:
            line = f"{m.name}{' [' + m.batch + ']' if m.batch else ''}: my price Rs {m.price_per_kg:,.0f}/kg"
            if m.list_price:
                line += f" (list price Rs {m.list_price:,.0f}/kg, for reference only)"
            if m.price_status == "estimate":
                line += "  ** ESTIMATE: replace with a real price **"
            if line not in notes:
                notes.append(line)
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
    pays_royalty = licence == "commercial" and bool(s.get("allow_royalty", True))
    royalty_pct = float(prefs.get("royalty_pct", 0.0)) if pays_royalty else 0.0
    royalty_fixed = float(prefs.get("royalty_fixed", 0.0)) if pays_royalty else 0.0
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
    watts = float(s["printer_watts"])
    inputs = CostInputs(
        model_weight_g=grams_total,
        price_per_kg=(material_cost / grams_total * 1000.0) if grams_total else 0.0,
        print_hours=print_hours,
        machine_rate_per_hour=float(s["machine_rate_per_hour"]),
        printer_watts=watts,
        electricity_rate_per_kwh=float(s["electricity_rate_per_kwh"]),
        waste_pct=float(s["waste_pct"]),
        post_processing_hours=float(prefs.get("post_processing_hours", s.get("post_processing_hours", 0.0))),
        post_processing_rate_per_hour=float(s.get("post_processing_rate_per_hour", 0.0)),
        include_design_cost=bool(prefs.get("include_design_cost", s.get("include_design_cost", False))),
        design_hours=float(prefs.get("design_hours", 0.0)),
        design_rate_per_hour=float(s.get("design_rate_per_hour", 0.0)),
        packaging_cost=float(prefs.get("packaging_cost", s.get("packaging_cost", 0.0))),
        shipping_cost=float(prefs.get("shipping_cost", s.get("shipping_cost", 0.0))),
        marketing_fee_pct=float(prefs.get("marketing_fee_pct", s.get("marketing_fee_pct", 0.0))),
        profit_margin_pct=float(prefs.get("profit_margin_pct", s["profit_margin_pct"])),
        hardware_cost=hw,
        royalty_fixed=royalty_fixed,
        royalty_pct=royalty_pct,
        failure_rate=float(prefs.get("failure_rate", s.get("failure_rate", 0.0))),
    )
    names = {m: prefs["market_material"] for m in market_grams} if prefs.get("market_material") else None
    remas_names = {m: prefs.get("market_material") or materials[m].remas_key
                   for m in market_grams if m in materials and (prefs.get("market_material") or materials[m].remas_key)}
    remas = market_mod.market_price(market_grams, s.get("market_rates_path") or None, remas_names or None,
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
        order_labour = 0.0
        cpg = market_mod.cost_per_gram(
            (material_cost / grams_total * 1000.0) if grams_total else 0.0, s)["cost"]
        real_cost = (b.material_cost + b.electricity_cost + b.machine_cost + b.waste_maintenance_cost
                     + b.failure_cost + b.post_processing_cost + grams_total * labour_per_g + b.hardware_cost)
        real_cost *= 1.0 + float(s.get("overhead_pct", 0) or 0)
        floor_mult = float(s.get("price_floor_multiplier", 0) or 0)
        if floor_mult > 0 and mine.customer < real_cost * floor_mult:
            notes.append(f"Slow print: this job's real time costs {real_cost:,.0f}; price raised from "
                         f"{mine.customer:,.0f} to cost x {floor_mult:g} = {real_cost * floor_mult:,.0f}.")
            mine.customer = real_cost * floor_mult
            if mine.reseller is not None:
                mine.reseller = mine.customer * (1 - float(s["reseller_discount"]))
        if grams_total > 0 and mine.customer > 0:
            notes.append(f"Cost basis Rs {cpg:,.1f}/g, your price Rs {mine.customer / grams_total:,.1f}/g "
                         f"(about cost x {mine.customer / grams_total / cpg:.1f}); this print's real cost Rs {real_cost:,.0f}.")
        minimum = float(s.get("minimum_price", 0) or 0)
        if minimum > 0 and mine.customer < minimum:
            notes.append(f"Minimum order applied: your price {mine.customer:,.0f} raised to {minimum:,.0f}.")
            mine.customer = minimum
            if mine.reseller is not None:
                mine.reseller = max(mine.reseller, minimum * (1 - float(s["reseller_discount"])))
        parts = market_mod.price_parts(
            grams_total, print_hours, b.material_cost, b.electricity_cost,
            b.machine_cost + b.waste_maintenance_cost, b.failure_cost,
            b.post_processing_cost + grams_total * labour_per_g + order_labour, mine.customer, b.hardware_cost)
    return JobPrice(b, mlines, plines, warnings, licence, stage, sellable,
                    s.get("currency", ""), remas, mine, parts, str(s.get("suggest_mode", "mine")),
                    float(s.get("cost_multiplier", 2.0)), print_hours, str(s.get("profile_name", "")), notes,
                    {**(factors or {}), "watts": watts, "printer": str(s.get("printer_name", "")),
                     "payback_per_g": s.get("payback_per_g"), "heads": s.get("heads_in_use"),
                     "kwh": watts * print_hours / 1000.0, "electricity_rate": float(s["electricity_rate_per_kwh"]),
                     "grams": grams_total},
                    float(s.get("rounding_step", 50.0) or 50.0), float(s.get("minimum_price", 0) or 0))


def format_job(job: JobPrice) -> str:
    c = job.currency
    money = (lambda v: f"{c}{v:,.2f}") if c else (lambda v: f"{v:,.2f}")
    b = job.breakdown
    out = [f"Profile: {job.profile or 'none'}   Stage: {job.stage}   Licence: {job.licence}"
           + ("" if job.sellable else "   ** DO NOT SELL **")]
    f = job.factors or {}
    if f:
        bits = [f"{job.hours:.2f} h", f"{f.get('grams', 0):,.0f} g"]
        if f.get("layer_height"):
            bits.append(f"layer {f['layer_height']:g} mm")
        if f.get("line_width"):
            bits.append(f"line {f['line_width']:g} mm")
        if f.get("printer"):
            bits.append(f"printer {f['printer']}" + (f" x{f['heads']} heads" if (f.get("heads") or 1) > 1 else "")
                        + (f", payback Rs {f['payback_per_g']:.2f}/g" if f.get("payback_per_g") is not None else ""))
        bits.append(f"{f.get('watts', 0):g} W = {f.get('kwh', 0):.2f} kWh"
                    f" x {f.get('electricity_rate', 0):g} = {money(f.get('kwh', 0) * f.get('electricity_rate', 0))}")
        out.append("Print factors: " + ", ".join(bits))
    out += [f"  {n}" for n in job.notes]
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
                                        job.cost_multiplier, c, grams, job.hours,
                                        job.rounding_step, job.minimum_price)
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
            if m.price_per_kg <= 0 and not m.price_required:
                m.price_per_kg = fallback
    prefs, _used = find_preferences(model_path) if model_path else ({}, [])
    chosen_profile = str(plugin_settings.get("profile", "") or "")
    own = {k: v for k, v in plugin_settings.items() if k in DEFAULT_SETTINGS or k.endswith("_rate_per_hour")
           or k in ("marketing_fee_pct",)}
    if chosen_profile or prefs.get("profile") or profiles_mod.configured_default(directory):
        settings = build_settings(directory, chosen_profile or None, plugin_settings.get("printer") or None,
                                  None, prefs, own)
    else:   # nothing chosen anywhere: the plugin's own dialog values apply, as before profiles existed
        settings = {**DEFAULT_SETTINGS, **own, **_read_json((directory or config_dir()) / "settings.json", {})}
        settings["profile_name"] = "plugin settings"
    for key in ("packaging_cost", "shipping_cost", "post_processing_hours", "include_design_cost", "design_hours"):
        if key in plugin_settings and key not in prefs and settings.get("profile_name") == "plugin settings":
            prefs[key] = plugin_settings[key]
    # a filament profile picked in the plugin (PETG A-grade, ABS...) overrides the slicer's material names
    chosen = str(plugin_settings.get("filament_profile", "") or "")
    chosen_name = parse_choice(chosen)[0]
    if chosen_name in materials:
        prefs["material"] = chosen
    grams = {}
    for i, g in enumerate(weights_g or []):
        if g and g > 0:
            name = names[i] if i < len(names or []) else ""
            if chosen_name in materials:
                name = chosen_name
            grams[name] = grams.get(name, 0.0) + float(g)
    factors = {k: plugin_settings[k] for k in ("layer_height", "line_width") if plugin_settings.get(k)}
    return price_job(grams, print_hours, prefs, settings, materials, load_parts(directory), factors=factors)
