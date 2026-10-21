// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (C) 2026 korxspyre
/*
    NanoBorealis: the lock screen as a live dashboard, shown while nobody is at the computer (it
    fades out for the usual unlock prompt as soon as someone touches the mouse or keyboard).
    Everything comes from one file that nanoborealis-dashboard.service keeps up to date; this
    only reads it. Installed next to Plasma's lock screen by the image build.
*/

import QtQuick
import QtQuick.Layouts

Item {
    id: dash

    property var d: ({})
    property var now: new Date()
    readonly property string source: "file:///run/nanoborealis-dashboard/dashboard.json"

    // NanoBorealis colors
    readonly property color bg: Qt.rgba(0.043, 0.071, 0.133, 0.82)
    readonly property color panel: Qt.rgba(0.067, 0.102, 0.18, 0.86)
    readonly property color line: Qt.rgba(1, 1, 1, 0.07)
    readonly property color text: "#E6EDF7"
    readonly property color muted: "#8A97AD"
    readonly property color accent: "#2DD4BF"
    readonly property color good: "#34D399"
    readonly property color warn: "#FBBF24"
    readonly property color bad: "#F87171"
    readonly property real u: Math.max(10, Math.min(width / 150, height / 84))  // one unit, scales with the screen
    readonly property string mono: "JetBrains Mono, monospace"

    function load() {
        const request = new XMLHttpRequest();
        request.onreadystatechange = function () {
            if (request.readyState === XMLHttpRequest.DONE && request.responseText) {
                try { dash.d = JSON.parse(request.responseText); } catch (e) {}
            }
        };
        request.open("GET", source + "?" + Date.now());
        request.send();
    }

    function ago(ms) {
        if (!ms) return "";
        const s = Math.max(0, (Date.now() - ms) / 1000);
        if (s < 60) return "just now";
        if (s < 3600) return Math.round(s / 60) + " min ago";
        if (s < 86400) return Math.round(s / 3600) + " h ago";
        return Math.round(s / 86400) + " d ago";
    }
    function until(ms) {
        if (!ms) return "";
        const s = (ms - Date.now()) / 1000;
        if (s < 60) return "now";
        if (s < 3600) return "in " + Math.round(s / 60) + " min";
        if (s < 86400) return "in " + Math.round(s / 3600) + " h";
        return "in " + Math.round(s / 86400) + " d";
    }
    function isoMs(text) { return text ? new Date(text).getTime() : 0; }
    function bytes(n) { return n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : Math.round(n / 1e6) + " MB"; }
    function agentState() {
        const a = d.agent || {};
        const current = d.activity && d.activity.current;
        if (!a.running) return { label: "Agent down", color: bad };
        if (current) return { label: "Working", color: accent };
        return { label: "Idle, ready", color: good };
    }

    Timer { interval: 3000; running: true; repeat: true; triggeredOnStart: true; onTriggered: dash.load() }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: dash.now = new Date() }

    // A few pixels of drift every minute, so nothing burns into the screen.
    property int driftX: 0
    property int driftY: 0
    Timer {
        interval: 60000; running: true; repeat: true
        onTriggered: { dash.driftX = Math.round(Math.random() * 12 - 6); dash.driftY = Math.round(Math.random() * 12 - 6); }
    }

    Rectangle { anchors.fill: parent; color: dash.bg }

    component Panel: Rectangle {
        property string title: ""
        default property alias body: inner.data
        color: dash.panel
        radius: dash.u * 0.9
        border.color: dash.line
        clip: true
        ColumnLayout {
            id: inner
            anchors.fill: parent
            anchors.margins: dash.u * 1.4
            spacing: dash.u * 0.7
            Text {
                visible: text !== ""
                text: parent.parent.title.toUpperCase()
                color: dash.muted; font.pixelSize: dash.u * 1.0; font.letterSpacing: dash.u * 0.12; font.weight: Font.DemiBold
            }
        }
    }
    component Label: Text {
        color: dash.text
        font.pixelSize: dash.u * 1.3
        elide: Text.ElideRight
        Layout.fillWidth: true
        textFormat: Text.PlainText
    }
    component Muted: Label { color: dash.muted; font.pixelSize: dash.u * 1.1 }
    component Dot: Rectangle { width: dash.u * 0.8; height: width; radius: width / 2 }

    Item {
        anchors.fill: parent
        anchors.margins: dash.u * 2.5
        anchors.leftMargin: dash.u * 2.5 + dash.driftX
        anchors.topMargin: dash.u * 2.5 + dash.driftY

        // -- Top: the time, the agent at a glance, what needs attention
        RowLayout {
            id: top
            anchors { left: parent.left; right: parent.right; top: parent.top }
            spacing: dash.u * 2.5
            ColumnLayout {
                spacing: 0
                Text { text: Qt.formatTime(dash.now, "HH:mm"); color: dash.text; font.pixelSize: dash.u * 7; font.weight: Font.Light }
                Text { text: Qt.formatDate(dash.now, "dddd, d MMMM yyyy"); color: dash.muted; font.pixelSize: dash.u * 1.6 }
            }
            Item { Layout.fillWidth: true }
            Repeater {
                model: [
                    { label: dash.agentState().label, color: dash.agentState().color },
                    { label: ((d.devices || []).filter(x => x.online).length) + " device(s) connected", color: dash.accent },
                    { label: (d.problems || []).length ? (d.problems.length + " to look at") : "All good",
                      color: (d.problems || []).some(p => p.level === "bad") ? dash.bad : (d.problems || []).length ? dash.warn : dash.good }
                ]
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    radius: height / 2; color: dash.panel; border.color: dash.line
                    implicitHeight: dash.u * 3.2; implicitWidth: pill.implicitWidth + dash.u * 3
                    RowLayout {
                        id: pill; anchors.centerIn: parent; spacing: dash.u * 0.8
                        Dot { color: modelData.color }
                        Text { text: modelData.label; color: dash.text; font.pixelSize: dash.u * 1.3 }
                    }
                }
            }
        }

        // -- The three columns
        RowLayout {
            anchors { left: parent.left; right: parent.right; top: top.bottom; bottom: parent.bottom; topMargin: dash.u * 2 }
            spacing: dash.u * 1.5

            // Now: what the agent is doing, and what it did
            ColumnLayout {
                Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.fillWidth: true
                spacing: dash.u * 1.5
                Panel {
                    title: "Now"
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 17
                    property var c: d.activity ? d.activity.current : null
                    Label {
                        text: parent.parent.c ? ("Answering " + parent.parent.c.from) : (d.agent && d.agent.running ? "Waiting for something to do" : "The agent isn't running")
                        font.pixelSize: dash.u * 1.8; font.weight: Font.DemiBold
                        color: parent.parent.c ? dash.accent : dash.text
                    }
                    Label {
                        visible: !!parent.parent.c
                        text: parent.parent.c ? "“" + parent.parent.c.text + "”" : ""
                        wrapMode: Text.WordWrap; maximumLineCount: 3
                    }
                    Muted {
                        visible: !!parent.parent.c
                        text: parent.parent.c ? (parent.parent.c.step + "  ·  " + parent.parent.c.tools + " step(s)  ·  started " + dash.ago(parent.parent.c.since)) : ""
                    }
                    Muted {
                        text: "Model: " + ((d.api && d.api.model) || "?") + (d.api && d.api.tokens_today ? ("  ·  " + d.api.tokens_today + " tokens today") : "")
                    }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "Activity"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Repeater {
                        model: d.activity ? d.activity.events : []
                        RowLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.8
                            Text {
                                text: modelData.kind === "message" ? "→" : modelData.kind === "reply" ? (modelData.failed ? "✕" : "←") : modelData.kind === "tool" ? "•" : "◷"
                                color: modelData.kind === "reply" && modelData.failed ? dash.bad : modelData.kind === "tool" ? dash.muted : dash.accent
                                font.pixelSize: dash.u * 1.3; Layout.preferredWidth: dash.u * 1.4
                            }
                            Label {
                                text: (modelData.kind === "message" ? (modelData.source + ": ") : modelData.kind === "reply" ? "Replied: " : "") + modelData.text
                                font.pixelSize: dash.u * 1.15; maximumLineCount: 1
                                color: modelData.kind === "tool" ? dash.muted : dash.text
                            }
                            Text { text: dash.ago(modelData.at); color: dash.muted; font.pixelSize: dash.u * 1.0 }
                        }
                    }
                    Muted { visible: !(d.activity && d.activity.events && d.activity.events.length); text: "Nothing yet since the agent started." }
                    Item { Layout.fillHeight: true }
                }
            }

            // Chats and schedules
            ColumnLayout {
                Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.fillWidth: true
                spacing: dash.u * 1.5
                Panel {
                    title: "Chats"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Repeater {
                        model: d.api && d.api.chats ? d.api.chats.slice(0, 4) : []
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.2
                            RowLayout {
                                Layout.fillWidth: true
                                Label { text: modelData.title; font.weight: Font.DemiBold }
                                Text { text: dash.ago(dash.isoMs(modelData.updated)); color: dash.muted; font.pixelSize: dash.u * 1.0 }
                            }
                            Repeater {
                                model: modelData.last || []
                                Label {
                                    text: (modelData.role === "user" ? "You: " : "Agent: ") + modelData.text.replace(/\s+/g, " ")
                                    color: modelData.role === "user" ? dash.muted : dash.text
                                    font.pixelSize: dash.u * 1.1; maximumLineCount: 2; wrapMode: Text.WordWrap
                                }
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: dash.line; Layout.topMargin: dash.u * 0.4 }
                        }
                    }
                    Muted { visible: !(d.api && d.api.chats && d.api.chats.length); text: d.api && d.api.error ? "Can't reach the agent right now." : "No chats yet." }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "Scheduled"
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 17
                    Repeater {
                        model: d.api && d.api.schedules ? d.api.schedules : []
                        RowLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.8
                            Dot { color: !modelData.enabled ? dash.muted : (modelData.last_status && modelData.last_status !== "ok") ? dash.bad : dash.good }
                            Label { text: modelData.name + "  ·  " + modelData.schedule; font.pixelSize: dash.u * 1.15 }
                            Text {
                                text: modelData.enabled ? ("next " + dash.until(modelData.next)) : "paused"
                                color: dash.muted; font.pixelSize: dash.u * 1.05
                            }
                        }
                    }
                    Muted { visible: !(d.api && d.api.schedules && d.api.schedules.length); text: "Nothing scheduled." }
                    Item { Layout.fillHeight: true }
                }
            }

            // Calendar, devices, this computer, attention
            ColumnLayout {
                Layout.fillHeight: true; Layout.preferredWidth: 1; Layout.fillWidth: true
                spacing: dash.u * 1.5
                Panel {
                    title: Qt.formatDate(dash.now, "MMMM")
                    Layout.fillWidth: true; Layout.fillHeight: true
                    // The month, with a mark on days that have something
                    GridLayout {
                        id: month
                        columns: 7; rowSpacing: 0; columnSpacing: 0
                        Layout.fillWidth: true
                        readonly property date first: new Date(dash.now.getFullYear(), dash.now.getMonth(), 1)
                        readonly property int offset: (first.getDay() + 6) % 7
                        readonly property int days: new Date(dash.now.getFullYear(), dash.now.getMonth() + 1, 0).getDate()
                        Repeater {
                            model: ["M", "T", "W", "T", "F", "S", "S"]
                            Text { text: modelData; color: dash.muted; font.pixelSize: dash.u * 1.0; horizontalAlignment: Text.AlignHCenter; Layout.fillWidth: true }
                        }
                        Repeater {
                            model: 42
                            Item {
                                Layout.fillWidth: true; implicitHeight: dash.u * 2.4
                                readonly property int day: index - month.offset + 1
                                readonly property bool real: day >= 1 && day <= month.days
                                readonly property bool today: real && day === dash.now.getDate()
                                readonly property string key: Qt.formatDate(new Date(dash.now.getFullYear(), dash.now.getMonth(), day), "yyyy-MM-dd")
                                readonly property bool busy: real && (d.calendar || []).some(e => e.start.slice(0, 10) === key)
                                visible: index < month.offset + month.days + (7 - (month.offset + month.days) % 7) % 7
                                Rectangle {
                                    anchors.centerIn: parent; width: dash.u * 2.1; height: width; radius: width / 2
                                    color: parent.today ? dash.accent : "transparent"
                                }
                                Text {
                                    anchors.centerIn: parent
                                    text: parent.real ? parent.day : ""
                                    color: parent.today ? "#08111F" : dash.text; font.pixelSize: dash.u * 1.1
                                }
                                Dot { visible: parent.busy && !parent.today; color: dash.accent; width: dash.u * 0.35; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom }
                            }
                        }
                    }
                    // What's coming: your events and the agent's, together
                    Repeater {
                        model: (d.calendar || []).filter(e => dash.isoMs(e.end) >= Date.now() - 3600000).slice(0, 6)
                        RowLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.8
                            Rectangle { width: dash.u * 0.3; Layout.fillHeight: true; radius: 2; color: modelData.agent ? dash.accent : "#818CF8" }
                            Text {
                                text: modelData.all_day ? Qt.formatDate(new Date(modelData.start), "ddd d") : Qt.formatDateTime(new Date(modelData.start), "ddd HH:mm")
                                color: dash.muted; font.pixelSize: dash.u * 1.05; font.family: dash.mono; Layout.preferredWidth: dash.u * 8
                            }
                            Label { text: modelData.title + (modelData.where ? ("  ·  " + modelData.where) : "") + (modelData.agent ? "  (agent)" : ""); font.pixelSize: dash.u * 1.15 }
                        }
                    }
                    Muted {
                        visible: !(d.calendar || []).some(e => !e.agent)
                        text: "Your calendars show here too: list them in ~/.config/nanoborealis/calendars."
                        wrapMode: Text.WordWrap; font.pixelSize: dash.u * 1.0
                    }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "Council"
                    visible: !!d.council && !d.council.error
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 10
                    Label {
                        text: d.council ? ((d.council.seats || []).length + " seat(s) · " + d.council.open + " open · "
                              + d.council.working + " in progress · " + d.council.review + " to review"
                              + (d.council.votes ? " · " + d.council.votes + " vote(s) open" : "")) : ""
                        font.pixelSize: dash.u * 1.1
                    }
                    Text {
                        visible: !!(d.council && d.council.latest)
                        text: d.council && d.council.latest ? d.council.latest.author + ": " + d.council.latest.text : ""
                        color: dash.accent; font.pixelSize: dash.u * 1.05
                        wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true
                    }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "Devices"
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 12
                    Repeater {
                        model: (d.devices || []).slice(0, 5)
                        RowLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.8
                            Dot { color: modelData.online ? dash.good : dash.muted }
                            Label { text: modelData.name + (modelData.here ? " (this computer)" : ""); font.pixelSize: dash.u * 1.15 }
                            Text {
                                text: modelData.online ? modelData.doing.join(", ") : ("seen " + dash.ago((modelData.seen || 0) * 1000))
                                color: modelData.online ? dash.accent : dash.muted; font.pixelSize: dash.u * 1.05
                            }
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "This computer"
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 13
                    property var m: d.machine || {}
                    property var n: d.network || {}
                    Muted {
                        text: {
                            const p = parent.parent.m.power || {};
                            const power = p.plugged_in === undefined ? "" : (p.plugged_in ? "Plugged in" : "On battery") + (p.battery !== undefined && p.battery !== null ? (", " + p.battery + "%") : "");
                            return [power, parent.parent.n.wifi ? ("Wi-Fi " + parent.parent.n.wifi.ssid + " " + parent.parent.n.wifi.signal + "%") : "",
                                    parent.parent.n.online === false ? "offline" : ""].filter(x => x).join("  ·  ");
                        }
                    }
                    Muted {
                        text: {
                            const m = parent.parent.m;
                            const parts = [];
                            if (m.load) parts.push("Load " + m.load[0].toFixed(2) + " / " + m.cpus + " cores");
                            if (m.memory) parts.push("Memory " + dash.bytes(m.memory.used) + " of " + dash.bytes(m.memory.total));
                            return parts.join("  ·  ");
                        }
                    }
                    Muted {
                        text: {
                            const m = parent.parent.m, o = d.os || {};
                            const parts = [];
                            if (m.disk) parts.push("Disk " + Math.round(100 * m.disk.used / m.disk.total) + "% used");
                            if (m.uptime_s) parts.push("up " + Math.round(m.uptime_s / 3600) + " h");
                            if (o.booted) parts.push(o.booted);
                            return parts.join("  ·  ");
                        }
                    }
                    Item { Layout.fillHeight: true }
                }
                Panel {
                    title: "Needs attention"
                    visible: (d.problems || []).length > 0
                    Layout.fillWidth: true; Layout.preferredHeight: dash.u * 3.4 + (d.problems || []).length * dash.u * 2.4
                    Repeater {
                        model: d.problems || []
                        RowLayout {
                            Layout.fillWidth: true; spacing: dash.u * 0.8
                            Dot { color: modelData.level === "bad" ? dash.bad : modelData.level === "warn" ? dash.warn : dash.accent }
                            Label { text: modelData.text; font.pixelSize: dash.u * 1.1; maximumLineCount: 2; wrapMode: Text.WordWrap }
                        }
                    }
                }
            }
        }

        Text {
            anchors { right: parent.right; bottom: parent.bottom; bottomMargin: -dash.u * 1.6 }
            text: d.updated ? ("Updated " + dash.ago(d.updated) + "  ·  move the mouse or press a key to unlock") : "Waiting for the dashboard service…"
            color: dash.muted; font.pixelSize: dash.u * 0.95; opacity: 0.7
        }
    }
}
