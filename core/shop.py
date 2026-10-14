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


def load_materials(directory: Path | None = None) -> dict:
    """{name: Material}: the built-in list (no prices) updated by materials.json."""
    lib = {n: Material(n, 0.0, d, list(a)) for n, (d, a) in DEFAULT_MATERIALS.items()}
    raw = _read_json((directory or config_dir()) / "materials.json", {})
    for name, spec in raw.items():
        base = lib.get(name, Material(name))
        lib[name] = Material(name, float(spec.get("price_per_kg", base.price_per_kg)),
                             float(spec.get("density_g_cm3", base.density_g_cm3)),
                             [a.lower() for a in spec.get("aliases", base.aliases)])
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


def price_job(grams_by_material: dict, print_hours: float, prefs: dict | None = None,
              settings: dict | None = None, materials: dict | None = None, parts: dict | None = None) -> JobPrice:
    """Price one job. grams_by_material: what the slicer reported, {slicer material name: grams}
    (an unnamed material comes in as "" and takes the preference file's material)."""
    prefs = dict(prefs or {})
    s = {**DEFAULT_SETTINGS, **(settings or {}), **prefs.get("settings", {})}
    materials = materials if materials is not None else load_materials()
    parts = parts if parts is not None else load_parts()
    warnings, mlines = [], []
    material_cost, grams_total = 0.0, 0.0
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
        cost = grams * m.price_per_kg / 1000.0
        material_cost += cost
        grams_total += grams
        mlines.append((m.name, grams, cost))
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
    )
    return JobPrice(calculate_cost(inputs), mlines, plines, warnings, licence, stage, sellable,
                    s.get("currency", ""))


def format_job(job: JobPrice) -> str:
    c = job.currency
    money = (lambda v: f"{c}{v:,.2f}") if c else (lambda v: f"{v:,.2f}")
    b = job.breakdown
    out = [f"Stage: {job.stage}   Licence: {job.licence}" + ("" if job.sellable else "   ** DO NOT SELL **")]
    for name, grams, cost in job.material_lines:
        out.append(f"  {name:<10} {grams:8.1f} g   {money(cost)}")
    for name, qty, cost in job.part_lines:
        out.append(f"  {name:<24} x{qty:g}   {money(cost)}")
    out += [f"Cost to make:  {money(b.cost_to_make)}",
            f"Selling price: {money(b.final_price)}" + ("" if job.sellable else "  (not for sale)")]
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
