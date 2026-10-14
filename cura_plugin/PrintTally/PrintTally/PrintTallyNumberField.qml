import QtQuick 2.10
import QtQuick.Controls 2.3
import UM 1.5 as UM

Row
{
    id: field
    property string labelText
    property string unitText
    property int labelWidth
    property int controlWidth
    property real maximumValue: Number.MAX_VALUE
    property alias valueText: input.text
    property bool acceptableInput: input.acceptableInput && input.text.length > 0
        && isFinite(Number(input.text)) && Number(input.text) <= maximumValue
    UM.Label
    {
        width: field.labelWidth
        text: field.labelText
        wrapMode: Text.WordWrap
        anchors.verticalCenter: parent.verticalCenter
    }
    Row
    {
        width: field.controlWidth
        spacing: UM.Theme.getSize("default_lining").height
        TextField
        {
            id: input
            width: parent.width * 0.55
            selectByMouse: true
            font: UM.Theme.getFont("default")
            color: UM.Theme.getColor("text")
            selectionColor: UM.Theme.getColor("primary")
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            validator: RegExpValidator { regExp: /[0-9]+(\.[0-9]*)?/ }
            background: Rectangle
            {
                color: UM.Theme.getColor("main_background")
                border.color: field.acceptableInput ? UM.Theme.getColor("lining") : UM.Theme.getColor("warning")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
            }
            Accessible.name: field.labelText
            Accessible.description: field.unitText
        }
        UM.Label { text: field.unitText; font: UM.Theme.getFont("small"); width: parent.width * 0.45; anchors.verticalCenter: parent.verticalCenter; wrapMode: Text.WordWrap }
    }
}
