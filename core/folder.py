"""
Price a whole folder of print files (STL packs, sliced G-code) and write a CSV.

Every folder that directly holds printable files is one product; its sub-folders are their own products
(variants such as hex6/ or snap_parts/ are alternatives, not extra parts). Folders named reference, docs and
the like, and files with DO_NOT_PRINT in the name, are skipped.

Per file the best source wins: G-code with the same name (real grams and time from the slicer), else the STL
or 3MF mesh (grams estimated from its volume and surface: a shell plus sparse infill, time from grams per hour).
Estimated grams are marked in the CSV. The model's printtally.json files apply as usual, plus:
  "copies":   {"egg_leg_L.stl": 2}   print a file more than once per product
  "skip":     ["test_coupon.stl"]    leave files out
  "product":  "Egg tray (2 levels)"  the product name in the CSV (default: the folder name)
  "estimate": {"shell_mm": 1.2, "infill": 0.15, "grams_per_hour": 15}   how STL grams and hours are guessed
"""
import csv
import math
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import gcode_stats, market, shop

PRINTABLE = (".gcode", ".stl", ".3mf")
SKIP_DIRS = {"reference", "references", "ref", "refs", "docs", "doc", "renders", "images", "__pycache__"}
ESTIMATE_DEFAULTS = {"shell_mm": 1.2, "infill": 0.15, "grams_per_hour": 15.0}
CSV_FIELDS = ["Product", "Folder", "Files", "Source", "Material", "Grams", "Print hours", "Unit cost",
              "Parts cost / unit", "Price", "Remas price", "Licence", "Stage", "Profile", "Printer",
              "Estimate", "Warnings"]


# ---------------------------------------------------------------------- meshes
def _triangle_stats(coords) -> dict:
    """coords: flat x1,y1,z1,x2,...,z3 per triangle (mm). Volume (divergence theorem), area, bounding box."""
    volume = area = 0.0
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    n = len(coords) // 9
    for i in range(0, n * 9, 9):
        x1, y1, z1, x2, y2, z2, x3, y3, z3 = coords[i:i + 9]
        ax, ay, az = x2 - x1, y2 - y1, z2 - z1
        bx, by, bz = x3 - x1, y3 - y1, z3 - z1
        cx, cy, cz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        volume += (x1 + x2 + x3) * cx
        for k, (a, b, c) in enumerate(((x1, x2, x3), (y1, y2, y3), (z1, z2, z3))):
            lo[k] = min(lo[k], a, b, c)
            hi[k] = max(hi[k], a, b, c)
    if n == 0:
        raise ValueError("mesh has no triangles")
    return {"volume_cm3": abs(volume) / 6000.0, "area_cm2": area / 100.0,
            "size_mm": [round(hi[k] - lo[k], 2) for k in range(3)], "triangles": n}


def stl_stats(path) -> dict:
    """Volume (cm3), surface area (cm2), bounding box (mm) and triangle count of a binary or ASCII STL."""
    data = Path(path).read_bytes()
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack_from("<I", data, 80)[0]:
        coords = []
        for tri in struct.iter_unpack("<12fH", data[84:]):
            coords.extend(tri[3:12])
    else:
        coords = []
        for line in data.decode("ascii", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "vertex":
                coords.extend(float(v) for v in parts[1:4])
        if len(coords) % 9:
            raise ValueError(f"{Path(path).name}: incomplete triangles")
    return _triangle_stats(coords)


def threemf_stats(path) -> dict:
    """Same as stl_stats for every mesh inside a 3MF (object transforms are ignored)."""
    coords = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".model"):
                continue
            root = ET.fromstring(z.read(name))
            for mesh in root.iter():
                if not mesh.tag.endswith("}mesh") and mesh.tag != "mesh":
                    continue
                verts = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
                         for v in mesh.iter() if v.tag.endswith("vertex")]
                for t in mesh.iter():
                    if t.tag.endswith("triangle"):
                        for key in ("v1", "v2", "v3"):
                            coords.extend(verts[int(t.get(key))])
    return _triangle_stats(coords)


def estimate_grams(stats: dict, density_g_cm3: float, shell_mm: float = 1.0, infill: float = 0.15) -> float:
    """A solid shell of shell_mm under the whole surface (never more than the part itself), the rest at
    `infill`. Thin parts come out solid, chunky parts mostly infill."""
    volume = stats["volume_cm3"]
    shell = min(volume, stats["area_cm2"] * shell_mm / 10.0)
    return (shell + (volume - shell) * infill) * density_g_cm3


