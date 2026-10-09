import QtQuick
import QtQuick.Controls
import Quickshell.Io
import qs.Commons
import qs.Ui

// ACES Fabric bar widget: the ACES Server at a glance.
//
// Reads ~/.config/aces/fabric.json (written by `aces fabric refresh`); never talks to the server itself and never
// switches modes. Left-click opens the panel, middle-click (or `r` in the panel, or `refresh` over IPC) re-runs the
// CLI. The bar glyph is bright when the server answered, dim when the manifest is stale or missing, urgent when the
// CLI recorded the server as unreachable.
Panel {
  id: root
  moduleName: "aces-fabric"
  ipcTarget: "aces-fabric"
  manageIpc: false

  readonly property real openIndicatorInlineOffset: bar && bar.vertical ? 0 : Style.spaceReal(1.5)

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color barForegroundColor: bar ? bar.barForeground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // Material Design Nerd Font glyph, same family the first-party panels use.
  readonly property string iconGlyph: "󰒋"      // server

  function glyphColor() {
    if (svc.status === "ok") return barForegroundColor
    if (svc.status === "unreachable") return urgent
    return Qt.darker(barForegroundColor, 1.55)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Service {
    id: svc
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { svc.refresh(); return "ok" }
    function mode(): string { return svc.modeName }
    function state(): string { return svc.status }
    function manifest(): string { return JSON.stringify(svc.manifest) }
  }

  // --- bar button ----------------------------------------------------------

  Item {
    id: button
    anchors.fill: parent
    implicitWidth: root.bar && root.bar.vertical ? root.bar.barSize : Style.space(27)
    implicitHeight: root.bar && root.bar.vertical ? Style.space(26) : (root.bar ? root.bar.barSize : Style.space(26))

    property var registeredBar: null

    function syncClickRegistration() {
      if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(button)
      registeredBar = root.bar
      if (registeredBar && registeredBar.registerClickTarget) registeredBar.registerClickTarget(button)
    }

    Component.onCompleted: syncClickRegistration()
    Component.onDestruction: if (registeredBar && registeredBar.unregisterClickTarget) registeredBar.unregisterClickTarget(button)

    Connections {
      target: root
      function onBarChanged() { button.syncClickRegistration() }
    }

    Text {
      id: barGlyph
      anchors.centerIn: parent
      anchors.horizontalCenterOffset: root.openIndicatorInlineOffset
      text: root.iconGlyph
      font.family: root.fontFamily
      font.pixelSize: Style.font.icon
      color: root.glyphColor()
    }

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.LeftButton | Qt.MiddleButton
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: function(mouse) {
        if (mouse.button === Qt.MiddleButton) svc.refresh()
        else root.toggle()
      }
    }
  }

  // --- popup ---------------------------------------------------------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(440))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "r" || t === "R") svc.refresh()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          PanelHero {
            id: hero
            width: parent.width
            title: "ACES Fabric" + (svc.serverName ? "  ·  " + svc.serverName : "")
            meta: svc.modeLabel
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: root.iconGlyph
                color: svc.status === "ok" ? root.foreground : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          // Notice, health and detected-mode line.
          Text {
            visible: svc.notice !== "" || svc.status !== "ok" || svc.detected !== ""
            width: parent.width
            text: svc.notice !== "" ? svc.notice
                : svc.status === "missing" ? svc.modeLabel
                : svc.status === "unreachable" ? "server unreachable when the manifest was written: " + svc.healthText()
                : svc.status === "stale" ? "manifest is stale (generated " + svc.generated + "); press r to refresh"
                : (svc.detected !== "" ? "detected: " + svc.detected : "")
            textFormat: Text.PlainText
            color: (svc.status === "unreachable") ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          PanelSeparator { foreground: root.foreground }

          PanelSectionHeader {
            text: "GPUS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Text {
            visible: svc.gpus.length === 0
            width: parent.width
            text: "No GPU rows in the manifest."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            Repeater {
              model: svc.gpus
              Text {
                required property var modelData
                width: parent.width
                text: svc.gpuLine(modelData)
                textFormat: Text.PlainText
                color: modelData.derated ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          PanelSectionHeader {
            text: "LANES"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Text {
            visible: svc.lanes.length === 0
            width: parent.width
            text: "No lanes derived for this mode."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            Repeater {
              model: svc.lanes
              Text {
                required property var modelData
                width: parent.width
                text: svc.laneLine(modelData)
                textFormat: Text.PlainText
                color: modelData.endpoint ? root.foreground : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
              }
            }
          }

          Column {
            visible: svc.notes.length > 0
            width: parent.width
            spacing: Style.space(2)
            Repeater {
              model: svc.notes
              Text {
                required property var modelData
                width: parent.width
                text: "note: " + modelData
                textFormat: Text.PlainText
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          Text {
            width: parent.width
            text: (svc.generated ? "manifest " + svc.generated + "  ·  " : "") + "mode switching stays in GRID CONTROL  ·  r refreshes"
            textFormat: Text.PlainText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }
}
