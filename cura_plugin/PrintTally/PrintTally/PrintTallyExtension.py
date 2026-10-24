# Copyright (c) 2026, PrintTally -- MIT licensed, see the repo's LICENSE file.
import json
import os.path

try:
    from cura.ApplicationMetadata import CuraSDKVersion
except ImportError:  # Cura <= 3.6
    CuraSDKVersion = "6.0.0"
if CuraSDKVersion >= "8.0.0":
    from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty
else:
    from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty

from cura.CuraApplication import CuraApplication
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.i18n import i18nCatalog

from .cost_model import CostInputs, calculate_cost, format_breakdown
from . import shop
from . import market as market_mod

catalog = i18nCatalog("cura")

SETTINGS_PREFERENCE = "printtally/settings"

# The video this formula is sourced from states ranges (20-60/hr machine
# rate, 5-10% waste, 20-30% margin) -- these defaults sit inside those
# ranges. Everything else genuinely varies by shop/region, so it defaults to
# zero rather than guessing a number that would look official but isn't.
DEFAULT_SETTINGS = {
    "price_per_kg": 2500.0,
    "profile": "",                    # "" = your default profile (settings.json), else service / product
    "printer": "",                    # "" = the profile's or your default printer
    "filament_profile": "",           # "" = from Cura's material names; else a materials.json profile
    "machine_rate_per_hour": 40.0,
    "printer_watts": 250.0,
    "electricity_rate_per_kwh": 90.0,
    "waste_pct": 0.07,
    "post_processing_rate_per_hour": 0.0,
    "post_processing_hours": 0.0,
    "include_design_cost": False,
    "design_hours": 0.0,
    "design_rate_per_hour": 0.0,
    "packaging_cost": 0.0,
    "shipping_cost": 0.0,
    "marketing_fee_pct": 0.0,
    "profit_margin_pct": 0.25,
}


