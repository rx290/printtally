"""Copy of core/profiles.py for the installed Cura plugin (a plugin folder is not on the repo path)."""
import json
import os
from pathlib import Path

NOT_SETTINGS = ("name", "description")
DEFAULT_PROFILE = "product"
# Pricing keys an older settings.json may hold: carried into the product profile until you save your own file.
LEGACY_KEYS = ("machine_rate_per_hour", "waste_pct", "profit_margin_pct", "failure_rate",
               "post_processing_rate_per_hour", "design_rate_per_hour", "packaging_cost", "shipping_cost",
               "marketing_fee_pct", "labour_rate_per_hour", "labour_min_per_100g", "grams_per_hour",
               "profit_multiplier", "tier_discounts", "reseller_discount", "suggest_mode", "cost_multiplier")


def config_dir() -> Path:
    return Path(os.environ.get("PRINTTALLY_CONFIG") or Path.home() / ".config" / "printtally")


def data_dir() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "data", here.parent / "data"):
        if candidate.is_dir():
            return candidate
    return here.parent / "data"


def _read(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def list_profiles(directory: Path | None = None) -> list:
    names = {p.stem for p in (data_dir() / "profiles").glob("*.json")}
    names |= {p.stem for p in ((directory or config_dir()) / "profiles").glob("*.json")}
    return sorted(names)


def user_path(name: str, directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "profiles" / f"{name}.json"


def load_profile(name: str, directory: Path | None = None) -> dict:
    """The profile's settings (name and description included). Raises ValueError for an unknown profile."""
    name = (name or DEFAULT_PROFILE).strip().lower()
    shipped = _read(data_dir() / "profiles" / f"{name}.json", None)
    mine_path = user_path(name, directory)
    mine = _read(mine_path, None) if mine_path.is_file() else None
    if shipped is None and mine is None:
        raise ValueError(f"No profile called '{name}'. Profiles: {', '.join(list_profiles(directory)) or 'none'}.")
    out = dict(shipped or {})
    if name == DEFAULT_PROFILE and mine is None:
        legacy = _read((directory or config_dir()) / "settings.json", {})
        out.update({k: legacy[k] for k in LEGACY_KEYS if k in legacy})
    out.update(mine or {})
    out["name"] = name
    return out


def profile_settings(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if k not in NOT_SETTINGS}


def parse_value(text: str):
    """key=value from the command line: numbers, true/false, lists and quoted text are read as JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def set_value(name: str, key: str, value, directory: Path | None = None) -> Path:
    """Save one setting into your copy of a profile (created from the current one on first edit)."""
    current = load_profile(name, directory)
    if key in NOT_SETTINGS:
        raise ValueError(f"'{key}' cannot be edited here.")
    if key not in profile_settings(current):
        known = ", ".join(sorted(profile_settings(current)))
        raise ValueError(f"'{key}' is not a profile setting. Known: {known}.")
    path = user_path(current["name"], directory)
    mine = _read(path, None) if path.is_file() else None
    if mine is None:
        mine = dict(current)
    mine[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mine, indent=2) + "\n")
    return path


def default_profile(directory: Path | None = None) -> str:
    """The profile used when none is chosen: $PRINTTALLY_PROFILE, else settings.json "default_profile",
    else "product". Returns "" when `strict` would be needed by callers; see configured_default()."""
    return configured_default(directory) or DEFAULT_PROFILE


def configured_default(directory: Path | None = None) -> str:
    """The profile you chose as default (env or settings.json), or "" if you never chose one."""
    return (os.environ.get("PRINTTALLY_PROFILE")
            or str(_read((directory or config_dir()) / "settings.json", {}).get("default_profile", "") or ""))


def set_default(name: str, directory: Path | None = None) -> Path:
    load_profile(name, directory)       # must exist
    path = (directory or config_dir()) / "settings.json"
    raw = _read(path, {})
    raw["default_profile"] = name.strip().lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n")
    return path


# ---------------------------------------------------------------------- printers
def list_printers(directory: Path | None = None) -> list:
    names = {p.stem for p in (data_dir() / "printers").glob("*.json")}
    names |= {p.stem for p in ((directory or config_dir()) / "printers").glob("*.json")}
    return sorted(names)


def printer_path(name: str, directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "printers" / f"{name}.json"


def load_printer(name: str, directory: Path | None = None) -> dict:
    key = (name or "").strip().lower()
    shipped = _read(data_dir() / "printers" / f"{key}.json", None)
    mine = _read(printer_path(key, directory), None) if printer_path(key, directory).is_file() else None
    if shipped is None and mine is None:
        raise ValueError(f"No printer called '{name}'. Printers: {', '.join(list_printers(directory)) or 'none'}.")
    out = {**(shipped or {}), **(mine or {}), "name": key}
    return out


def printer_rates(printer: dict, heads: int | None = None) -> dict:
    """What this printer costs to run, as profile settings. heads = heads printing at once (default: all of them;
    use 1 for a single-part job): throughput is grams per hour x heads, so payback per gram is divided by the heads."""
    have = max(int(printer.get("heads", 1) or 1), 1)
    used = min(max(int(heads or have), 1), have)
    hours = max(float(printer.get("payback_years", 2)) * float(printer.get("paid_hours_per_year", 2400)), 1.0)
    payback_h = float(printer["cost"]) / hours
    gph = float(printer.get("grams_per_hour", 12.0)) * used
    watts = float(printer.get("watts", 150.0)) + float(printer.get("extra_watts_per_head", 0.0)) * (used - 1)
    return {"machine_rate_per_hour": round(payback_h + float(printer.get("maintenance_per_hour", 0.0)), 2),
            "printer_watts": watts, "grams_per_hour": gph, "heads_in_use": used,
            "printer_name": printer["name"], "purge_waste_g": float(printer.get("purge_waste_g", 0) or 0), "payback_per_hour": round(payback_h, 2),
            "payback_per_g": round(payback_h / gph, 3)}


def set_printer_value(name: str, key: str, value, directory: Path | None = None) -> Path:
    current = load_printer(name, directory)
    if key == "name" or key not in current:
        raise ValueError(f"'{key}' is not a printer setting. Known: {', '.join(k for k in sorted(current) if k != 'name')}.")
    path = printer_path(current["name"], directory)
    mine = _read(path, None) if path.is_file() else None
    mine = dict(current) if mine is None else mine
    mine[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mine, indent=2) + "\n")
    return path


def configured_printer(directory: Path | None = None) -> str:
    """The printer you chose as default (env or settings.json "default_printer"), or ""."""
    return (os.environ.get("PRINTTALLY_PRINTER")
            or str(_read((directory or config_dir()) / "settings.json", {}).get("default_printer", "") or ""))


def set_default_printer(name: str, directory: Path | None = None) -> Path:
    load_printer(name, directory)
    path = (directory or config_dir()) / "settings.json"
    raw = _read(path, {})
    raw["default_printer"] = name.strip().lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n")
    return path
