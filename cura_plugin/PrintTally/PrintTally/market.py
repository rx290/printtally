"""
Market price: what a print shop would charge for the same part, from a per-gram rate card.

The card (data/market_rates_pk.json by default) lists PKR per gram of finished part for each material,
in three weight tiers (under 500 g, 500-1000 g, above 1000 g), for customers and resellers. Some
materials also carry a fixed setup charge. Price = grams x rate for the tier + setup.

Pure Python, standard library only, so the Cura plugin can carry a copy (see tests).
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


def market_price(grams_by_material: dict, rates=None, card_names: dict | None = None) -> MarketPrice:
    """grams_by_material: {PrintTally material name: grams}. The tier is chosen by the part's total grams.
    card_names optionally maps a material name to a card key (a model's "market_material" preference)."""
    out = MarketPrice(rates_path=str(rates_path(rates)))
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
            out.messages.append(f"No market rate for '{name}' on the rate card ({out.rates_path}): "
                                "market price not shown. Set \"market_material\" in the model's printtally.json "
                                "to a card name, or add the material to the card.")
            continue
        spec = card[key]
        c = grams * spec["customer"][i] + float(spec.get("setup", 0) or 0)
        r = None
        if spec.get("reseller"):
            r = grams * spec["reseller"][i] + float(spec.get("setup", 0) or 0)
        else:
            reseller_ok = False
            out.messages.append(f"{key} has no reseller rate on the card.")
        out.lines.append((key, grams, c, r))
        customer += c
        reseller += r or 0.0
    out.customer = customer
    out.reseller = reseller if (reseller_ok and out.lines) else None
    return out


def round_to(value: float, step: float = 50.0) -> float:
    return math.floor(value / step + 0.5) * step


def suggest(market: MarketPrice, cost: float, mode: str = "customer", multiplier: float = 2.0,
            step: float = 50.0) -> tuple[float, str]:
    """(suggested price, how it was chosen). Falls back to cost x multiplier when the card has no price."""
    mode = (mode or "customer").lower()
    if mode == "reseller" and market.available and market.reseller is not None:
        return round_to(market.reseller, step), "reseller price"
    if mode == "customer" and market.available:
        return round_to(market.customer, step), "customer price"
    if mode == "multiplier":
        return round_to(cost * multiplier, step), f"cost x {multiplier:g}"
    return round_to(cost * multiplier, step), f"cost x {multiplier:g} (no market price for the {mode} mode)"


def format_market(market: MarketPrice, cost: float, mode: str = "customer", multiplier: float = 2.0,
                  currency: str = "") -> list:
    """Report lines: your cost, the market price, margin and the suggested price."""
    def money(v):
        return f"{currency}{v:,.0f}" if currency else f"{v:,.0f}"
    out = [f"YOUR COST:     {money(cost)}"]
    if market.available:
        out.append(f"MARKET PRICE:  customer {money(market.customer)}"
                   + (f", reseller {money(market.reseller)}" if market.reseller is not None
                      else ", reseller not offered") + f"  ({market.tier})")
        out.append(f"Margin:        {money(market.customer - cost)} vs customer"
                   + (f", {money(market.reseller - cost)} vs reseller" if market.reseller is not None else ""))
    else:
        out.append("MARKET PRICE:  not available")
    price, how = suggest(market, cost, mode, multiplier)
    out.append(f"Suggested:     {money(price)}  ({how})")
    out += [f"! {m}" for m in market.messages]
    return out