class PrintTallyExtension(QObject, Extension):
    def __init__(self, app: CuraApplication) -> None:
        super().__init__()
        self._app = app
        self._breakdown = None
        self._job = None
        self._settings_dialog = None

        self._preferences = self._app.getPreferences()
        self._preferences.addPreference(SETTINGS_PREFERENCE, json.dumps(DEFAULT_SETTINGS))

        self.setMenuName(catalog.i18nc("@item:inmenu", "PrintTally"))
        self.addMenuItem(catalog.i18nc("@item:inmenu", "Settings..."), self.showSettingsDialog)

        self._app.mainWindowChanged.connect(self._connectToPrintInformation)

    # ---------------------------------------------------------------- data

    def _loadSettings(self) -> dict:
        raw = self._preferences.getValue(SETTINGS_PREFERENCE)
        settings = dict(DEFAULT_SETTINGS)
        if raw:
            try:
                settings.update(json.loads(raw))
            except (TypeError, ValueError):
                Logger.log("w", "PrintTally: stored settings were not valid JSON, using defaults")
        return settings

    def _recalculate(self) -> None:
        print_information = self._app.getPrintInformation()
        settings = self._loadSettings()

        weight_g = 0.0
        print_hours = 0.0
        has_estimate = False
        if print_information is not None:
            weights = print_information.materialWeights
            if weights:
                weight_g = sum(weights)
            current_print_time = print_information.currentPrintTime
            if current_print_time is not None and current_print_time.valid and not current_print_time.isTotalDurationZero:
                print_hours = int(current_print_time) / 3600.0
                has_estimate = weight_g > 0.0

        self._print_hours = print_hours
        self._job = None
        if has_estimate:
            try:
                names = list(getattr(print_information, "materialNames", []) or [])
                try:   # real print factors from the slicer's own settings
                    stack = self._app.getGlobalContainerStack()
                    settings = dict(settings, layer_height=float(stack.getProperty("layer_height", "value") or 0),
                                    line_width=float(stack.getProperty("line_width", "value") or 0))
                except Exception:  # noqa: BLE001
                    pass
                self._job = shop.price_from_slicer(list(print_information.materialWeights), names, print_hours,
                                                   self._modelPath(), settings)
                self._breakdown = self._job.breakdown
            except ValueError as e:
                Logger.log("w", f"PrintTally: invalid settings, can't price this print: {e}")
                self._breakdown = None
        else:
            self._breakdown = None

        self.hasEstimateChanged.emit()
        self.finalPriceChanged.emit()
        self.breakdownTextChanged.emit()

    def _modelPath(self):
        """The file of the first model on the build plate: its printtally.json preferences apply."""
        try:
            from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
            for node in DepthFirstIterator(self._app.getController().getScene().getRoot()):
                mesh = node.getMeshData() if hasattr(node, "getMeshData") else None
                if mesh is not None and node.callDecoration("isSliceable") and mesh.getFileName():
                    return mesh.getFileName()
        except Exception as e:  # noqa: BLE001 - a missing path only means no preference files
            Logger.log("w", f"PrintTally: could not read the model's file name: {e}")
        return None

    # ------------------------------------------------------- live widget

    def _connectToPrintInformation(self) -> None:
        qml_path = os.path.join(
            PluginRegistry.getInstance().getPluginPath(self.getPluginId()), "PrintTallyWidget.qml"
        )
        component = self._app.createQmlComponent(qml_path, {"manager": self})
        if component is None:
            Logger.log("e", "PrintTally: failed to create PrintTallyWidget.qml")
            return
        self._app.addAdditionalComponent("saveButton", component)

        print_information = self._app.getPrintInformation()
        print_information.currentPrintTimeChanged.connect(self._recalculate)
        print_information.materialWeightsChanged.connect(self._recalculate)
        self._recalculate()

    hasEstimateChanged = pyqtSignal()

    @pyqtProperty(bool, notify=hasEstimateChanged)
    def hasEstimate(self) -> bool:
        return self._breakdown is not None

    finalPriceChanged = pyqtSignal()

    @pyqtProperty(float, notify=finalPriceChanged)
    def finalPrice(self) -> float:
        return self._breakdown.final_price if self._breakdown else 0.0

    breakdownTextChanged = pyqtSignal()

    @pyqtProperty(str, notify=breakdownTextChanged)
    def breakdownText(self) -> str:
        if not self._breakdown:
            return catalog.i18nc("@info", "Slice a model to see a price here.")
        currency = self._app.getPreferences().getValue("cura/currency") or ""
        if getattr(self, "_job", None) is not None:
            if not self._job.currency:
                self._job.currency = currency
            return shop.format_job(self._job) + "\n\n" + format_breakdown(self._breakdown, currency)
        return format_breakdown(self._breakdown, currency)

    @pyqtProperty(str, notify=breakdownTextChanged)
    def displayJson(self) -> str:
        job = getattr(self, "_job", None)
        if not job:
            return json.dumps({"rows": [], "warnings": [], "badge": "", "sellable": True, "total": ""})
        b = job.breakdown
        currency = job.currency or self._app.getPreferences().getValue("cura/currency") or ""
        def money(value):
            return f"{currency}{value:,.2f}"
        rows = [{"label": f"{name} · {grams:,.1f} g", "value": money(cost)}
                for name, grams, cost in job.material_lines]
        rows += [{"label": label, "value": money(value)} for label, value in [
            (f"Machine · {self._print_hours:,.2f} h", b.machine_cost),
            ("Electricity", b.electricity_cost), ("Parts", b.hardware_cost),
            ("Waste / maintenance", b.waste_maintenance_cost),
            ("Labor", b.post_processing_cost), ("Design", b.design_cost),
            ("Selling costs", b.selling_cost), ("Royalty", b.royalty_cost),
            ("Margin", b.profit_margin), ("Cost to make", b.cost_to_make)]]
        if job.sellable and job.market is not None:
            cost = sum(v for _, v in job.parts[:-1]) if job.parts else b.cost_to_make
            if job.mine is not None and job.mine.available:
                rows += [{"label": label, "value": money(v)} for label, v in job.parts]
                rows.append({"label": f"Your price ({job.mine.tier})", "value": money(job.mine.customer)})
                if job.mine.reseller is not None:
                    rows.append({"label": "Your reseller price", "value": money(job.mine.reseller)})
            if job.market.available:
                rows.append({"label": "Market (Remas) customer", "value": money(job.market.customer)})
                if job.market.reseller is not None:
                    rows.append({"label": "Market (Remas) reseller", "value": money(job.market.reseller)})
            suggested, how = market_mod.suggest(job.mine, job.market, cost, job.suggest_mode, job.cost_multiplier,
                                                job.rounding_step, job.minimum_price, job.market_cap)
            rows.append({"label": f"Suggested price ({how})", "value": money(suggested)})
            extra = (job.mine.messages if job.mine else []) + job.market.messages
            if job.mine is not None and job.mine.cap_state == "below_cost":
                extra = list(extra) + [market_mod.BELOW_COST_WARNING]
            job.warnings = list(job.warnings) + [m for m in extra if m not in job.warnings]
        return json.dumps({"rows": rows, "warnings": job.warnings,
                           "badge": f"{job.stage} · {job.licence}" + (" · DO NOT SELL" if not job.sellable else ""),
                           "sellable": job.sellable, "total": money(b.final_price)})

    # ----------------------------------------------------- settings dialog

    def showSettingsDialog(self) -> None:
        if self._settings_dialog is None:
            qml_path = os.path.join(
                PluginRegistry.getInstance().getPluginPath(self.getPluginId()),
                "PrintTallySettingsDialog.qml",
            )
            self._settings_dialog = self._app.createQmlComponent(qml_path, {"manager": self})
            if self._settings_dialog is None:
                Logger.log("e", "PrintTally: failed to create PrintTallySettingsDialog.qml")
                return
        self._settings_dialog.show()

    settingsChanged = pyqtSignal()

    @pyqtProperty(str, notify=settingsChanged)
    def settingsJson(self) -> str:
        return json.dumps(self._loadSettings())

    @pyqtProperty(str, notify=settingsChanged)
    def filamentProfilesJson(self) -> str:
        """["Auto (from Cura)", profile names from materials.json...] for the settings dropdown."""
        try:
            names = []
            for m in sorted(shop.load_materials().values(), key=lambda x: x.name):
                names.append(m.name)
                names += [f"{m.name}:{b}" for b in m.batches] if len(m.batches) > 1 else []
        except Exception:
            names = []
        return json.dumps(["Auto (from Cura)"] + names)

    @pyqtProperty(str, notify=settingsChanged)
    def profileChoicesJson(self) -> str:
        """["(default)", profile names...] for the profile dropdown."""
        try:
            from . import profiles as profiles_mod
            return json.dumps(["(default)"] + profiles_mod.list_profiles())
        except Exception:
            return json.dumps(["(default)"])

    @pyqtProperty(str, notify=settingsChanged)
    def printerChoicesJson(self) -> str:
        try:
            from . import profiles as profiles_mod
            return json.dumps(["(default)"] + profiles_mod.list_printers())
        except Exception:
            return json.dumps(["(default)"])

    @pyqtSlot(str)
    def saveSettingsJson(self, settings_json: str) -> None:
        try:
            new_settings = json.loads(settings_json)
        except (TypeError, ValueError):
            Logger.log("e", "PrintTally: refused to save settings, payload was not valid JSON")
            return
        settings = dict(DEFAULT_SETTINGS)
        settings.update(new_settings)
        self._preferences.setValue(SETTINGS_PREFERENCE, json.dumps(settings))
        self.settingsChanged.emit()
        self._recalculate()