# --------------------------------------------------------------------- folders
def _skipped(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith(".") or name in SKIP_DIRS


def _printables(folder: Path) -> list:
    return sorted(f for f in folder.iterdir()
                  if f.is_file() and f.suffix.lower() in PRINTABLE and "do_not_print" not in f.name.lower())


def find_products(paths) -> list:
    """[(target, label)]: every folder under `paths` that directly holds printable files (a file given directly
    is its own product). The label is the folder's path from the one given, e.g. 11-wall-jali-tile/hex6."""
    out = []

    def walk(folder: Path, label: str):
        if _skipped(folder):
            return
        if _printables(folder):
            out.append((folder, label))
        for d in sorted(x for x in folder.iterdir() if x.is_dir()):
            walk(d, f"{label}/{d.name}" if label else d.name)

    for p in map(Path, paths):
        p = p.expanduser()
        if p.is_file() and p.suffix.lower() in PRINTABLE:
            out.append((p, p.stem))
        elif p.is_dir():
            if _printables(p):
                out.append((p, p.name))
            for d in sorted(x for x in p.iterdir() if x.is_dir()):
                walk(d, f"{p.name}/{d.name}" if _printables(p) else d.name)
    return out


def _sources(files: list) -> list:
    """One source per model name: G-code wins over STL, STL over 3MF."""
    rank = {".gcode": 0, ".stl": 1, ".3mf": 2}
    best = {}
    for f in files:
        key = f.stem.lower()
        if key not in best or rank[f.suffix.lower()] < rank[best[key].suffix.lower()]:
            best[key] = f
    return sorted(best.values())


# --------------------------------------------------------------------- pricing
def price_product(target: Path, label: str = "", profile=None, printer=None, heads=None, material=None,
                  night_share=None, directory=None, materials=None, parts=None) -> dict:
    """One CSV row for a product folder (or a single file)."""
    files = [target] if target.is_file() else _printables(target)
    prefs, _used = shop.find_preferences((target if target.is_file() else target / "_product_"))
    if material:
        prefs["material"] = material
    if night_share is not None:
        prefs["night_share"] = night_share
    settings = shop.build_settings(directory, profile, printer, heads, prefs)
    materials = materials if materials is not None else shop.load_materials(directory)
    parts = parts if parts is not None else shop.load_parts(directory)
    est = {**ESTIMATE_DEFAULTS, **(settings.get("estimate") or {}), **(prefs.get("estimate") or {})}
    copies = {str(k).lower(): float(v) for k, v in (prefs.get("copies") or {}).items()}
    skip = {str(s).lower() for s in prefs.get("skip") or []}
    chosen = shop.parse_choice(prefs.get("material", ""))[0]
    stand_in = (shop.match_material(chosen, materials)
                or shop.match_material(str(settings.get("fallback_material", "")), materials))
    density = stand_in.density_g_cm3 if stand_in else 1.24
    grams, hours, used, kinds, problems = {}, 0.0, [], set(), shop.check_preferences(prefs)
    for f in _sources([f for f in files if f.name.lower() not in skip]):
        n = copies.get(f.name.lower(), copies.get(f.stem.lower(), 1.0))
        try:
            if f.suffix.lower() == ".gcode":
                stats = gcode_stats.parse(f.read_text(errors="ignore"))
                for name, g in gcode_stats.grams_by_material(stats, materials, chosen,
                                                             float(settings.get("filament_diameter_mm", 1.75))).items():
                    grams[name] = grams.get(name, 0.0) + g * n
                hours += stats.hours * n
                kinds.add("gcode")
            else:
                mesh = stl_stats(f) if f.suffix.lower() == ".stl" else threemf_stats(f)
                g = estimate_grams(mesh, density, float(est["shell_mm"]), float(est["infill"])) * n
                grams[chosen] = grams.get(chosen, 0.0) + g
                hours += g / max(float(est["grams_per_hour"]), 0.1)
                kinds.add("mesh")
        except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, struct.error) as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        used.append(f.name + (f" x{n:g}" if n != 1 else ""))
    job = shop.price_job(grams, hours, prefs, settings, materials, parts)
    b = job.breakdown
    cost = sum(v for _, v in job.parts[:-1]) if job.parts else b.cost_to_make
    price = ""
    if job.sellable:
        if job.market is not None:
            price = market.suggest(job.mine, job.market, cost, job.suggest_mode, job.cost_multiplier,
                                   job.rounding_step, job.minimum_price, job.market_cap,
                                   job.cost_floor)[0]
        else:
            price = b.final_price
    if price != "" and price < cost:
        problems.append(f"Price {price:,.0f} is below this print's cost {cost:,.0f} (slow print or cheap profile).")
    estimate = []
    if "mesh" in kinds:
        estimate.append("grams and hours from the mesh")
    if job.estimated:
        estimate.append("fallback material price")
    return {
        "Product": prefs.get("product") or label or target.name,
        "Folder": str(target),
        "Files": "; ".join(used),
        "Source": " + ".join(sorted(kinds)) or "none",
        "Material": ", ".join(sorted({m for m, _, _ in job.material_lines})),
        "Grams": round(sum(g for _, g, _ in job.material_lines), 1),
        "Print hours": round(hours, 2),
        "Unit cost": round(cost),
        "Parts cost / unit": round(b.hardware_cost),
        "Price": round(price) if price != "" else "",
        "Remas price": round(job.market.customer) if job.market is not None and job.market.available else "",
        "Licence": job.licence + ("" if job.sellable else " (DO NOT SELL)"),
        "Stage": job.stage,
        "Profile": job.profile,
        "Printer": job.factors.get("printer", ""),
        "Estimate": "; ".join(estimate),
        "Warnings": " | ".join(problems + job.warnings),
    }


def price_folders(paths, out=None, **kwargs) -> list:
    """Rows for every product under `paths`; written to `out` as CSV when given."""
    targets = find_products(paths)
    materials = shop.load_materials(kwargs.get("directory"))
    parts = shop.load_parts(kwargs.get("directory"))
    rows = [price_product(t, label, materials=materials, parts=parts, **kwargs) for t, label in targets]
    if out and rows:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return rows
