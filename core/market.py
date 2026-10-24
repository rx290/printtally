"""
Market price: what a print shop would charge for the same part, from a per-gram rate card.

The card (data/market_rates_pk.json by default) lists PKR per gram of finished part for each material,
in three weight tiers (under 500 g, 500-1000 g, above 1000 g), for customers and resellers. Some
materials also carry a fixed setup charge. Price = grams x rate for the tier + setup.

Two cards use the same shape: YOUR card (my_rates.json, built by `printtally build-card` from your own costs and
then edited by hand, or generated on the fly from your settings when you have no file yet) sets your price; the
Remas card is only a market reference. Pure Python, standard library only, so the Cura plugin can carry a copy (see tests).
"""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path

TIER_LABELS = ("under 500 g", "500-1000 g", "above 1000 g")

# PrintTally material name -> key on the rate card (case-insensitive match is tried first).
CARD_KEYS = {"pla": "PLA+", "pla+": "PLA+", "tpu": "TPU 95A", "pa": "Nylon PA6/PA12", "nylon": "Nylon PA6/PA12",
             "pc": "PC 3dxtech"}


def tier_index(grams: float) -> int:
    """0 under 500 g, 1 from 500 g up to 1000 g, 2 above 1000 g."""
    return 0 if grams < 500 else (1 if grams <= 1000 else 2)


def default_rates_path() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "market_rates_pk.json", here.parent / "data" / "market_rates_pk.json"):
        if candidate.is_file():
            return candidate
    return here.parent / "data" / "market_rates_pk.json"


def rates_path(configured=None) -> Path:
    """The rate card to use: an explicit path, else $PRINTTALLY_MARKET_RATES, else the built-in card."""
    chosen = configured or os.environ.get("PRINTTALLY_MARKET_RATES")
    return Path(chosen).expanduser() if chosen else default_rates_path()


def load_card(path=None) -> dict:
    """{card key: spec} for FDM and resin together. Raises ValueError if the card cannot be read."""
    p = rates_path(path)
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read the market rate card {p}: {exc}") from exc
    card = {}
    for section in ("fdm", "resin"):
        card.update(raw.get(section, {}))
    return card


def card_key(name: str, card: dict) -> str | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for k in card:
        if k.lower() == key:
            return k
    alias = CARD_KEYS.get(key)
    return alias if alias in card else None


@dataclass
class MarketPrice:
    lines: list = field(default_factory=list)   # (card key, grams, customer, reseller or None)
    customer: float = 0.0
    reseller: float | None = None
    tier: str = ""
    missing: list = field(default_factory=list)  # material names with no card entry
    messages: list = field(default_factory=list)
    rates_path: str = ""

    @property
    def available(self) -> bool:
        return bool(self.lines) and not self.missing


def market_price(grams_by_material: dict, rates=None, card_names: dict | None = None,
                 card: dict | None = None, label: str = "the rate card") -> MarketPrice:
    """grams_by_material: {PrintTally material name: grams}. The tier is chosen by the part's total grams.
    card_names optionally maps a material name to a card key (a model's "market_material" preference)."""
    out = MarketPrice(rates_path=str(rates_path(rates)) if card is None else label)
    if card is None:
        try:
            card = load_card(rates)
        except ValueError as exc:
            out.messages.append(str(exc))
            return out
    total = sum(grams_by_material.values())
    i = tier_index(total)
    out.tier = TIER_LABELS[i]
    customer, reseller = 0.0, 0.0
    reseller_ok = True
    for name, grams in grams_by_material.items():
        key = card_key((card_names or {}).get(name) or name, card)
        if key is None:
            out.missing.append(name)
            out.messages.append(f"No rate for '{name}' on {label} ({out.rates_path}): "
                                "price not shown. Set \"market_material\" in the model's printtally.json "
                                "to a card name, or add the material to the card.")
            continue
        spec = card[key]
        c = grams * spec["customer"][i] + float(spec.get("setup", 0) or 0)
        r = None
        if spec.get("reseller"):
            r = grams * spec["reseller"][i] + float(spec.get("setup", 0) or 0)
        else:
            reseller_ok = False
            out.messages.append(f"{key} has no reseller rate on {label}.")
        out.lines.append((key, grams, c, r))
        customer += c
        reseller += r or 0.0
    out.customer = customer
    out.reseller = reseller if (reseller_ok and out.lines) else None
    return out


