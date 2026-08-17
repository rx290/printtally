# Copyright (c) 2026, PrintTally -- MIT licensed, see the repo's LICENSE file.
"""
PrintTally Cost Calculator -- a Cura post-processing script.

Install: copy this file into your Cura scripts folder (Help -> Show
Configuration Folder -> scripts/), restart Cura, then enable it from
Extensions -> Post Processing -> Modify G-Code -> Add a script.

This is a deliberately self-contained, single-file script (the same
convention every real Cura post-processing script uses -- see any script in
Cura's own bundled PostProcessingPlugin/scripts/ folder). The cost formula
below is a duplicate of printtally/core/cost_model.py's logic, kept
byte-for-byte behaviorally identical and verified so by
tests/test_cura_script_matches_core.py in the PrintTally repo -- change one,
change both, and re-run that test.

Weight comes from Cura's real print-information API
(Application.getInstance().getPrintInformation().materialWeights, confirmed
against Cura 5.13's own bundled DisplayInfoOnLCD.py script), summed across
every extruder actually used -- not a single-extruder assumption. Print time
comes from the ";TIME:<seconds>" line Cura writes into the first G-code
section (also confirmed against that same bundled script), not from a
slicer-agnostic estimate.
"""
from ..Script import Script
from UM.Application import Application


