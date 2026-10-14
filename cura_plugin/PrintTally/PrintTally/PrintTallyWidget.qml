import QtQuick 2.10
import QtQuick.Controls 2.3
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Rectangle
{
    id: card
    property var display: JSON.parse(manager.displayJson)
    property real margin: UM.Theme.getSize("default_margin").width
    width: UM.Theme.getSize("default_margin").width * 26
    height: content.implicitHeight + 2 * margin
    color: UM.Theme.getColor("main_background")
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    radius: UM.Theme.getSize("default_radius").width
    anchors.bottom: parent ? parent.bottom : undefined
    anchors.bottomMargin: -UM.Theme.getSize("thick_margin").width
    visible: (UM.Backend.state == UM.Backend.Done || UM.Backend.state == UM.Backend.Disabled) && manager.hasEstimate

    ColumnLayout
    {
        id: content
        x: card.margin
        y: card.margin
        width: parent.width - 2 * card.margin
        spacing: UM.Theme.getSize("default_margin").height / 2
        UM.Label { text: "PrintTally · Total price"; font: UM.Theme.getFont("default") }
        UM.Label
        {
            text: card.display.total
            font.family: UM.Theme.getFont("large").family
            font.pixelSize: UM.Theme.getFont("large").pixelSize
            font.bold: true
            Layout.fillWidth: true
            wrapMode: Text.WrapAnywhere
        }
        UM.Label
        {
            text: card.display.badge
            textFormat: Text.PlainText
            color: card.display.sellable ? UM.Theme.getColor("text") : UM.Theme.getColor("error")
            font: UM.Theme.getFont("default_bold")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        Rectangle { Layout.fillWidth: true; height: UM.Theme.getSize("default_lining").height; color: UM.Theme.getColor("lining") }
        Repeater
        {
            model: card.display.rows
            delegate: RowLayout
            {
                Layout.fillWidth: true
                UM.Label { text: modelData.label; textFormat: Text.PlainText; Layout.fillWidth: true; Layout.preferredWidth: card.width * 0.55; wrapMode: Text.WordWrap; font: UM.Theme.getFont("small") }
                UM.Label { text: modelData.value; textFormat: Text.PlainText; horizontalAlignment: Text.AlignRight; font: UM.Theme.getFont("small") }
            }
        }
        Repeater
        {
            model: card.display.warnings
            delegate: UM.Label
            {
                text: "⚠ " + modelData
                textFormat: Text.PlainText
                color: UM.Theme.getColor("warning")
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font: UM.Theme.getFont("small")
            }
        }
        Cura.SecondaryButton { text: "Settings"; Layout.alignment: Qt.AlignRight; onClicked: manager.showSettingsDialog() }
    }
}
