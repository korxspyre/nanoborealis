/*
    SPDX-FileCopyrightText: 2014 Marco Martin <mart@kde.org>
    SPDX-FileCopyrightText: 2026 korxspyre

    SPDX-License-Identifier: GPL-2.0-or-later

    Changed from Plasma's Breeze splash, by way of Aurora's, for NanoBorealis.
*/
// While Plasma starts: the NanoBorealis star over the darkened aurora.
import QtQuick
import QtQuick.Window
import org.kde.kirigami as Kirigami

Rectangle {
    id: root
    color: "#070d1f"

    property int stage

    onStageChanged: {
        if (stage == 2) {
            introAnimation.running = true;
        } else if (stage == 5) {
            introAnimation.target = busyIndicator;
            introAnimation.from = 1;
            introAnimation.to = 0;
            introAnimation.running = true;
        }
    }

    Image {
        anchors.fill: parent
        source: "file:///usr/share/wallpapers/NanoBorealis/contents/images/1920x1080.jpg"
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        opacity: 0.5
    }

    Item {
        id: content
        anchors.fill: parent
        opacity: 0

        Image {
            id: logo
            readonly property real size: Kirigami.Units.gridUnit * 7
            anchors.centerIn: parent
            asynchronous: true
            source: "images/nanoborealis.svg"
            sourceSize.width: size
            sourceSize.height: size
        }

        Text {
            id: wordmark
            anchors.top: logo.bottom
            anchors.topMargin: Kirigami.Units.gridUnit
            anchors.horizontalCenter: parent.horizontalCenter
            text: "NanoBorealis"
            color: "#e6edf7"
            font.pixelSize: Kirigami.Units.gridUnit * 1.6
            font.weight: Font.Light
            font.letterSpacing: Kirigami.Units.gridUnit * 0.12
            renderType: Screen.devicePixelRatio % 1 !== 0 ? Text.QtRendering : Text.NativeRendering
        }

        Image {
            id: busyIndicator
            y: parent.height - (parent.height - wordmark.y - wordmark.height) / 2 - height / 2
            anchors.horizontalCenter: parent.horizontalCenter
            asynchronous: true
            source: "file:///usr/share/plasma/look-and-feel/org.kde.breeze.desktop/contents/splash/images/busywidget.svgz"
            sourceSize.height: Kirigami.Units.gridUnit * 2
            sourceSize.width: Kirigami.Units.gridUnit * 2
            RotationAnimator on rotation {
                from: 0
                to: 360
                duration: 2000
                loops: Animation.Infinite
                running: Kirigami.Units.longDuration > 1
            }
        }
    }

    OpacityAnimator {
        id: introAnimation
        running: false
        target: content
        from: 0
        to: 1
        duration: Kirigami.Units.veryLongDuration * 2
        easing.type: Easing.InOutQuad
    }
}
