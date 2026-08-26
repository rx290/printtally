// Copyright (c) 2026, PrintTally -- MIT licensed, see the repo's LICENSE file.
import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

UM.Dialog
{
    id: base
    title: catalog.i18nc("@title:window", "PrintTally Settings")

    minimumWidth: screenScaleFactor * 460
    minimumHeight: screenScaleFactor * 620
    width: minimumWidth
    height: minimumHeight

    property var catalog: UM.I18nCatalog { name: "cura" }

    property int labelWidth: (base.width - 3 * UM.Theme.getSize("default_margin").width) * 0.55
    property int controlWidth: (base.width - 3 * UM.Theme.getSize("default_margin").width) * 0.4
    property string currency: UM.Preferences.getValue("cura/currency")

    function loadValues()
    {
        var s = JSON.parse(manager.settingsJson)
        pricePerKg.valueText = String(s.price_per_kg)
        machineRate.valueText = String(s.machine_rate_per_hour)
        printerWatts.valueText = String(s.printer_watts)
        electricityRate.valueText = String(s.electricity_rate_per_kwh)
        wastePct.valueText = String(s.waste_pct)
        laborRate.valueText = String(s.post_processing_rate_per_hour)
        laborHours.valueText = String(s.post_processing_hours)
        includeDesignCost.checked = !!s.include_design_cost
        designHours.valueText = String(s.design_hours)
        designRate.valueText = String(s.design_rate_per_hour)
        packagingCost.valueText = String(s.packaging_cost)
        shippingCost.valueText = String(s.shipping_cost)
        marketingFeePct.valueText = String(s.marketing_fee_pct)
        marginPct.valueText = String(s.profit_margin_pct)
    }

    function save(closeDialog)
    {
        var s = {
            price_per_kg: parseFloat(pricePerKg.valueText) || 0,
            machine_rate_per_hour: parseFloat(machineRate.valueText) || 0,
            printer_watts: parseFloat(printerWatts.valueText) || 0,
            electricity_rate_per_kwh: parseFloat(electricityRate.valueText) || 0,
            waste_pct: parseFloat(wastePct.valueText) || 0,
            post_processing_rate_per_hour: parseFloat(laborRate.valueText) || 0,
            post_processing_hours: parseFloat(laborHours.valueText) || 0,
            include_design_cost: includeDesignCost.checked,
            design_hours: parseFloat(designHours.valueText) || 0,
            design_rate_per_hour: parseFloat(designRate.valueText) || 0,
            packaging_cost: parseFloat(packagingCost.valueText) || 0,
            shipping_cost: parseFloat(shippingCost.valueText) || 0,
            marketing_fee_pct: parseFloat(marketingFeePct.valueText) || 0,
            profit_margin_pct: parseFloat(marginPct.valueText) || 0
        }
        manager.saveSettingsJson(JSON.stringify(s))
        if (closeDialog)
        {
            base.accept()
        }
    }

    Component.onCompleted: loadValues()
    onVisibleChanged: if (visible) { loadValues() }

    rightButtons:
    [
        Cura.SecondaryButton
        {
            text: catalog.i18nc("@action:button", "Cancel")
            onClicked: base.reject()
        },
        Cura.PrimaryButton
        {
            text: catalog.i18nc("@action:button", "Save")
            onClicked: save(true)
        }
    ]

    ScrollView
    {
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Column
        {
            width: base.width - 2 * UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label { text: catalog.i18nc("@label", "Material"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: pricePerKg
                width: parent.width
                labelText: catalog.i18nc("@label", "Price per kg")
                unitText: base.currency
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Machine"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: machineRate
                width: parent.width
                labelText: catalog.i18nc("@label", "Hourly machine rate")
                unitText: base.currency + "/h"
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Electricity"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: printerWatts
                width: parent.width
                labelText: catalog.i18nc("@label", "Printer power draw")
                unitText: "W"
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }
            Cura.NumericTextFieldWithUnit
            {
                id: electricityRate
                width: parent.width
                labelText: catalog.i18nc("@label", "Electricity rate")
                unitText: base.currency + "/kWh"
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Maintenance"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: wastePct
                width: parent.width
                labelText: catalog.i18nc("@label", "Waste / maintenance")
                unitText: catalog.i18nc("@label", "fraction, e.g. 0.07 = 7%")
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Labor"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: laborRate
                width: parent.width
                labelText: catalog.i18nc("@label", "Labor rate")
                unitText: base.currency + "/h"
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }
            Cura.NumericTextFieldWithUnit
            {
                id: laborHours
                width: parent.width
                labelText: catalog.i18nc("@label", "Post-processing hours")
                unitText: catalog.i18nc("@label", "h")
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Design (custom jobs only)"); font: UM.Theme.getFont("default_bold") }
            UM.CheckBox
            {
                id: includeDesignCost
                text: catalog.i18nc("@option:check", "Include design cost for this job")
            }
            Cura.NumericTextFieldWithUnit
            {
                id: designHours
                width: parent.width
                enabled: includeDesignCost.checked
                labelText: catalog.i18nc("@label", "Design hours")
                unitText: catalog.i18nc("@label", "h")
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }
            Cura.NumericTextFieldWithUnit
            {
                id: designRate
                width: parent.width
                enabled: includeDesignCost.checked
                labelText: catalog.i18nc("@label", "Design rate")
                unitText: base.currency + "/h"
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Selling"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: packagingCost
                width: parent.width
                labelText: catalog.i18nc("@label", "Packaging cost")
                unitText: base.currency
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }
            Cura.NumericTextFieldWithUnit
            {
                id: shippingCost
                width: parent.width
                labelText: catalog.i18nc("@label", "Shipping cost")
                unitText: base.currency
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }
            Cura.NumericTextFieldWithUnit
            {
                id: marketingFeePct
                width: parent.width
                labelText: catalog.i18nc("@label", "Marketing / marketplace fee")
                unitText: catalog.i18nc("@label", "fraction, e.g. 0.1 = 10%")
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            UM.Label { text: catalog.i18nc("@label", "Profit"); font: UM.Theme.getFont("default_bold") }
            Cura.NumericTextFieldWithUnit
            {
                id: marginPct
                width: parent.width
                labelText: catalog.i18nc("@label", "Profit margin")
                unitText: catalog.i18nc("@label", "fraction, e.g. 0.25 = 25%")
                labelWidth: base.labelWidth
                controlWidth: base.controlWidth
            }

            Item { width: 1; height: UM.Theme.getSize("default_margin").height }
        }
    }
}
