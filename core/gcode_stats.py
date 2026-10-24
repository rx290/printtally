"""
What a sliced G-code file says about its job: grams (or metres) of filament per extruder, the material
names, and the print time. Understands Cura (";TIME:", ";Filament used: 1.2m, 0.4m") and
OrcaSlicer / PrusaSlicer / Bambu Studio ("; filament used [g] = ...", "; filament_type = PETG;PLA",
"; estimated printing time (normal mode) = 3h 12m"). Standard library only.
"""
from dataclasses import dataclass, field
import re


@dataclass
class GcodeStats:
    slicer: str
    hours: float
    grams: list = field(default_factory=list)       # per extruder, when the slicer reports grams
    metres: list = field(default_factory=list)      # per extruder, when it reports length only (Cura)
    materials: list = field(default_factory=list)   # per extruder names, when reported
    layer_height: float = 0.0                       # mm, when the G-code says so
    line_width: float = 0.0                         # mm


def _duration_hours(text: str) -> float:
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", text):
        total += float(value) * {"d": 24, "h": 1, "m": 1 / 60, "s": 1 / 3600}[unit]
    return total


def _numbers(text: str) -> list:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def _print_factors(text: str) -> tuple:
    """(layer height, line width) in mm from the slicer's own comments or its embedded settings; 0 if absent."""
    lh = re.search(r"^;\s*layer_height\s*=\s*(\d+(?:\.\d+)?)|^;Layer height:\s*(\d+(?:\.\d+)?)", text, re.M)
    lw = re.search(r"^;\s*(?:line_width|outer_wall_line_width|perimeter_extrusion_width)\s*=\s*(\d+(?:\.\d+)?)"
                   r"|(?:^|[\s;]|\\n)line_width\s*=\s*(\d+(?:\.\d+)?)", text, re.M)
    first = lambda m: float(next(g for g in m.groups() if g)) if m else 0.0
    return first(lh), first(lw)


def parse(gcode_text: str) -> GcodeStats:
    stats = _parse(gcode_text)
    stats.layer_height, stats.line_width = _print_factors(gcode_text[:20000] + "\n" + gcode_text[-60000:])
    return stats


def _parse(gcode_text: str) -> GcodeStats:
    head_tail = gcode_text[:20000] + "\n" + gcode_text[-60000:]
    m = re.search(r"^;\s*filament used \[g\]\s*=\s*(.+)$", head_tail, re.M)
    if m:
        grams = _numbers(m.group(1))
        t = re.search(r"^;\s*estimated printing time \(normal mode\)\s*=\s*(.+)$", head_tail, re.M)
        types = re.search(r"^;\s*filament_type\s*=\s*(.+)$", head_tail, re.M)
        mats = [x.strip() for x in re.split(r"[;,]", types.group(1))] if types else []
        return GcodeStats("orca/prusa", _duration_hours(t.group(1)) if t else 0.0, grams, [], mats[:len(grams)])
    t = re.search(r"^;TIME:(\d+)", head_tail, re.M)
    f = re.search(r"^;Filament used:\s*(.+)$", head_tail, re.M)
    if t or f:
        metres = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)m", f.group(1))] if f else []
        mats = re.findall(r"^;EXTRUDER_TRAIN\.\d+\.MATERIAL\.(?:NAME|TYPE):(.+)$", head_tail, re.M)
        return GcodeStats("cura", int(t.group(1)) / 3600.0 if t else 0.0, [], metres, [x.strip() for x in mats])
    raise ValueError("Could not find the slicer's filament and time comments in this G-code "
                     "(Cura ';TIME:' / ';Filament used:', or Orca/Prusa '; filament used [g] =').")


def grams_by_material(stats: GcodeStats, materials: dict, default_material: str = "",
                      diameter_mm: float = 1.75) -> dict:
    """{material name as the slicer called it (or ''): grams}. Cura metres become grams through each
    material's density (the preference file's material when the G-code does not name it)."""
    from .shop import match_material, grams_from_length, Material
    out = {}
    if stats.grams:
        for i, g in enumerate(stats.grams):
            if g <= 0:
                continue
            name = stats.materials[i] if i < len(stats.materials) else default_material
            out[name] = out.get(name, 0.0) + g
        return out
    for i, length in enumerate(stats.metres):
        if length <= 0:
            continue
        name = stats.materials[i] if i < len(stats.materials) else default_material
        m = match_material(name, materials) or match_material(default_material, materials)
        # Unknown material: weigh it as PLA (1.24 g/cm3) so the grams still show; pricing then reports the
        # unknown name instead of guessing its price.
        grams = grams_from_length(length, m if m is not None else Material("?"), diameter_mm)
        out[name] = out.get(name, 0.0) + grams
    return out
