import QtQuick
import Quickshell
import Quickshell.Io

// Headless reader for the ACES lane manifest.
//
// The source of truth is the file the aces CLI writes (~/.config/aces/fabric.json). This service never contacts the
// server and never changes anything on it: refresh() runs `aces fabric refresh`, and the file watcher picks up the
// rewritten manifest. State is derived, not guessed: "ok" when GRID CONTROL answered, "stale" when the manifest is
// older than the configured age, "unreachable" when the CLI recorded that the server did not answer, "missing" when
// there is no manifest yet.
Item {
  id: root

  readonly property string home: Quickshell.env("HOME") || ""
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (home + "/.config")
  readonly property string manifestPath: configHome + "/aces/fabric.json"
  readonly property int staleAfterMs: 15 * 60 * 1000

  property var manifest: ({})
  // "status", not "state": Item already has a `state` property (the States mechanism), and shadowing it is a
  // property-override lint error on the Omarchy host. (Comments must not start a sentence with the linter's own
  // name followed by a word: it reads that as a directive.)
  property string status: "missing"
  property string modeName: ""
  property string modeLabel: "no manifest yet; run `aces fabric refresh`"
  property string detected: ""
  property var gpus: []
  property var lanes: []
  property var health: ({})
  property var notes: []
  property string generated: ""
  property string serverName: ""
  property string notice: ""
  property bool refreshing: false

  signal changed()

  function healthText() {
    var keys = Object.keys(health)
    var parts = []
    for (var i = 0; i < keys.length; i++) parts.push(keys[i] + ": " + health[keys[i]])
    return parts.join("  ·  ")
  }

  function gpuLine(g) {
    var mem = (typeof g.mem_used_mb === "number") ? (g.mem_used_mb / 1024).toFixed(1) + " GB" : "?"
    var line = "GPU " + g.index + "   " + (g.util === undefined ? "?" : g.util + "%") + "   " + mem
             + "   " + (g.temp_c === undefined ? "?" : g.temp_c + "°C")
    if (g.derated) line += "   derated"
    if (g.processes && g.processes.length > 0) {
      var names = []
      for (var i = 0; i < g.processes.length; i++) names.push(g.processes[i].processName || "process")
      line += "   " + names.join(", ")
    }
    return line
  }

  function laneLine(l) {
    var gpuList = (l.gpus && l.gpus.length) ? "gpus " + l.gpus.join(",") : ""
    return l.id + "   " + (l.kind || "") + "   " + gpuList + "   " + (l.endpoint ? l.endpoint : "no endpoint")
  }

  function evaluate() {
    var m = manifest || {}
    var h = m.health || {}
    var gc = String(h.grid_control || "")
    var ageMs = generated ? (Date.now() - Date.parse(generated)) : NaN
    if (!m.generated) status = "missing"
    else if (gc.indexOf("ok") !== 0) status = "unreachable"
    else if (!isNaN(ageMs) && ageMs > staleAfterMs) status = "stale"
    else status = "ok"
    changed()
  }

  function parse(text) {
    var m
    try {
      m = JSON.parse(text)
    } catch (e) {
      manifest = {}
      modeLabel = "manifest is not valid JSON: " + e
      status = "missing"
      changed()
      return
    }
    manifest = m
    var mode = m.mode || {}
    modeName = mode.name ? String(mode.name) : ""
    detected = mode.detected ? String(mode.detected) : ""
    modeLabel = modeName ? (modeName + (mode.label ? "  —  " + mode.label : "")) : "server mode unknown"
    gpus = m.gpus || []
    lanes = m.lanes || []
    health = m.health || {}
    notes = m.notes || []
    generated = m.generated ? String(m.generated) : ""
    serverName = (m.server && m.server.name) ? String(m.server.name) : ""
    evaluate()
  }

  function refresh() {
    if (refreshing) return
    refreshing = true
    notice = "refreshing from the server…"
    refreshProc.running = true
  }

  FileView {
    id: file
    path: root.manifestPath
    watchChanges: true
    onFileChanged: reload()
    onLoaded: root.parse(text())
    onLoadFailed: function(error) {
      root.manifest = {}
      root.status = "missing"
      root.modeLabel = "no manifest yet; run `aces fabric refresh`"
      root.changed()
    }
  }

  Process {
    id: refreshProc
    command: ["aces", "fabric", "refresh"]
    onExited: function(exitCode) {
      root.refreshing = false
      root.notice = exitCode === 0 ? "" : "aces fabric refresh failed (exit " + exitCode + ")"
      file.reload()
    }
  }

  // Re-evaluate staleness even when the file does not change.
  Timer {
    interval: 60 * 1000
    running: true
    repeat: true
    onTriggered: root.evaluate()
  }
}
