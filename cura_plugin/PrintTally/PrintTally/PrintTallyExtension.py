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

catalog = i18nCatalog("cura")

SETTINGS_PREFERENCE = "printtally/settings"

# The video this formula is sourced from states ranges (20-60/hr machine
# rate, 5-10% waste, 20-30% margin) -- these defaults sit inside those
# ranges. Everything else genuinely varies by shop/region, so it defaults to
# zero rather than guessing a number that would look official but isn't.
DEFAULT_SETTINGS = {
    "price_per_kg": 2500.0,
    "machine_rate_per_hour": 40.0,
    "printer_watts": 250.0,
    "electricity_rate_per_kwh": 0.0,
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

        if has_estimate:
            try:
                inputs = CostInputs(
                    model_weight_g=weight_g,
                    print_hours=print_hours,
                    price_per_kg=settings["price_per_kg"],
                    machine_rate_per_hour=settings["machine_rate_per_hour"],
                    printer_watts=settings["printer_watts"],
                    electricity_rate_per_kwh=settings["electricity_rate_per_kwh"],
                    waste_pct=settings["waste_pct"],
                    post_processing_hours=settings["post_processing_hours"],
                    post_processing_rate_per_hour=settings["post_processing_rate_per_hour"],
                    include_design_cost=settings["include_design_cost"],
                    design_hours=settings["design_hours"],
                    design_rate_per_hour=settings["design_rate_per_hour"],
                    packaging_cost=settings["packaging_cost"],
                    shipping_cost=settings["shipping_cost"],
                    marketing_fee_pct=settings["marketing_fee_pct"],
                    profit_margin_pct=settings["profit_margin_pct"],
                )
                self._breakdown = calculate_cost(inputs)
            except ValueError as e:
                Logger.log("w", f"PrintTally: invalid settings, can't price this print: {e}")
                self._breakdown = None
        else:
            self._breakdown = None

        self.hasEstimateChanged.emit()
        self.finalPriceChanged.emit()
        self.breakdownTextChanged.emit()

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
        return format_breakdown(self._breakdown, currency)

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
