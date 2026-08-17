import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "orca_prusa_script" / "printtally_postprocess.py"

# This footer text is copied verbatim in shape from OrcaSlicer's own source
# (src/libslic3r/GCode.cpp's out_filament_used_g/out_filament_cost pairs, and
# GCodeProcessor.cpp's "; estimated printing time (%s mode) = %s" line) --
# not guessed, confirmed against the real strings before writing this test.
REAL_FOOTER_SHAPE = """G1 X10 Y10 Z0.2 F1500
; some print moves would be here
; filament used [mm] = 1234.56
; filament used [cm3] = 12.34
; filament used [g] = 45.23
; filament cost = 1.13
; total filament used [g] = 45.23
; total filament cost = 1.13
; estimated printing time (normal mode) = 3h 12m 45s
"""


def _run_script(gcode_path: Path, extra_args=None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(SCRIPT), "--price-per-kg", "2500",
            "--machine-rate", "40", "--watts", "250", "--electricity-rate", "12",
            "--waste-pct", "0.07", "--margin-pct", "0.25"]
    if extra_args:
        args.extend(extra_args)
    args.append(str(gcode_path))
    return subprocess.run(args, capture_output=True, text=True)


def test_parses_real_footer_shape_and_appends_breakdown(tmp_path):
    gcode_file = tmp_path / "test.gcode"
    gcode_file.write_text(REAL_FOOTER_SHAPE)

    result = _run_script(gcode_file)

    assert result.returncode == 0, result.stderr
    assert "FINAL PRICE" in result.stdout

    updated_text = gcode_file.read_text()
    assert "PrintTally cost breakdown" in updated_text
    assert "Material cost:" in updated_text
    # weight=45.23g, price_per_kg=2500 -> 45.23 * 2.5 = 113.075
    assert "113.08" in updated_text or "113.07" in updated_text


def test_print_hours_parsed_correctly_from_hms_format(tmp_path):
    gcode_file = tmp_path / "test.gcode"
    gcode_file.write_text(REAL_FOOTER_SHAPE)

    # 3h 12m 45s -> 3 + 12/60 + 45/3600 hours
    result = _run_script(gcode_file, extra_args=["--machine-rate", "100", "--waste-pct", "0", "--margin-pct", "0", "--price-per-kg", "0", "--electricity-rate", "0"])
    assert result.returncode == 0, result.stderr
    expected_hours = 3 + 12 / 60 + 45 / 3600
    expected_machine_cost = 100.0 * expected_hours
    assert f"{expected_machine_cost:.2f}" in result.stdout


def test_missing_weight_footer_raises_clear_error(tmp_path):
    gcode_file = tmp_path / "broken.gcode"
    gcode_file.write_text("G1 X10 Y10\n; no footer here at all\n")

    result = _run_script(gcode_file)

    assert result.returncode != 0
    assert "total filament used" in result.stderr


def test_missing_time_footer_raises_clear_error(tmp_path):
    gcode_file = tmp_path / "no_time.gcode"
    gcode_file.write_text("; total filament used [g] = 10.00\n")

    result = _run_script(gcode_file)

    assert result.returncode != 0
    assert "estimated printing time" in result.stderr


def test_days_hours_minutes_seconds_all_parse_when_present(tmp_path):
    gcode_file = tmp_path / "long_print.gcode"
    gcode_file.write_text(
        "; total filament used [g] = 100.00\n"
        "; estimated printing time (normal mode) = 1d 2h 30m 15s\n"
    )
    result = _run_script(gcode_file, extra_args=["--machine-rate", "10", "--waste-pct", "0", "--margin-pct", "0", "--price-per-kg", "0", "--electricity-rate", "0"])
    assert result.returncode == 0, result.stderr
    expected_hours = 24 + 2 + 30 / 60 + 15 / 3600
    expected_machine_cost = 10.0 * expected_hours
    assert f"{expected_machine_cost:.2f}" in result.stdout