def round_to(value: float, step: float = 50.0) -> float:
    return math.floor(value / step + 0.5) * step


# ------------------------------------------------------------------ your own card
CARD_DEFAULTS = {
    "labour_rate_per_hour": 150.0,     # Rs per hour of your time (support removal, finishing, packing)
    "labour_min_per_100g": 5.0,        # minutes of that work per 100 g of part
    "grams_per_hour": 12.0,            # typical output of a 0.4 mm nozzle: folds print time into a per-gram rate
    "profit_multiplier": 2.0,          # price = cost x this (2.0 = 100% on top of cost)
    "tier_discounts": [0.0, 0.15, 0.30],   # price drop for the 500-1000 g and above-1000 g tiers
    "reseller_discount": 0.17,         # resellers pay this much less than customers
    "setup_charges": {},               # {"PC 3dxtech": 1000}: fixed charge per material, added once
}


def cost_per_gram(price_per_kg: float, s: dict) -> dict:
    """What one gram of finished part costs you, split into its parts (all Rs per gram)."""
    gph = max(float(s.get("grams_per_hour") or CARD_DEFAULTS["grams_per_hour"]), 0.1)
    filament = price_per_kg / 1000.0
    electricity = float(s.get("printer_watts", 250)) / 1000.0 * float(s.get("electricity_rate_per_kwh", 0)) / gph
    wear = float(s.get("machine_rate_per_hour", 0)) / gph
    labour = (float(s.get("labour_rate_per_hour", CARD_DEFAULTS["labour_rate_per_hour"]))
              * float(s.get("labour_min_per_100g", CARD_DEFAULTS["labour_min_per_100g"])) / 60.0 / 100.0)
    before_failure = filament + electricity + wear + labour
    failure = before_failure / (1.0 - float(s.get("failure_rate", 0))) - before_failure
    cost = before_failure + failure
    return {"filament": filament, "electricity": electricity, "wear": wear, "failure": failure,
            "labour": labour, "cost": cost}


def build_card(settings: dict, price_per_kg: dict) -> dict:
    """Your rate card from your own costs. price_per_kg: {PrintTally material name: Rs per kg} (priced ones only).
    Same shape as the Remas card, so you can edit any number afterwards."""
    s = {**CARD_DEFAULTS, **{k: v for k, v in settings.items() if v is not None}}
    mult = float(s["profit_multiplier"])
    tiers = list(s["tier_discounts"]) + [s["tier_discounts"][-1]] * (3 - len(s["tier_discounts"]))
    reseller = float(s["reseller_discount"])
    fdm = {}
    for name, per_kg in price_per_kg.items():
        if per_kg <= 0:
            continue
        key = CARD_KEYS.get(name.lower(), name)
        base = cost_per_gram(per_kg, s)["cost"] * mult
        customer = [round(base * (1 - d), 1) for d in tiers[:3]]
        spec = {"customer": customer, "reseller": [round(r * (1 - reseller), 1) for r in customer]}
        if key in (s.get("setup_charges") or {}):
            spec["setup"] = s["setup_charges"][key]
        fdm[key] = spec
    return {"source": "Built by `printtally build-card` from your own costs; edit any number.",
            "unit": "per gram of finished part", "tiers": list(TIER_LABELS), "fdm": fdm,
            "assumptions": {k: s[k] for k in CARD_DEFAULTS} | {
                k: settings.get(k) for k in ("printer_watts", "electricity_rate_per_kwh", "machine_rate_per_hour",
                                             "failure_rate") if settings.get(k) is not None}}


