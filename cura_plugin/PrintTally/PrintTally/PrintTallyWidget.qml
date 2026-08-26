// Copyright (c) 2026, PrintTally -- MIT licensed, see the repo's LICENSE file.
import QtQuick 2.7
import QtQuick.Controls 2.1
import UM 1.5 as UM
import Cura 1.0 as Cura

Rectangle
{
    id: printTallyWidget
    color: UM.Theme.getColor("main_background")
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    radius: UM.Theme.getSize("default_radius").width
    width: childrenRect.width + UM.Theme.getSize("default_margin").width
    height: childrenRect.height + UM.Theme.getSize("default_margin").height
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.bottomMargin: -UM.Theme.getSize("thick_margin").width
    visible: (UM.Backend.state == UM.Backend.Done || UM.Backend.state == UM.Backend.Disabled) && manager.hasEstimate

    Cura.IconWithText
    {
        id: printTallyPrice
        anchors.verticalCenter: parent.verticalCenter
        anchors.horizontalCenter: parent.horizontalCenter
        font: UM.Theme.getFont("default")
        source: Qt.resolvedUrl("icon-coin.svg")
        text: "%1 %2".arg(UM.Preferences.getValue("cura/currency")).arg(manager.finalPrice.toFixed(2))
    }

    MouseArea
    {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        onClicked: manager.showSettingsDialog()
        onEntered: { tooltip.text = manager.breakdownText; tooltip.show() }
        onExited: tooltip.hide()
    }

    UM.ToolTip
    {
        id: tooltip
        arrowSize: 0
        x: mouseArea.mouseX
        y: -height - UM.Theme.getSize("default_margin").height
    }
}