class PrintTallyCostCalculator(Script):
    def getSettingDataString(self):
        return """{
            "name": "PrintTally Cost Calculator",
            "key": "PrintTallyCostCalculator",
            "metadata": {},
            "version": 2,
            "settings":
            {
                "price_per_kg":
                {
                    "label": "Filament price per kg",
                    "description": "What you paid for this filament, per kilogram.",
                    "type": "float",
                    "default_value": 20.0,
                    "minimum_value": 0
                },
                "machine_rate_per_hour":
                {
                    "label": "Machine cost per hour",
                    "description": "Hourly rate covering depreciation, replacement parts, and wear and tear. A typical range is 20-60 (in your currency) per hour.",
                    "type": "float",
                    "default_value": 40.0,
                    "minimum_value": 0
                },
                "printer_watts":
                {
                    "label": "Printer power draw (W)",
                    "description": "Average power usage of the printer in watts.",
                    "type": "float",
                    "default_value": 250.0,
                    "minimum_value": 0
                },
                "electricity_rate_per_kwh":
                {
                    "label": "Electricity rate per kWh",
                    "description": "Your real electricity cost per kilowatt-hour.",
                    "type": "float",
                    "default_value": 0.0,
                    "minimum_value": 0
                },
                "waste_pct":
                {
                    "label": "Waste/maintenance %",
                    "description": "Consumables, cleaning supplies, build plate/nozzle/glue, and wastage from failed prints -- applied as a percentage of material+machine+electricity cost. Typical range 5-10%.",
                    "type": "float",
                    "unit": "%",
                    "default_value": 7.0,
                    "minimum_value": 0,
                    "maximum_value": 100
                },
                "post_processing_hours":
                {
                    "label": "Post-processing time (hours)",
                    "description": "Time spent removing supports, sanding, finishing, and quality-checking this print.",
                    "type": "float",
                    "default_value": 0.0,
                    "minimum_value": 0
                },
                "post_processing_rate_per_hour":
                {
                    "label": "Post-processing labor rate",
                    "description": "Your hourly rate for post-processing time.",
                    "type": "float",
                    "default_value": 0.0,
                    "minimum_value": 0
                },
                "packaging_cost":
                {
                    "label": "Packaging cost",
                    "description": "Cartons, tape, bubble wrap, labels -- a fixed cost per order.",
                    "type": "float",
                    "default_value": 0.0,
                    "minimum_value": 0
                },
                "shipping_cost":
                {
                    "label": "Shipping cost",
                    "description": "A fixed shipping cost per order.",
                    "type": "float",
                    "default_value": 0.0,
                    "minimum_value": 0
                },
                "marketing_fee_pct":
                {
                    "label": "Marketplace/referral fee %",
                    "description": "A percentage fee charged by a marketplace or referral channel, if selling through one.",
                    "type": "float",
                    "unit": "%",
                    "default_value": 0.0,
                    "minimum_value": 0,
                    "maximum_value": 100
                },
                "profit_margin_pct":
                {
                    "label": "Profit margin %",
                    "description": "Covers design time, quality checking, and running a real business. Typical range 20-30%.",
                    "type": "float",
                    "unit": "%",
                    "default_value": 25.0,
                    "minimum_value": 0
                },
                "currency":
                {
                    "label": "Currency symbol",
                    "description": "Prefix for the printed breakdown, e.g. $ or Rs.",
                    "type": "str",
                    "default_value": ""
                }
            }
        }"""

    def execute(self, data):
        material_weight_g = sum(
            Application.getInstance().getPrintInformation().materialWeights
        )

        time_total_seconds = None
        for line in data[0].split("\n"):
            if line.startswith(";TIME:"):
                time_total_seconds = int(line.split(":")[1])
                break
        if time_total_seconds is None:
            # No estimate available (e.g. slicing was skipped) -- fail
            # loudly rather than silently price this as a zero-hour print.
            raise ValueError(
                "PrintTallyCostCalculator: no ';TIME:' line found in the "
                "G-code. Make sure the print has actually been sliced."
            )
        print_hours = time_total_seconds / 3600.0

        breakdown = self._calculate_cost(
            model_weight_g=material_weight_g,
            price_per_kg=self.getSettingValueByKey("price_per_kg"),
            print_hours=print_hours,
            machine_rate_per_hour=self.getSettingValueByKey("machine_rate_per_hour"),
            printer_watts=self.getSettingValueByKey("printer_watts"),
            electricity_rate_per_kwh=self.getSettingValueByKey("electricity_rate_per_kwh"),
            waste_pct=self.getSettingValueByKey("waste_pct") / 100.0,
            post_processing_hours=self.getSettingValueByKey("post_processing_hours"),
            post_processing_rate_per_hour=self.getSettingValueByKey("post_processing_rate_per_hour"),
            packaging_cost=self.getSettingValueByKey("packaging_cost"),
            shipping_cost=self.getSettingValueByKey("shipping_cost"),
            marketing_fee_pct=self.getSettingValueByKey("marketing_fee_pct") / 100.0,
            profit_margin_pct=self.getSettingValueByKey("profit_margin_pct") / 100.0,
        )

        currency = self.getSettingValueByKey("currency") or ""
        report_lines = [
            ";PrintTally cost breakdown",
            f";  Material cost:      {currency}{breakdown['material_cost']:.2f}",
            f";  Machine cost:       {currency}{breakdown['machine_cost']:.2f}",
            f";  Electricity cost:   {currency}{breakdown['electricity_cost']:.2f}",
            f";  Waste/maintenance:  {currency}{breakdown['waste_maintenance_cost']:.2f}",
            f";  Post-processing:    {currency}{breakdown['post_processing_cost']:.2f}",
            f";  Selling costs:      {currency}{breakdown['selling_cost']:.2f}",
            f";  Subtotal:           {currency}{breakdown['subtotal']:.2f}",
            f";  Profit margin:      {currency}{breakdown['profit_margin']:.2f}",
            f";  FINAL PRICE:        {currency}{breakdown['final_price']:.2f}",
        ]
        data[0] += "\n" + "\n".join(report_lines) + "\n"
        return data

    @staticmethod
    def _calculate_cost(
        model_weight_g, price_per_kg, print_hours, machine_rate_per_hour,
        printer_watts, electricity_rate_per_kwh, waste_pct,
        post_processing_hours, post_processing_rate_per_hour,
        packaging_cost, shipping_cost, marketing_fee_pct, profit_margin_pct,
    ):
        """
        Byte-for-byte the same formula as printtally/core/cost_model.py's
        calculate_cost() -- duplicated here (not imported) because Cura
        post-processing scripts are distributed as single self-contained
        files. Kept in sync and verified identical by
        tests/test_cura_script_matches_core.py in the PrintTally repo.
        """
        if model_weight_g < 0 or price_per_kg < 0 or print_hours < 0:
            raise ValueError("weight, price, and hours cannot be negative")
        if not (0 <= waste_pct <= 1) or not (0 <= marketing_fee_pct <= 1):
            raise ValueError("waste_pct and marketing_fee_pct must be between 0 and 1")
        if profit_margin_pct < 0:
            raise ValueError("profit_margin_pct cannot be negative")

        material_cost = model_weight_g * (price_per_kg / 1000.0)
        machine_cost = machine_rate_per_hour * print_hours
        electricity_cost = ((printer_watts * print_hours) / 1000.0) * electricity_rate_per_kwh
        post_processing_cost = post_processing_rate_per_hour * post_processing_hours

        production_subtotal = material_cost + machine_cost + electricity_cost
        waste_maintenance_cost = waste_pct * production_subtotal

        selling_cost_fixed = packaging_cost + shipping_cost
        pre_marketing_subtotal = (
            production_subtotal + waste_maintenance_cost
            + post_processing_cost + selling_cost_fixed
        )
        marketing_fee = marketing_fee_pct * pre_marketing_subtotal
        selling_cost = selling_cost_fixed + marketing_fee

        subtotal = pre_marketing_subtotal + marketing_fee
        profit_margin = profit_margin_pct * subtotal
        final_price = subtotal + profit_margin

        return {
            "material_cost": round(material_cost, 2),
            "machine_cost": round(machine_cost, 2),
            "electricity_cost": round(electricity_cost, 2),
            "waste_maintenance_cost": round(waste_maintenance_cost, 2),
            "post_processing_cost": round(post_processing_cost, 2),
            "selling_cost": round(selling_cost, 2),
            "subtotal": round(subtotal, 2),
            "profit_margin": round(profit_margin, 2),
            "final_price": round(final_price, 2),
        }