def my_card(settings: dict, price_per_kg: dict, path=None):
    """(card dict, where it came from): your edited file if it exists, else one built from your settings."""
    p = Path(path).expanduser() if path else None
    if p is not None and p.is_file():
        try:
            raw = json.loads(p.read_text())
            return {**raw.get("fdm", {}), **raw.get("resin", {})}, str(p)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read your rate card {p}: {exc}") from exc
    built = build_card(settings, price_per_kg)
    return built["fdm"], "built from your settings"


# ------------------------------------------------------------------- suggestions
def suggest(mine: MarketPrice | None, remas: MarketPrice | None, cost: float, mode: str = "mine",
            multiplier: float = 2.0, step: float = 50.0) -> tuple[float, str]:
    """(suggested price, how). Default is YOUR card's customer price; Remas is used only when you choose it."""
    mode = {"customer": "mine", "reseller": "mine_reseller"}.get((mode or "mine").lower(), (mode or "mine").lower())
    if mode == "mine_reseller" and mine and mine.available and mine.reseller is not None:
        return round_to(mine.reseller, step), "your reseller price"
    if mode == "mine" and mine and mine.available:
        return round_to(mine.customer, step), "your price"
    if mode == "remas" and remas and remas.available:
        return round_to(remas.customer, step), "Remas customer price"
    if mode == "remas_reseller" and remas and remas.available and remas.reseller is not None:
        return round_to(remas.reseller, step), "Remas reseller price"
    if mode == "multiplier":
        return round_to(cost * multiplier, step), f"cost x {multiplier:g}"
    return round_to(cost * multiplier, step), f"cost x {multiplier:g} (no price for the {mode} mode)"


def price_parts(grams: float, hours: float, filament: float, electricity: float, wear: float, failure: float,
                labour: float, mine_price: float, other: float = 0.0) -> list:
    """[(label, amount)] that add up exactly to your price: the costs, then profit as what is left."""
    costs = [("Filament", filament), ("Electricity", electricity), ("Machine wear", wear),
             ("Failure allowance", failure), ("Labour", labour)]
    if other:
        costs.append(("Parts and other costs", other))
    return costs + [("Profit", mine_price - sum(v for _, v in costs))]


def format_market(parts: list | None, mine: MarketPrice | None, remas: MarketPrice | None, cost: float,
                  mode: str = "mine", multiplier: float = 2.0, currency: str = "", grams: float = 0.0,
                  hours: float = 0.0) -> list:
    """Report lines: how your price is built, then the Remas comparison, the gap and the suggested price."""
    def money(v):
        return f"{currency}{v:,.0f}" if currency else f"{v:,.0f}"
    out = [f"YOUR COST:     {money(cost)}"]
    if mine is not None and mine.available and parts:
        out.append(f"YOUR PRICE ({grams:,.0f} g, {hours:.2f} h, {mine.tier}):")
        for label, amount in parts:
            out.append(f"  {'+ ' if label != parts[0][0] else '  '}{label:<22} {money(amount):>10}")
        out.append(f"  = YOUR PRICE           {money(mine.customer):>10}"
                   + (f"   (reseller {money(mine.reseller)})" if mine.reseller is not None else ""))
    else:
        out.append("YOUR PRICE:    not available")
    if remas is not None and remas.available:
        line = f"market (Remas): customer {money(remas.customer)}" + (
            f", reseller {money(remas.reseller)}" if remas.reseller is not None else ", reseller not offered")
        out.append(line)
        if mine is not None and mine.available:
            gap = mine.customer - remas.customer
            out.append(f"Gap to market: {money(abs(gap))} {'above' if gap > 0 else 'below'} Remas (customer)"
                       if gap else "Gap to market: none (same as Remas customer)")
    else:
        out.append("market (Remas): not available")
    price, how = suggest(mine, remas, cost, mode, multiplier)
    out.append(f"Suggested:     {money(price)}  ({how})")
    for m in (mine.messages if mine else []) + (remas.messages if remas else []):
        out.append(f"! {m}")
    return out
