"""Sync published CSV prices into the local shop files, retaining dated history."""
import csv
import io
import json
import math
from datetime import date, datetime, timezone
from http.client import HTTPException
from urllib.request import urlopen

from .shop import config_dir

HISTORY_FIELDS = ("date", "kind", "name", "price", "synced_at")


def download(url):
    with urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8-sig")


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_json(path, data):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _number(value):
    """A non-negative number; accepts what a published Google Sheet writes ("Rs 3,500", "1,200 g")."""
    if isinstance(value, str):
        value = value.replace(",", "").replace("Rs", "").replace("PKR", "").replace("g", "").strip()
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Invalid nonnegative number: {value!r}")
    return number


def _parse(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    reader.fieldnames = [h.strip().lower() for h in (reader.fieldnames or [])]
    if not {"date", "kind", "name", "price"}.issubset(reader.fieldnames):
        raise ValueError("Sheet requires date, kind, name, price columns")
    rows = []
    for line, raw in enumerate(reader, 2):
        if not any(raw.values()):
            continue
        try:
            row = {k: (v or "").strip() for k, v in raw.items() if k is not None}
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"]:
                raise ValueError("date must be YYYY-MM-DD")
            row["kind"] = row["kind"].lower()
            if row["kind"] not in ("material", "part") or not row["name"]:
                raise ValueError("kind must be material or part, and name must be nonempty")
            row["price"] = _number(row["price"])
            for field in ("density_g_cm3", "weigh_factor"):
                if row.get(field):
                    row[field] = _number(row[field])
            rows.append(row)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Sheet row {line}: {exc}") from exc
    return rows


def price_history(name, directory=None):
    path = (directory or config_dir()) / "price_history.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        rows = [r for r in csv.DictReader(stream) if r["name"].casefold() == name.casefold()]
    return sorted(rows, key=lambda r: (r["date"], r["synced_at"]))


def sync_sheet(url=None, directory=None, today=None):
    """Return printable changes. Blank optional cells preserve local fields.

    Equal dates use the last sheet row. History includes future rows and revised
    prices, keyed by date, kind, name and numeric price, excluding sync time.
    """
    directory = directory or config_dir()
    settings = _json(directory / "settings.json")
    url = url or settings.get("sheet_csv_url")
    if not url:
        raise ValueError("Provide a published CSV URL: sync-sheet URL")
    cache = directory / "sheet_cache.csv"
    messages = []
    try:
        text = download(url)
    except (OSError, HTTPException) as exc:
        if not cache.exists():
            raise ValueError(f"Download failed and no cached sheet exists: {exc}") from exc
        text = cache.read_text(encoding="utf-8")
        messages.append(f"Download failed ({exc}); using cached sheet: {cache}")
    rows = _parse(text)
    current = {}
    today = (today or date.today()).isoformat()
    for row in rows:
        key = row["kind"], row["name"]
        if row["date"] <= today and (key not in current or row["date"] >= current[key]["date"]):
            current[key] = row
    catalogs = {kind: _json(directory / filename) for kind, filename in
                (("material", "materials.json"), ("part", "parts.json"))}
    for (kind, name), row in current.items():
        field = "price_per_kg" if kind == "material" else "price"
        original = catalogs[kind].get(name, {})
        spec = dict(original) if isinstance(original, dict) else {field: original}
        updates = {field: row["price"]}
        for optional in ("density_g_cm3", "weigh_factor", "aliases", "notes"):
            if row.get(optional) != "" and row.get(optional) is not None:
                updates[optional] = ([a.strip() for a in row[optional].split(";") if a.strip()]
                                     if optional == "aliases" else row[optional])
        for key, value in updates.items():
            if spec.get(key) != value:
                label = name if key == field else f"{name} ({key})"
                messages.append(f"{label}: {spec.get(key, 'unset')} -> {value}")
        catalogs[kind][name] = {**spec, **updates}
    history = directory / "price_history.csv"
    seen = set()
    if history.exists():
        with history.open(encoding="utf-8", newline="") as stream:
            seen = {(r["date"], r["kind"], r["name"], float(r["price"])) for r in csv.DictReader(stream)}
    directory.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HISTORY_FIELDS)
        if stream.tell() == 0:
            writer.writeheader()
        synced_at = datetime.now(timezone.utc).isoformat()
        for row in rows:
            key = tuple(row[f] for f in HISTORY_FIELDS[:-1])
            if key not in seen:
                writer.writerow({**dict(zip(HISTORY_FIELDS[:-1], key)), "synced_at": synced_at})
                seen.add(key)
    for kind, filename in (("material", "materials.json"), ("part", "parts.json")):
        _write_json(directory / filename, catalogs[kind])
    settings["sheet_csv_url"] = url
    _write_json(directory / "settings.json", settings)
    temporary = cache.with_suffix(".csv.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(cache)
    return messages or ["No price changes."]


def sync_inventory(url=None, directory=None):
    """Filament stock from a second published CSV (material, color, stock_g, reorder_at_g) into inventory.json,
    summed per material across colours. Offline: the last cached copy. Returns printable lines."""
    directory = directory or config_dir()
    settings = _json(directory / "settings.json")
    url = url or settings.get("inventory_csv_url")
    if not url:
        return []
    cache, messages = directory / "inventory_cache.csv", []
    try:
        text = download(url)
    except (OSError, HTTPException) as exc:
        if not cache.exists():
            return [f"Inventory download failed and no cached copy exists: {exc}"]
        text = cache.read_text(encoding="utf-8")
        messages.append(f"Inventory download failed ({exc}); using cached copy")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    reader.fieldnames = [h.strip().lower() for h in (reader.fieldnames or [])]
    if not {"material", "stock_g"}.issubset(reader.fieldnames):
        raise ValueError("Inventory CSV needs at least the columns material and stock_g")
    stock = {}
    for row in reader:
        name = (row.get("material") or "").strip()
        if not name or not (row.get("stock_g") or "").strip():
            continue
        entry = stock.setdefault(name, {"stock_g": 0.0, "reorder_at_g": 0.0})
        entry["stock_g"] += _number(row["stock_g"])
        if (row.get("reorder_at_g") or "").strip():
            entry["reorder_at_g"] += _number(row["reorder_at_g"])
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "inventory.json", stock)
    settings["inventory_csv_url"] = url
    _write_json(directory / "settings.json", settings)
    cache.write_text(text, encoding="utf-8")
    return messages + [f"{n}: {v['stock_g']:.0f} g in stock" for n, v in sorted(stock.items())]
