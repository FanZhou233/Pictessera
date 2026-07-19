"""Non-modal Apple-inspired settings window."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photo_manager import __version__
from photo_manager.services.i18n import TranslationService
from photo_manager.services.settings import SettingsService, detect_lightroom_path, windows_apps_use_light_theme
from photo_manager.ui.theme_profiles import make_theme_font, resolve_theme_profile, theme_display_point_size


ACCENTS = {
    "blue": "#007AFF",
    "purple": "#AF52DE",
    "pink": "#FF2D55",
    "orange": "#FF9500",
    "green": "#34C759",
    "red": "#FF3B30",
}


class AccentButton(QToolButton):
    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.color = QColor(color)
        self.visual_style = "apple"
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QToolButton { border: none; background: transparent; }")

    def set_visual_style(self, style: str):
        self.visual_style = str(style or "apple")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        center = self.rect().center()
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color)
        if self.visual_style in {"win11", "win7"}:
            radius = 4 if self.visual_style == "win11" else 1
            painter.drawRoundedRect(QRectF(center.x() - 10, center.y() - 10, 20, 20), radius, radius)
        else:
            painter.drawEllipse(center, 10, 10)
        if self.isChecked():
            painter.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(center.x() - 4, center.y(), center.x() - 1, center.y() + 3)
            painter.drawLine(center.x() - 1, center.y() + 3, center.x() + 5, center.y() - 4)


class SwitchControl(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.accent = "#007AFF"
        self.classic = False
        self.visual_style = "apple"
        self.setFixedSize(42, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QCheckBox { background: transparent; border: none; }")

    def set_visual_style(self, style: str):
        self.visual_style = str(style or "apple")
        self.classic = self.visual_style in {"win7", "win2000", "macos8"}
        self.setFixedSize(20, 20) if self.classic else self.setFixedSize(42, 24)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.classic:
            box = self.rect().adjusted(2, 2, -2, -2)
            fill = "#FFFFFF" if self.visual_style != "macos8" else "#EEEEEE"
            dark = "#404040" if self.visual_style == "win2000" else "#555555"
            light = "#FFFFFF"
            painter.fillRect(box, QColor(fill))
            painter.setPen(QPen(QColor(dark), 1))
            painter.drawLine(box.topLeft(), box.topRight())
            painter.drawLine(box.topLeft(), box.bottomLeft())
            painter.setPen(QPen(QColor(light), 1))
            painter.drawLine(box.bottomLeft(), box.bottomRight())
            painter.drawLine(box.topRight(), box.bottomRight())
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawRect(box.adjusted(1, 1, -1, -1))
            if self.isChecked():
                checked = "#000080" if self.visual_style == "win2000" else ("#3366CC" if self.visual_style == "macos8" else self.accent)
                painter.setPen(QPen(QColor(checked), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
                painter.drawLine(box.left() + 4, box.center().y(), box.left() + 7, box.bottom() - 4)
                painter.drawLine(box.left() + 7, box.bottom() - 4, box.right() - 3, box.top() + 4)
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        track = self.rect().adjusted(1, 2, -1, -2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.accent if self.isChecked() else "#C7C7CC"))
        painter.drawRoundedRect(track, 10, 10)
        diameter = 18
        x = self.width() - diameter - 3 if self.isChecked() else 3
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(QPen(QColor(0, 0, 0, 24), 0.7))
        painter.drawEllipse(x, 3, diameter, diameter)


class AppleSpinBox(QSpinBox):
    """Spin box with an inset macOS-style chevron stepper."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_half = 0
        self._pressed_half = 0
        self.visual_style = "apple"
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setMouseTracking(True)
        self.setAccelerated(True)
        self.lineEdit().setTextMargins(4, 0, 36, 0)
        self.lineEdit().setMouseTracking(True)
        self.lineEdit().installEventFilter(self)

    def set_visual_style(self, style: str):
        self.visual_style = str(style or "apple")
        custom = self.visual_style == "apple"
        self.setButtonSymbols(QAbstractSpinBox.NoButtons if custom else QAbstractSpinBox.UpDownArrows)
        self.lineEdit().setTextMargins(4, 0, 36 if custom else 4, 0)
        self.update()

    def _stepper_half_at(self, pos) -> int:
        if self.visual_style != "apple":
            return 0
        if pos.x() < self.width() - 36:
            return 0
        return 1 if pos.y() < self.height() / 2 else -1

    def mouseMoveEvent(self, event):
        try:
            pos = event.position()
        except Exception:
            pos = event.pos()
        half = self._stepper_half_at(pos)
        if half != self._hover_half:
            self._hover_half = half
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_half = 0
        self._pressed_half = 0
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                pos = event.position()
            except Exception:
                pos = event.pos()
            half = self._stepper_half_at(pos)
            if half:
                self._pressed_half = half
                self.stepBy(half)
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_half:
            self._pressed_half = 0
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit():
            event_type = event.type()
            if event_type in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                try:
                    pos = self.mapFromGlobal(event.globalPosition().toPoint())
                except Exception:
                    pos = self.mapFrom(self.lineEdit(), event.pos())
                half = self._stepper_half_at(pos)
                if event_type == QEvent.MouseMove:
                    if half != self._hover_half:
                        self._hover_half = half
                        self.update()
                elif event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and half:
                    self._pressed_half = half
                    self.stepBy(half)
                    self.update()
                    return True
                elif event_type == QEvent.MouseButtonRelease and self._pressed_half:
                    self._pressed_half = 0
                    self.update()
                    return True
            elif event_type == QEvent.Leave:
                self._hover_half = 0
                self._pressed_half = 0
                self.update()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.visual_style != "apple":
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        clip = QPainterPath()
        clip.addRoundedRect(outer, 8.5, 8.5)
        painter.setClipPath(clip)

        step_x = self.width() - 36.0
        if self._hover_half or self._pressed_half:
            half = self._pressed_half or self._hover_half
            top = 1.0 if half > 0 else self.height() / 2.0
            bottom = self.height() / 2.0 if half > 0 else self.height() - 1.0
            alpha = 32 if self._pressed_half else 18
            painter.fillRect(QRectF(step_x, top, 35, bottom - top), QColor(0, 122, 255, alpha))

        separator = self.palette().color(QPalette.Mid)
        separator.setAlpha(105)
        painter.setPen(QPen(separator, 1.0))
        painter.drawLine(QPointF(step_x, 6), QPointF(step_x, self.height() - 6))

        glyph = self.palette().color(QPalette.Text)
        glyph.setAlpha(205 if self.isEnabled() else 90)
        pen = QPen(glyph, 1.35)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx = self.width() - 18.0
        upper_y = self.height() * 0.31
        lower_y = self.height() * 0.69
        painter.drawLine(QPointF(cx - 3.2, upper_y + 1.5), QPointF(cx, upper_y - 1.5))
        painter.drawLine(QPointF(cx, upper_y - 1.5), QPointF(cx + 3.2, upper_y + 1.5))
        painter.drawLine(QPointF(cx - 3.2, lower_y - 1.5), QPointF(cx, lower_y + 1.5))
        painter.drawLine(QPointF(cx, lower_y + 1.5), QPointF(cx + 3.2, lower_y - 1.5))
        painter.end()


class FlavorCaptionButton(QPushButton):
    """Caption button shared by the Windows 7/11/2000 settings chrome."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self.skin = "win7"
        self.setFixedSize(46 if role != "close" else 52, 28)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")

    def set_skin(self, skin: str):
        self.skin = skin if skin in {"win11", "win7", "win2000"} else "win7"
        if self.skin == "win2000":
            self.setFixedSize(24, 20)
        elif self.skin == "win11":
            self.setFixedSize(42, 28)
        else:
            self.setFixedSize(46 if self.role != "close" else 52, 28)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        down = self.isDown()
        hover = self.underMouse()
        if self.skin == "win2000":
            rect = self.rect().adjusted(1, 1, -2, -2)
            painter.fillRect(rect, QColor("#D4D0C8"))
            light, dark = (QColor("#333333"), QColor("#FFFFFF")) if down else (QColor("#FFFFFF"), QColor("#333333"))
            painter.setPen(QPen(light, 1))
            painter.drawLine(rect.topLeft(), rect.topRight())
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
            painter.setPen(QPen(dark, 1))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomRight())
            glyph = QColor("#000000")
        elif self.skin == "win11":
            rect = self.rect()
            if hover or down:
                painter.fillRect(rect, QColor("#C42B1C" if self.role == "close" else "#D8D8DC"))
            glyph = QColor("#FFFFFF" if self.role == "close" and (hover or down) else "#333333")
        else:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -1.5)
            face = QLinearGradient(0, rect.top(), 0, rect.bottom())
            if self.role == "close":
                face.setColorAt(0.0, QColor("#F9B1A8" if not down else "#DE776E"))
                face.setColorAt(0.46, QColor("#E86E63" if not down else "#C94C43"))
                face.setColorAt(1.0, QColor("#B63B35" if not down else "#932C28"))
                outline = QColor("#87332F")
                glyph = QColor("#FFFFFF")
            else:
                face.setColorAt(0.0, QColor("#F6FCFF" if not down else "#C0DFEE"))
                face.setColorAt(0.45, QColor("#C8E4F0" if not down else "#8EBFD7"))
                face.setColorAt(1.0, QColor("#75AAC5" if not down else "#568AA6"))
                outline = QColor("#4A7893")
                glyph = QColor("#16394E")
            painter.setPen(QPen(outline, 1.0))
            painter.setBrush(face)
            painter.drawRoundedRect(rect, 3.0, 3.0)
            painter.setPen(QPen(QColor(255, 255, 255, 180 if hover else 125), 1.0))
            painter.drawLine(QPointF(2, 1.5), QPointF(self.width() - 3, 1.5))

        pen = QPen(glyph, 1.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        if self.role == "min":
            painter.drawLine(QPointF(cx - 5, cy + 3), QPointF(cx + 5, cy + 3))
        elif self.role == "max":
            painter.drawRect(QRectF(cx - 5, cy - 5, 10, 9))
        else:
            painter.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx + 4, cy + 4))
            painter.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx - 4, cy + 4))


class FlavorSettingsTitleBar(QWidget):
    """Movable title bar for every Windows flavour settings window."""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self.dialog = dialog
        self._drag_offset = QPoint()
        self.skin = "win7"
        self.setObjectName("FlavorSettingsTitleBar")
        self.setFixedHeight(34)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 0, 3)
        layout.setSpacing(0)
        self.title = QLabel(dialog.windowTitle(), self)
        self.title.setObjectName("FlavorSettingsCaption")
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.btn_min = FlavorCaptionButton("min", self)
        self.btn_max = FlavorCaptionButton("max", self)
        self.btn_close = FlavorCaptionButton("close", self)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)
        self.btn_min.clicked.connect(dialog.showMinimized)
        self.btn_max.clicked.connect(self._toggle_maximized)
        self.btn_close.clicked.connect(dialog.close)
        self.set_skin("win7")

    def set_skin(self, skin: str):
        self.skin = skin if skin in {"win11", "win7", "win2000"} else "win7"
        self.setFixedHeight(28 if self.skin == "win2000" else (32 if self.skin == "win7" else 36))
        for button in (self.btn_min, self.btn_max, self.btn_close):
            button.set_skin(self.skin)
        self.update()

    def set_title(self, title: str):
        self.title.setText(title)

    def _toggle_maximized(self):
        self.dialog.showNormal() if self.dialog.isMaximized() else self.dialog.showMaximized()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.dialog.isMaximized():
            self._drag_offset = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.dialog.isMaximized():
            self.dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.skin == "win2000":
            painter.fillRect(self.rect(), QColor("#0A246A"))
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        if self.skin == "win11":
            painter.fillRect(self.rect(), QColor("#F3F3F3"))
            painter.setPen(QPen(QColor("#DADADA"), 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
            return
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor("#E5F5FC"))
        gradient.setColorAt(0.16, QColor("#C7E7F4"))
        gradient.setColorAt(0.48, QColor("#8CC2DA"))
        gradient.setColorAt(0.78, QColor("#5796B9"))
        gradient.setColorAt(1.0, QColor("#3C759A"))
        painter.fillRect(self.rect(), gradient)
        gloss = QLinearGradient(0, 0, 0, self.height() * 0.56)
        gloss.setColorAt(0.0, QColor(255, 255, 255, 185))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(0, 0, self.width(), int(self.height() * 0.56), gloss)
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1))
        painter.drawLine(1, 0, self.width() - 2, 0)
        painter.setPen(QPen(QColor("#285E7F"), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class MacOS8CaptionButton(QPushButton):
    """One-bit Platinum-style caption control used by the Mac OS 8 skin."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self.setFixedSize(22, 20)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect().adjusted(1, 1, -2, -2)
        painter.fillRect(rect, QColor("#DDDDDD"))
        if self.isDown():
            top_left, bottom_right = QColor("#555555"), QColor("#FFFFFF")
        else:
            top_left, bottom_right = QColor("#FFFFFF"), QColor("#555555")
        painter.setPen(QPen(top_left, 1))
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.setPen(QPen(bottom_right, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.setPen(QPen(QColor("#000000"), 1))
        cx, cy = rect.center().x(), rect.center().y()
        if self.role == "min":
            painter.drawLine(cx - 4, cy + 3, cx + 4, cy + 3)
        elif self.role == "max":
            painter.drawRect(cx - 4, cy - 4, 8, 7)
        else:
            painter.drawLine(cx - 4, cy - 4, cx + 4, cy + 4)
            painter.drawLine(cx + 4, cy - 4, cx - 4, cy + 4)


class MacOS8SettingsTitleBar(QWidget):
    """Classic Platinum title bar so the settings window matches Mac OS 8."""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self.dialog = dialog
        self._drag_offset = QPoint()
        self.setObjectName("MacOS8SettingsTitleBar")
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(3)
        self.btn_close = MacOS8CaptionButton("close", self)
        self.btn_min = MacOS8CaptionButton("min", self)
        self.btn_max = MacOS8CaptionButton("max", self)
        self.title = QLabel(dialog.windowTitle(), self)
        self.title.setObjectName("MacOS8SettingsCaption")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.btn_close)
        layout.addStretch(1)
        layout.addWidget(self.title, 0)
        layout.addStretch(1)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        self.btn_min.clicked.connect(dialog.showMinimized)
        self.btn_max.clicked.connect(self._toggle_maximized)
        self.btn_close.clicked.connect(dialog.close)

    def set_title(self, title: str):
        self.title.setText(title)

    def _toggle_maximized(self):
        self.dialog.showNormal() if self.dialog.isMaximized() else self.dialog.showMaximized()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.dialog.isMaximized():
            self._drag_offset = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.dialog.isMaximized():
            self.dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#DDDDDD"))
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.drawLine(0, 0, self.width() - 1, 0)
        for y in range(5, self.height() - 2, 3):
            painter.drawLine(0, y, self.width() - 1, y)
        painter.setPen(QPen(QColor("#777777"), 1))
        painter.drawLine(0, self.height() - 1, self.width() - 1, self.height() - 1)


class ModernMacCaptionButton(QPushButton):
    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self.setFixedSize(20, 20)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        colors = {"close": "#FF5F57", "min": "#FEBB2E", "max": "#28C840"}
        circle = QRectF(3, 3, 14, 14)
        painter.setPen(QPen(QColor(0, 0, 0, 35), 0.8))
        painter.setBrush(QColor(colors.get(self.role, "#C7C7CC")))
        painter.drawEllipse(circle)
        if not (self.underMouse() or self.isDown()):
            return
        painter.setPen(QPen(QColor("#513A35"), 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = circle.center().x(), circle.center().y()
        if self.role == "min":
            painter.drawLine(cx - 3, cy, cx + 3, cy)
        elif self.role == "max":
            painter.drawLine(cx - 2.6, cy + 2.6, cx + 2.6, cy - 2.6)
            painter.drawLine(cx + 0.3, cy - 2.6, cx + 2.6, cy - 2.6)
            painter.drawLine(cx + 2.6, cy - 2.6, cx + 2.6, cy - 0.3)
        else:
            painter.drawLine(cx - 2.7, cy - 2.7, cx + 2.7, cy + 2.7)
            painter.drawLine(cx + 2.7, cy - 2.7, cx - 2.7, cy + 2.7)


class ModernMacSettingsTitleBar(QWidget):
    """Modern macOS-style settings chrome for the standard light/dark themes."""

    def __init__(self, dialog: QDialog, parent=None):
        super().__init__(parent)
        self.dialog = dialog
        self.dark = False
        self._drag_offset = QPoint()
        self.setObjectName("ModernMacSettingsTitleBar")
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)
        self.btn_close = ModernMacCaptionButton("close", self)
        self.btn_min = ModernMacCaptionButton("min", self)
        self.btn_max = ModernMacCaptionButton("max", self)
        self.title = QLabel(dialog.windowTitle(), self)
        self.title.setObjectName("ModernMacSettingsCaption")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.btn_close)
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addSpacing(10)
        layout.addWidget(self.title, 1)
        layout.addSpacing(82)
        self.btn_min.clicked.connect(dialog.showMinimized)
        self.btn_max.clicked.connect(self._toggle_maximized)
        self.btn_close.clicked.connect(dialog.close)

    def set_title(self, title: str):
        self.title.setText(title)

    def set_dark(self, dark: bool):
        self.dark = bool(dark)
        self.update()

    def _toggle_maximized(self):
        self.dialog.showNormal() if self.dialog.isMaximized() else self.dialog.showMaximized()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.dialog.isMaximized():
            self._drag_offset = event.globalPosition().toPoint() - self.dialog.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.dialog.isMaximized():
            self.dialog.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2C2C2E" if self.dark else "#F7F7F8"))
        painter.setPen(QPen(QColor("#48484A" if self.dark else "#D9D9DE"), 1))
        painter.drawLine(0, self.height() - 1, self.width() - 1, self.height() - 1)


class SettingsDialog(QDialog):
    action_requested = Signal(str, object)

    def __init__(
        self,
        settings: SettingsService,
        translations: TranslationService,
        assets_directory: Path,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.translations = translations
        self.assets_directory = Path(assets_directory)
        self._bound_widgets: dict[str, tuple[QWidget, str, object]] = {}
        self._page_buttons: list[QPushButton] = []
        self._standard_window_flags = self.windowFlags()
        initial_theme = str(self.settings.get("appearance.theme", "system"))
        initial_titlebar = str(self.settings.get("appearance.titlebar_style", "macos"))
        self._frame_skin = (
            initial_theme if initial_theme in {"win11", "win7", "win2000", "macos8"}
            else "win11" if initial_titlebar == "windows" else "macos"
        )
        if self._frame_skin:
            self.setWindowFlags(self._standard_window_flags | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)

        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(self.translations.tr("settings.title"))
        self.setMinimumSize(900, 650)
        self.resize(980, 710)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._build_ui()
        self.retranslate()
        self._install_style()
        self.settings.setting_changed.connect(self._on_external_setting_change)
        self.settings.settings_reloaded.connect(lambda _snapshot: self.sync_all())
        self.translations.language_changed.connect(
            lambda _locale: (self.retranslate(), self._install_style())
        )
        self.sync_all()

    def _icon(self, name: str) -> QIcon:
        path = self.assets_directory / "icons" / f"{name}.svg"
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            return QIcon(str(path))

        def pixmap(color: str) -> QPixmap:
            try:
                dpr = max(1.0, float(self.devicePixelRatioF()))
            except Exception:
                dpr = 1.0
            physical = max(20, int(round(20 * dpr)))
            image = QPixmap(physical, physical)
            image.fill(Qt.transparent)
            renderer = QSvgRenderer(QByteArray(source.replace("currentColor", color).encode("utf-8")))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            renderer.render(painter, QRectF(0, 0, physical, physical))
            painter.end()
            image.setDevicePixelRatio(dpr)
            return image

        icon = QIcon()
        icon.addPixmap(pixmap("#6E6E73"), QIcon.Normal, QIcon.Off)
        icon.addPixmap(pixmap("#FFFFFF"), QIcon.Normal, QIcon.On)
        icon.addPixmap(pixmap("#FFFFFF"), QIcon.Selected, QIcon.On)
        icon.addPixmap(pixmap("#1D1D1F"), QIcon.Active, QIcon.Off)
        return icon

    def _build_ui(self):
        root = QVBoxLayout(self)
        self._root_layout = root
        rim = 1 if self._frame_skin == "win2000" else 0
        root.setContentsMargins(rim, rim, rim, rim)
        root.setSpacing(0)

        self.flavor_titlebar = FlavorSettingsTitleBar(self, self)
        self.flavor_titlebar.set_skin(self._frame_skin)
        self.flavor_titlebar.setVisible(self._frame_skin in {"win11", "win7", "win2000"})
        root.addWidget(self.flavor_titlebar)
        self.macos8_titlebar = MacOS8SettingsTitleBar(self, self)
        self.macos8_titlebar.setVisible(self._frame_skin == "macos8")
        root.addWidget(self.macos8_titlebar)
        self.modern_macos_titlebar = ModernMacSettingsTitleBar(self, self)
        self.modern_macos_titlebar.setVisible(self._frame_skin == "macos")
        root.addWidget(self.modern_macos_titlebar)

        client = QFrame(self)
        client.setObjectName("SettingsClient")
        client.setAttribute(Qt.WA_StyledBackground, True)
        client.setAutoFillBackground(True)
        self.settings_client = client
        client_layout = QHBoxLayout(client)
        client_layout.setContentsMargins(0, 0, 0, 0)
        client_layout.setSpacing(0)
        root.addWidget(client, 1)

        sidebar = QFrame(client)
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(204)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 22, 16, 18)
        side_layout.setSpacing(6)
        self.sidebar_title = QLabel(self.translations.tr("settings.title"), sidebar)
        self.sidebar_title.setObjectName("SettingsSidebarTitle")
        side_layout.addWidget(self.sidebar_title)
        side_layout.addSpacing(14)

        self.stack = QStackedWidget(client)
        self.stack.setAttribute(Qt.WA_StyledBackground, True)
        self.stack.setAutoFillBackground(True)
        pages = [
            ("settings.general", "gear", self._build_general_page),
            ("settings.appearance", "appearance", self._build_appearance_page),
            ("settings.language", "language", self._build_language_page),
            ("settings.integration", "integration", self._build_integration_page),
            ("settings.advanced", "advanced", self._build_advanced_page),
        ]
        self._page_specs = pages
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (title_key, icon_name, builder) in enumerate(pages):
            button = QPushButton(self.translations.tr(title_key), sidebar)
            button.setObjectName("SettingsNavItem")
            button.setIcon(self._icon(icon_name))
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self.stack.setCurrentIndex(i))
            group.addButton(button)
            side_layout.addWidget(button)
            self._page_buttons.append(button)
            self.stack.addWidget(builder())
        self._page_buttons[0].setChecked(True)
        side_layout.addStretch(1)

        privacy = QLabel("全部本地处理\n照片、标签和分类数据不会上传", sidebar)
        privacy.setObjectName("PrivacyNote")
        privacy.setWordWrap(True)
        side_layout.addWidget(privacy)

        client_layout.addWidget(sidebar)
        client_layout.addWidget(self.stack, 1)

    def _set_window_frame_skin(self, skin: str):
        """Switch only the custom chrome; all settings surfaces stay opaque."""

        skin = skin if skin in {"", "macos", "win11", "win7", "win2000", "macos8"} else ""
        if skin == self._frame_skin:
            self.flavor_titlebar.set_skin(skin)
            self.flavor_titlebar.setVisible(skin in {"win11", "win7", "win2000"})
            self.macos8_titlebar.setVisible(skin == "macos8")
            self.modern_macos_titlebar.setVisible(skin == "macos")
            rim = 1 if skin == "win2000" else 0
            self._root_layout.setContentsMargins(rim, rim, rim, rim)
            return
        geometry = self.geometry()
        was_visible = self.isVisible()
        self._frame_skin = skin
        flags = self._standard_window_flags | (Qt.FramelessWindowHint if skin else Qt.Widget)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self.flavor_titlebar.set_skin(skin)
        self.flavor_titlebar.setVisible(skin in {"win11", "win7", "win2000"})
        self.macos8_titlebar.setVisible(skin == "macos8")
        self.modern_macos_titlebar.setVisible(skin == "macos")
        rim = 1 if skin == "win2000" else 0
        self._root_layout.setContentsMargins(rim, rim, rim, rim)
        if was_visible:
            self.setGeometry(geometry)
            self.show()
            self.raise_()
            self.activateWindow()
        self.update()

    def _page(self, title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        body = QWidget(area)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(34, 28, 34, 34)
        layout.setSpacing(16)
        heading = QLabel(title, body)
        heading.setObjectName("SettingsPageTitle")
        hint = QLabel(subtitle, body)
        hint.setObjectName("SettingsPageSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(2)
        area.setWidget(body)
        return area, layout

    def _card(self, layout: QVBoxLayout, title: str) -> QVBoxLayout:
        card = QFrame(self)
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 15, 18, 15)
        card_layout.setSpacing(0)
        label = QLabel(title, card)
        label.setObjectName("SettingsCardTitle")
        card_layout.addWidget(label)
        card_layout.addSpacing(8)
        layout.addWidget(card)
        return card_layout

    def _row(self, card: QVBoxLayout, title: str, description: str, control: QWidget) -> QWidget:
        row = QWidget(self)
        row.setObjectName("SettingsRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 9, 0, 9)
        row_layout.setSpacing(18)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_label = QLabel(title, row)
        title_label.setObjectName("SettingsRowTitle")
        desc_label = QLabel(description, row)
        desc_label.setObjectName("SettingsRowDescription")
        desc_label.setWordWrap(True)
        text_box.addWidget(title_label)
        if description:
            text_box.addWidget(desc_label)
        row_layout.addLayout(text_box, 1)
        row_layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        card.addWidget(row)
        return row

    def _combo(self, key: str, items: list[tuple[str, object]], width: int = 190) -> QComboBox:
        combo = QComboBox(self)
        combo.setMinimumWidth(width)
        for text, data in items:
            combo.addItem(text, data)
        combo.currentIndexChanged.connect(lambda _i, k=key, w=combo: self.settings.set(k, w.currentData()))
        self._bound_widgets[key] = (combo, "combo", None)
        return combo

    def _check(self, key: str) -> QCheckBox:
        checkbox = SwitchControl(self)
        checkbox.toggled.connect(lambda value, k=key: self.settings.set(k, bool(value)))
        self._bound_widgets[key] = (checkbox, "check", None)
        return checkbox

    def _spin(self, key: str, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        spin = AppleSpinBox(self)
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        spin.setMinimumWidth(120)
        spin.valueChanged.connect(lambda value, k=key: self.settings.set(k, int(value)))
        self._bound_widgets[key] = (spin, "spin", None)
        return spin

    def _line(self, key: str, width: int = 330) -> QLineEdit:
        line = QLineEdit(self)
        line.setMinimumWidth(width)
        line.editingFinished.connect(lambda k=key, w=line: self.settings.set(k, w.text().strip()))
        self._bound_widgets[key] = (line, "line", None)
        return line

    def _build_general_page(self) -> QWidget:
        page, layout = self._page("通用", "启动、浏览、扫描性能、删除安全与 Live Photo 的默认行为。")
        card = self._card(layout, "启动与浏览")
        self._row(card, "恢复上次文件夹", "启动后恢复最后使用的照片资料库。", self._check("general.restore_last_folder"))
        self._row(card, "启动时自动扫描", "恢复文件夹后立即开始扫描。", self._check("general.auto_scan_on_start"))
        self._row(card, "默认视图", "首次打开资料库时使用的呈现方式。", self._combo("general.default_view", [("照片墙", "grid"), ("表格", "table")]))
        self._row(card, "默认排序", "控制首次扫描后的默认顺序。", self._combo("general.default_sort", [("时间：旧到新", "time_asc"), ("时间：新到旧", "time_desc"), ("名称", "name")]))

        thumb_box = QWidget(self)
        thumb_layout = QHBoxLayout(thumb_box)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Horizontal, thumb_box)
        slider.setRange(0, 2)
        slider.setFixedWidth(150)
        thumb_label = QLabel("中", thumb_box)
        labels = ["小", "中", "大"]
        values = ["small", "medium", "large"]
        slider.valueChanged.connect(lambda i: thumb_label.setText(self.translations.text(labels[i])))
        slider.sliderReleased.connect(lambda: self.settings.set("general.thumbnail_size", values[slider.value()]))
        thumb_layout.addWidget(slider)
        thumb_layout.addWidget(thumb_label)
        self._bound_widgets["general.thumbnail_size"] = (slider, "slider", values)
        self._row(card, "缩略图尺寸", "照片墙缩略图的默认显示大小。", thumb_box)

        scan = self._card(layout, "扫描与性能")
        self._row(scan, "递归子文件夹", "扫描所选目录下的全部子目录。", self._check("scan.recursive"))
        self._row(scan, "工作线程", "0 表示根据当前设备自动选择。", self._spin("scan.workers", 0, 16))
        self._row(scan, "缩略图缓存上限", "达到上限后优先清理较旧缓存。", self._spin("scan.thumbnail_cache_mb", 128, 32768, " MB"))
        excludes = QLineEdit(self)
        excludes.setMinimumWidth(300)
        excludes.setPlaceholderText("例如：.git; node_modules; *_cache")
        excludes.editingFinished.connect(
            lambda w=excludes: self.settings.set(
                "scan.exclude_patterns",
                [part.strip() for part in w.text().split(";") if part.strip()],
            )
        )
        self._bound_widgets["scan.exclude_patterns"] = (excludes, "list_line", None)
        self._row(scan, "排除规则", "使用分号分隔文件夹名或通配符。", excludes)
        cache_button = QPushButton("清除缩略图缓存", self)
        cache_button.clicked.connect(lambda: self.action_requested.emit("clear_thumbnail_cache", None))
        self._row(scan, "缓存维护", "立即删除已生成的缩略图缓存。", cache_button)

        safety = self._card(layout, "删除与安全")
        self._row(safety, "删除行为", "推荐保留应用内垃圾箱以便恢复。", self._combo("deletion.behavior", [("应用内垃圾箱", "app_trash"), ("系统回收站", "system_trash"), ("直接删除", "permanent")]))
        self._row(safety, "垃圾箱自动清理", "0 表示永不自动清理。", self._spin("deletion.auto_cleanup_days", 0, 365, " 天"))
        self._row(safety, "危险操作二次确认", "直接删除、覆盖导出等操作再次确认。", self._check("deletion.confirm_dangerous"))

        live = self._card(layout, "Live Photo")
        self._row(live, "悬停自动播放", "鼠标停留在实况照片上时开始播放。", self._check("live_photo.hover_play"))
        self._row(live, "悬停延迟", "减少快速划过照片时的解码开销。", self._spin("live_photo.hover_delay_ms", 0, 2000, " ms"))
        self._row(live, "播放声音", "当前预览解码链默认只读取画面。", self._check("live_photo.play_sound"))
        layout.addStretch(1)
        return page

    def _build_appearance_page(self) -> QWidget:
        page, layout = self._page("外观", "主题、标题栏和强调色都会即时应用，无需重新启动。")
        card = self._card(layout, "界面")
        self._row(
            card,
            "主题",
            "跟随系统时监听 Windows 的 AppsUseLightTheme。",
            self._combo(
                "appearance.theme",
                [
                    ("跟随系统", "system"),
                    ("浅色", "light"),
                    ("深色", "dark"),
                    ("Windows 11", "win11"),
                    ("Windows 7", "win7"),
                    ("Windows 2000", "win2000"),
                    ("Mac OS 8", "macos8"),
                ],
            ),
        )
        self.titlebar_style_row = self._row(
            card,
            "标题栏风格",
            "在 macOS 红绿灯与 Windows 按钮布局之间切换。",
            self._combo("appearance.titlebar_style", [("macOS 红绿灯", "macos"), ("Windows 按钮", "windows")]),
        )

        accent_box = QWidget(self)
        accent_layout = QHBoxLayout(accent_box)
        accent_layout.setContentsMargins(0, 0, 0, 0)
        accent_layout.setSpacing(2)
        self.accent_group = QButtonGroup(self)
        self.accent_group.setExclusive(True)
        self.accent_buttons: dict[str, AccentButton] = {}
        for name, color in ACCENTS.items():
            button = AccentButton(name, color, accent_box)
            button.clicked.connect(lambda _checked=False, value=name: self.settings.set("appearance.accent", value))
            self.accent_group.addButton(button)
            self.accent_buttons[name] = button
            accent_layout.addWidget(button)
        self.accent_row = self._row(card, "强调色", "用于选中状态、按钮、进度和焦点描边。", accent_box)
        layout.addStretch(1)
        return page

    def _build_language_page(self) -> QWidget:
        page, layout = self._page("语言", "设置窗口与主界面支持即时语言切换。")
        card = self._card(layout, "界面语言")
        language = self._combo("language.locale", [("跟随系统", "system"), ("简体中文", "zh_CN"), ("繁體中文", "zh_TW"), ("English", "en")], 220)
        self._row(card, "语言", "日期、文件大小和新界面文本使用所选区域设置。", language)
        self._row(card, "即时切换", "关闭时将在下次启动后完整应用语言。", self._check("language.hot_reload"))
        note = QLabel("所有设置页面和主界面常驻控件均会即时切换语言；个别文件操作结果保留原始文件名。", self)
        note.setObjectName("SettingsInfoNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _path_picker(self, key: str, detect: bool = False) -> QWidget:
        box = QWidget(self)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        line = self._line(key, 280)
        browse = QPushButton("浏览…", box)
        browse.clicked.connect(lambda: self._browse_executable(line, key))
        layout.addWidget(line)
        layout.addWidget(browse)
        if detect:
            button = QPushButton("自动检测", box)
            button.clicked.connect(self._detect_lightroom)
            layout.addWidget(button)
        return box

    def _directory_picker(self, key: str, width: int = 280) -> QWidget:
        box = QWidget(self)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        line = self._line(key, width)
        browse = QPushButton("浏览…", box)
        browse.clicked.connect(lambda: self._browse_directory(line, key))
        layout.addWidget(line, 1)
        layout.addWidget(browse)
        return box

    def _build_integration_page(self) -> QWidget:
        page, layout = self._page("集成", "从照片右键菜单调用桌面编辑器和 Windows 打开方式。")
        card = self._card(layout, "外部应用")
        self._row(card, "Adobe Lightroom", "支持注册表、常见安装目录检测和手动指定。", self._path_picker("integration.lightroom_path", True))
        self._row(card, "Adobe Photoshop", "指定 Photoshop 可执行文件。", self._path_picker("integration.photoshop_path"))
        self._row(card, "系统默认查看器", "在右键菜单中显示“用默认应用打开”。", self._check("integration.default_viewer"))
        hint = QLabel("选择照片后，右键菜单可在 Lightroom、Photoshop、系统默认查看器中打开，或在资源管理器中定位。所有操作只传递本地文件路径。", self)
        hint.setObjectName("SettingsInfoNote")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_advanced_page(self) -> QWidget:
        page, layout = self._page("高级", "分类、导出、数据维护以及版本信息。")
        classification = self._card(layout, "智能分类")
        for key, title in (("time", "时间"), ("media", "媒体类型"), ("device", "拍摄设备"), ("location", "GPS 位置"), ("file", "文件状态"), ("plus", "Plus 分析")):
            self._row(classification, title, "关闭后重建分类缓存即可移除该规则结果。", self._check(f"classification.rules.{key}"))
        self._row(classification, "大文件阈值", "文件状态分类使用的容量阈值。", self._spin("classification.large_file_mb", 1, 102400, " MB"))
        rebuild = QPushButton("重建分类缓存", self)
        rebuild.clicked.connect(lambda: self.action_requested.emit("rebuild_classification", None))
        self._row(classification, "内容识别", "使用完全位于本机的视觉模型生成苹果、桌子等标签。", self._check("classification.ai_enabled"))
        self._row(classification, "本地模型目录", "兼容 Transformers 的本地图像分类模型；程序不会上传照片。", self._directory_picker("classification.content_model_path", 280))
        self._row(classification, "标签置信度", "低于该置信度的自动标签会被忽略。", self._spin("classification.content_confidence_percent", 1, 100, "%"))
        self._row(classification, "分类缓存", "丢弃旧快照并重新执行当前启用规则。", rebuild)

        export = self._card(layout, "导出")
        directory = self._line("export.default_directory", 300)
        choose = QPushButton("浏览…", self)
        choose.clicked.connect(lambda: self._browse_directory(directory, "export.default_directory"))
        dir_box = QWidget(self)
        dir_layout = QHBoxLayout(dir_box)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.addWidget(directory)
        dir_layout.addWidget(choose)
        self._row(export, "默认导出目录", "导出对话框优先从此目录开始。", dir_box)
        self._row(export, "DCF 起始编号", "IMG_0001 对应 1。", self._spin("export.dcf_start", 1, 9999))
        self._row(export, "冲突策略", "目标文件已存在时的默认处理。", self._combo("export.conflict_policy", [("跳过", "skip"), ("自动重命名", "rename"), ("覆盖", "overwrite")]))

        data = self._card(layout, "数据与诊断")
        open_data = QPushButton("打开数据文件夹", self)
        open_data.clicked.connect(lambda: self.action_requested.emit("open_data_folder", None))
        self._row(data, "应用数据", "打开 Pictessera_Data。", open_data)
        self._row(data, "日志级别", "更详细的日志可能增加磁盘写入。", self._combo("advanced.log_level", [("错误", "ERROR"), ("警告", "WARNING"), ("信息", "INFO"), ("调试", "DEBUG")]))

        buttons = QWidget(self)
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        export_settings = QPushButton("导出设置…", buttons)
        import_settings = QPushButton("导入设置…", buttons)
        reset = QPushButton("恢复默认设置", buttons)
        export_settings.clicked.connect(self._export_settings)
        import_settings.clicked.connect(self._import_settings)
        reset.clicked.connect(self._reset_settings)
        buttons_layout.addWidget(export_settings)
        buttons_layout.addWidget(import_settings)
        buttons_layout.addWidget(reset)
        self._row(data, "设置备份", "JSON 文件可用于迁移或恢复。", buttons)

        about = self._card(layout, "关于")
        version = QLabel(f"照片资料库  {__version__}\n全部照片分析和设置数据均在本地处理。", self)
        version.setObjectName("SettingsAbout")
        version.setWordWrap(True)
        about.addWidget(version)
        layout.addStretch(1)
        return page

    def _browse_executable(self, line: QLineEdit, key: str):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.translations.text("选择应用程序"),
            line.text(),
            self.translations.text("应用程序 (*.exe);;所有文件 (*)"),
        )
        if path:
            line.setText(path)
            self.settings.set(key, path)

    def _browse_directory(self, line: QLineEdit, key: str):
        path = QFileDialog.getExistingDirectory(self, self.translations.text("选择文件夹"), line.text())
        if path:
            line.setText(path)
            self.settings.set(key, path)

    def _detect_lightroom(self):
        path = detect_lightroom_path()
        if path:
            self.settings.set("integration.lightroom_path", path)
            QMessageBox.information(self, "Lightroom", f"{self.translations.text('已找到：')}\n{path}")
        else:
            QMessageBox.information(self, "Lightroom", self.translations.text("未在注册表或常见安装目录中找到 Lightroom。"))

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, self.translations.text("导出设置"), "photo-manager-settings.json", "JSON (*.json)")
        if path:
            self.settings.export_to(path)

    def _import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, self.translations.text("导入设置"), "", "JSON (*.json)")
        if not path:
            return
        try:
            self.settings.import_from(path)
        except Exception as exc:
            QMessageBox.warning(self, self.translations.text("导入失败"), str(exc))

    def _reset_settings(self):
        reply = QMessageBox.question(
            self,
            self.translations.text("恢复默认设置"),
            self.translations.text("确定恢复全部默认设置吗？"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.settings.reset_defaults()

    def sync_all(self):
        for key in list(self._bound_widgets):
            self._sync_widget(key)
        accent = str(self.settings.get("appearance.accent", "blue"))
        if accent in self.accent_buttons:
            self.accent_buttons[accent].setChecked(True)

    def _sync_widget(self, key: str):
        entry = self._bound_widgets.get(key)
        if entry is None:
            return
        widget, kind, extra = entry
        value = self.settings.get(key)
        widget.blockSignals(True)
        try:
            if kind == "check":
                widget.setChecked(bool(value))
            elif kind == "combo":
                index = widget.findData(value)
                widget.setCurrentIndex(max(0, index))
            elif kind == "spin":
                widget.setValue(int(value or 0))
            elif kind == "line":
                widget.setText(str(value or ""))
            elif kind == "list_line":
                widget.setText("; ".join(str(part) for part in (value or [])))
            elif kind == "slider":
                values = list(extra)
                widget.setValue(values.index(value) if value in values else 1)
        finally:
            widget.blockSignals(False)

    def _on_external_setting_change(self, key: str, value):
        if key == "language.locale":
            self.translations.set_locale(str(value))
        if key == "appearance.accent" and str(value) in self.accent_buttons:
            self.accent_buttons[str(value)].setChecked(True)
        if key.startswith("appearance."):
            self._install_style()
        self._sync_widget(key)

    def retranslate(self):
        self.setWindowTitle(self.translations.tr("settings.title"))
        if hasattr(self, "flavor_titlebar"):
            self.flavor_titlebar.set_title(self.windowTitle())
        if hasattr(self, "macos8_titlebar"):
            self.macos8_titlebar.set_title(self.windowTitle())
        if hasattr(self, "modern_macos_titlebar"):
            self.modern_macos_titlebar.set_title(self.windowTitle())
        self.sidebar_title.setText(self.translations.tr("settings.title"))
        for button, (title_key, _icon, _builder) in zip(self._page_buttons, self._page_specs):
            button.setText(self.translations.tr(title_key))
        # Page widgets keep their Simplified-Chinese source in a dynamic
        # property. This makes repeated zh_CN -> en -> zh_TW switches lossless.
        roots = [self.stack]
        privacy = self.findChild(QLabel, "PrivacyNote")
        if privacy is not None:
            roots.append(privacy)
        for root in roots:
            text_widgets = [root] if isinstance(root, (QLabel, QPushButton)) else (
                root.findChildren(QLabel) + root.findChildren(QPushButton)
            )
            for widget in text_widgets:
                source = widget.property("i18nSourceText")
                if source is None:
                    source = widget.text()
                    widget.setProperty("i18nSourceText", source)
                widget.setText(self.translations.text(str(source)))
            combos = root.findChildren(QComboBox) if not isinstance(root, QComboBox) else [root]
            for combo in combos:
                for index in range(combo.count()):
                    source = combo.itemData(index, Qt.UserRole + 41)
                    if source is None:
                        source = combo.itemText(index)
                        combo.setItemData(index, source, Qt.UserRole + 41)
                    combo.setItemText(index, self.translations.text(str(source)))
            lines = root.findChildren(QLineEdit) if not isinstance(root, QLineEdit) else [root]
            for line in lines:
                source = line.property("i18nSourcePlaceholder")
                if source is None:
                    source = line.placeholderText()
                    line.setProperty("i18nSourcePlaceholder", source)
                if source:
                    line.setPlaceholderText(self.translations.text(str(source)))

    def _refresh_theme_icons(self, profile):
        for button, (_title_key, icon_name, _builder) in zip(self._page_buttons, self._page_specs):
            button.setIcon(self._icon(icon_name) if profile.uses_modern_icons else QIcon())
            button.setIconSize(QSize(18 if profile.control_style == "win11" else 20, 18 if profile.control_style == "win11" else 20))

    def _install_style(self):
        theme = str(self.settings.get("appearance.theme", "system"))
        system_dark = not windows_apps_use_light_theme()
        requested_accent = ACCENTS.get(str(self.settings.get("appearance.accent", "blue")), "#007AFF")
        profile = resolve_theme_profile(theme, system_dark=system_dark, accent=requested_accent)
        requested_titlebar = str(self.settings.get("appearance.titlebar_style", "macos"))
        skin = profile.titlebar_skin if profile.is_flavor else ("win11" if requested_titlebar == "windows" else "macos")
        self._set_window_frame_skin(skin)
        accent = profile.accent
        bg, sidebar, card = profile.app_bg, profile.sidebar, profile.panel
        card_border, text, muted = profile.border, profile.text, profile.muted
        control = profile.content if profile.control_style != "apple" else profile.panel
        hover = profile.gray_2 if profile.control_style in {"win7", "win2000", "macos8"} else profile.gray_1

        # Scroll areas and stacked widgets default to transparent child
        # surfaces, so always give every settings client viewport a solid base.
        client_surface = QColor(bg)
        for widget in (self.settings_client, self.stack):
            palette = widget.palette()
            palette.setColor(QPalette.Window, client_surface)
            palette.setColor(QPalette.Base, client_surface)
            widget.setPalette(palette)
            widget.setAutoFillBackground(True)
        for area in self.findChildren(QScrollArea):
            viewport = area.viewport()
            palette = viewport.palette()
            palette.setColor(QPalette.Window, client_surface)
            palette.setColor(QPalette.Base, client_surface)
            viewport.setPalette(palette)
            viewport.setAutoFillBackground(True)

        settings_font_size = theme_display_point_size(
            theme, self.translations.locale, 9 if profile.is_flavor else 10
        )
        ui_font = make_theme_font(theme, self.translations.locale, settings_font_size)
        self.setFont(ui_font)
        self.modern_macos_titlebar.set_dark(profile.theme_id == "dark")
        self._refresh_theme_icons(profile)
        if hasattr(self, "accent_row"):
            self.accent_row.setVisible(not profile.fixed_accent)
        if hasattr(self, "titlebar_style_row"):
            self.titlebar_style_row.setVisible(not profile.is_flavor)
        for switch in self.findChildren(SwitchControl):
            switch.accent = accent
            switch.set_visual_style(profile.control_style)
        for spin in self.findChildren(AppleSpinBox):
            spin.set_visual_style(profile.control_style)
        for button in self.findChildren(AccentButton):
            button.set_visual_style(profile.control_style)

        custom_stepper = profile.control_style == "apple"
        stepper_style = (
            "QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; border: none; background: transparent; }"
            if custom_stepper
            else f"QSpinBox::up-button, QSpinBox::down-button {{ width: 17px; background: {profile.gray_2}; border-left: 1px solid {card_border}; }}"
        )
        slider_radius = 0 if profile.corner_style == "square" else (1 if profile.control_style == "win7" else 2)
        handle_radius = 0 if profile.corner_style == "square" else (2 if profile.control_style == "win7" else 9)
        nav_padding = 8 if profile.icon_policy == "text" else 12
        weight = 600 if profile.control_style not in {"win2000", "macos8"} else 500
        scrollbar_extent = 16 if profile.control_style in {"win2000", "macos8"} else (17 if profile.control_style == "win7" else 8)
        scrollbar_radius = 0 if profile.corner_style == "square" else min(4, profile.control_radius)

        flavor_extra = ""
        if profile.control_style == "win11":
            flavor_extra += "QPushButton { border-radius: 4px; } QComboBox, QLineEdit, QSpinBox { border-radius: 4px; }"
        elif profile.control_style == "win7":
            flavor_extra += (
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #FFFFFF,stop:0.48 #F3F8FC,stop:0.52 #E5F0F8,stop:1 #D3E4F1); border:1px solid #7F9DB9; }"
                "QPushButton:hover { border-color:#3C7FB1; background:#EAF6FD; }"
            )
        elif profile.uses_bevels:
            dark_edge = "#333333" if profile.control_style == "macos8" else "#404040"
            flavor_extra += (
                "QPushButton { border-top: 2px solid #FFFFFF; border-left: 2px solid #FFFFFF; "
                f"border-right: 2px solid {dark_edge}; border-bottom: 2px solid {dark_edge}; }}"
                f"QPushButton:pressed {{ border-top-color: {dark_edge}; border-left-color: {dark_edge}; border-right-color: #FFFFFF; border-bottom-color: #FFFFFF; }}"
                "QComboBox, QLineEdit, QSpinBox { border-top: 2px solid #555555; border-left: 2px solid #555555; border-right: 2px solid #FFFFFF; border-bottom: 2px solid #FFFFFF; }"
            )
        macos8_extra = ""
        if profile.theme_id == "macos8":
            macos8_extra = (
                "#MacOS8SettingsTitleBar { background: #DDDDDD; border-bottom: 1px solid #777777; }"
                "#MacOS8SettingsCaption { background: #DDDDDD; color: #000000; padding: 0 9px; font-weight: 500; }"
                "#SettingsClient { background: #DDDDDD; border: 1px solid #777777; }"
                "#SettingsSidebar { background: #DDDDDD; border-right: 1px solid #777777; }"
                "QScrollArea, QScrollArea > QWidget > QWidget { background: #DDDDDD; }"
            )

        title_caption = "#FFFFFF" if profile.titlebar_skin == "win2000" else "#1D1D1F"
        modern_caption = "#F5F5F7" if profile.theme_id == "dark" else "#1D1D1F"
        caption_size = theme_display_point_size(theme, self.translations.locale, 9)
        dialog_background = bg
        self.setStyleSheet(
            f"QDialog {{ background: {dialog_background}; color: {text}; }}"
            f"#FlavorSettingsCaption {{ background: transparent; color: {title_caption}; font-size: {caption_size}pt; font-weight: 600; padding-left: 2px; }}"
            f"#ModernMacSettingsCaption {{ background: transparent; color: {modern_caption}; font-size: 10pt; font-weight: 600; padding: 0 8px; }}"
            f"#SettingsClient QWidget {{ font-size: {settings_font_size}pt; }}"
            f"#SettingsClient {{ background: {bg}; }}"
            f"#SettingsSidebar {{ background: {sidebar}; border-right: 1px solid {card_border}; }}"
            "#SettingsSidebarTitle { font-size: 22px; font-weight: 700; padding: 2px 8px; }"
            f"QPushButton#SettingsNavItem {{ min-height: 38px; text-align: left; padding: 0 {nav_padding}px; border: none; border-radius: {profile.nav_radius}px; background: transparent; font-weight: {weight}; }}"
            f"QPushButton#SettingsNavItem:hover {{ background: {hover}; }}"
            f"QPushButton#SettingsNavItem:checked {{ background: {accent}; color: white; }}"
            f"#PrivacyNote {{ color: {muted}; font-size: 11px; padding: 12px 8px; }}"
            f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: {bg}; border: none; color: {text}; }}"
            "#SettingsPageTitle { font-size: 24px; font-weight: 700; }"
            f"#SettingsPageSubtitle {{ color: {muted}; font-size: 12px; }}"
            f"#SettingsCard {{ background: {card}; border: 1px solid {card_border}; border-radius: {profile.card_radius}px; }}"
            f"#SettingsCardTitle {{ color: {muted}; font-size: 11px; font-weight: 700; }}"
            f"#SettingsRow {{ border-top: 1px solid {profile.gray_3}; }}"
            f"#SettingsRowTitle {{ font-size: 13px; font-weight: {weight}; }}"
            f"#SettingsRowDescription {{ color: {muted}; font-size: 11px; }}"
            f"#SettingsInfoNote, #SettingsAbout {{ color: {muted}; background: {card}; border-radius: {profile.card_radius}px; padding: 14px; }}"
            f"QComboBox, QLineEdit, QSpinBox {{ min-height: 32px; padding: 0 10px; background: {control}; color: {text}; border: 1px solid {card_border}; border-radius: {profile.control_radius}px; }}"
            + stepper_style
            + f"QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {accent}; }}"
            f"QPushButton {{ min-height: 32px; padding: 0 13px; background: {control}; color: {text}; border: 1px solid {card_border}; border-radius: {profile.control_radius}px; font-weight: {weight}; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QSlider::groove:horizontal {{ height: 4px; background: {profile.gray_3}; border-radius: {slider_radius}px; }}"
            f"QSlider::sub-page:horizontal {{ background: {accent}; border-radius: {slider_radius}px; }}"
            f"QSlider::handle:horizontal {{ width: 18px; margin: -7px 0; background: {control}; border: 1px solid {profile.gray_4}; border-radius: {handle_radius}px; }}"
            f"QScrollBar:vertical {{ width: {scrollbar_extent}px; background: {profile.panel_2}; margin: 0; }}"
            f"QScrollBar::handle:vertical {{ min-height: 28px; background: {profile.gray_3}; border: 1px solid {profile.border}; border-radius: {scrollbar_radius}px; }}"
            f"QScrollBar:horizontal {{ height: {scrollbar_extent}px; background: {profile.panel_2}; margin: 0; }}"
            f"QScrollBar::handle:horizontal {{ min-width: 28px; background: {profile.gray_3}; border: 1px solid {profile.border}; border-radius: {scrollbar_radius}px; }}"
            + flavor_extra
            + macos8_extra
        )
