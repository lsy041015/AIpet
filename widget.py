#!/usr/bin/env python3
"""Compact desktop quota widget for the existing local CLI accounts."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime

# This widget does no parallel numerical work; avoid NumPy/BLAS helper pools.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

# Wayland cannot position top-level windows; the pet and its bubble need global coordinates.
if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent, QSettings, QUrl, QSize, QRectF, QPointF, QVariantAnimation, QEasingCurve, QProcess, QProcessEnvironment
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QColor, QPainter, QPen, QPalette, QPixmap, QLinearGradient, QPainterPath
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QProgressBar, QScrollArea,
                               QFrame, QButtonGroup, QToolButton, QMenu, QSizeGrip,
                               QGraphicsDropShadowEffect, QSlider, QWidgetAction,
                               QInputDialog, QMessageBox, QStackedWidget, QSizePolicy, QGridLayout)

from providers import PROVIDERS, QuotaError, executable, claude_max_dir, claude_environment
from calendar_panel import CalendarPanel
from billing import BillingDialog, payment_summary, next_payment
from notifications import Notifications
from agent_activity import read_activity, ActivityTracker
from agent_windows import XWindows
from pixel_pet import paint_computer, animation_row, display_row, DURATIONS, ANIMATION_NAMES, atlas
from html import escape


APP_NAME = "check AI"

WEB_APPS = {
    "github": ("GitHub", "https://github.com/"),
}

SERVICES = {
    "codex": ("GPT / Codex", "ChatGPT 구독 · Codex 한도", "#74d9b3", "https://chatgpt.com/codex/settings/usage"),
    "claude": ("Claude Code", "기본 CLI 계정 · 세션·주간 잔여량", "#d97757", "https://claude.ai/settings/usage"),
    "claude_max": ("Claude · Max 5x", "개인 구독 · 별도 계정", "#d97757", "https://claude.ai/settings/usage"),
    "antigravity": ("Antigravity", "모델 그룹별 사용한도", "#94b8ff", "https://antigravity.google/"),
    "copilot": ("GitHub Copilot", "크레딧·요청 잔여량", "#cfacff", "https://github.com/settings/copilot"),
}

THEMES = {
    "dark": {"background": "#171b24", "card": "#252c38", "text": "#f5f5f7",
             "muted": "#aeaeb8", "track": "#414146", "control": "#363639",
             "selected": "#485364", "border": "#384353", "warning": "#ffb970", "danger": "#ff8a80",
             "codex": "#66dfb5", "antigravity": "#91bcff", "copilot": "#d4b3ff", "claude": "#f29c7b", "claude_max": "#f29c7b"},
    "light": {"background": "#f2f2f7", "card": "#ffffff", "text": "#1c1c1e",
              "muted": "#62626c", "track": "#e6e6ec", "control": "#e4e4eb",
              "selected": "#ffffff", "border": "#d3d3dd", "warning": "#a04a00", "danger": "#c62828",
              "codex": "#087e61", "antigravity": "#185ec7", "copilot": "#7b3bb4", "claude": "#a34224", "claude_max": "#a34224"},
}

PLAN_NAMES = {
    "claude": {"pro": "Pro", "max": "Max", "team": "Business / Team", "enterprise": "Enterprise"},
    "codex": {"free": "Free", "plus": "Plus", "pro": "Pro", "team": "Team",
              "business": "Business", "enterprise": "Enterprise", "edu": "Edu"},
    "copilot": {"free": "Copilot Free", "individual": "Copilot Pro",
                "individual_pro": "Copilot Pro+", "business": "Copilot Business",
                "enterprise": "Copilot Enterprise"},
}


def label(text, role=""):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    item.setWordWrap(True)
    if role:
        item.setObjectName(role)
    return item


def risk_level(remaining):
    if remaining is None:
        return ""
    return "critical" if remaining <= 10 else "low" if remaining <= 20 else ""


def time_left(reset_at, now=None):
    if reset_at is None:
        return ""
    seconds = reset_at - (time.time() if now is None else now)
    if seconds <= 0:
        return "곧 초기화"
    minutes = math.ceil(seconds / 60)
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    if days:
        return f"{days}일 {hours}시간 후" if hours else f"{days}일 후"
    if hours:
        return f"{hours}시간 {mins}분 후" if mins else f"{hours}시간 후"
    return f"{mins}분 후"


def reset_line(row, now=None):
    return " · ".join(v for v in (row.get("detail"), row["reset"], time_left(row.get("reset_at"), now)) if v)


def set_risk(item, level):
    if item.property("risk") != level:
        item.setProperty("risk", level)
        item.style().unpolish(item)
        item.style().polish(item)


def logo_asset(key):
    asset_key = "claude" if key == "claude_max" else key
    return Path(__file__).parent / "assets" / (asset_key + (".png" if key == "antigravity" else ".svg"))


def motion_curve():
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.23, 1), QPointF(0.32, 1), QPointF(1, 1))
    return curve


class PageFade(QWidget):
    """Fade the previous snapshot without nesting graphics effects under the window shadow."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.snapshot = QPixmap()
        self.progress = 1.0
        self.hide()

    def opacity(self):
        return self.progress

    def setOpacity(self, value):
        self.progress = float(value)
        self.setVisible(self.progress < 1.0)
        if self.progress >= 1.0:
            self.snapshot = QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setOpacity(1.0 - self.progress)
        painter.drawPixmap(0, 0, self.snapshot)


class FoldTransition(QWidget):
    """Input-transparent snapshot reveals toward the circle without relaying out live UI."""
    def __init__(self, owner):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.owner = owner
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(200)
        self.motion.setEasingCurve(motion_curve())
        self.motion.valueChanged.connect(self.advance)
        self.motion.finished.connect(self.finish)
        self.progress = 1.0

    def begin(self, snapshot, large_rect, small_rect, expanding):
        self.snapshot = snapshot
        self.large = QRectF(large_rect)
        self.small = QRectF(small_rect)
        self.expanding = expanding
        self.setGeometry(large_rect.united(small_rect))
        self.owner.setWindowOpacity(0)
        if expanding:
            # The revealed window stays invisible until finish(); a double-click must not reach it.
            self.owner.windowHandle().installEventFilter(self)
        self.motion.setStartValue(0.0)
        self.motion.setEndValue(1.0)
        self.progress = 0.0
        self.show()
        self.motion.start()

    def advance(self, value):
        self.progress = float(value)
        self.update()

    def finish(self):
        self.motion.stop()
        if self.owner.windowHandle():
            self.owner.windowHandle().removeEventFilter(self)
        self.hide()
        self.snapshot = QPixmap()
        self.owner.setWindowOpacity(1.0 if self.owner.collapsed else 1 - self.owner.opacity_slider.value() / 100)

    def eventFilter(self, watched, event):
        return event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
                                QEvent.MouseMove, QEvent.Wheel)

    def paintEvent(self, event):
        amount = self.progress if self.expanding else 1 - self.progress
        origin = QPointF(self.pos())
        rect = QRectF(self.small.topLeft() + (self.large.topLeft() - self.small.topLeft()) * amount,
                      self.small.size() + (self.large.size() - self.small.size()) * amount)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(rect.translated(-origin), 28, 28)
        p.setClipPath(clip)
        p.setOpacity((.7 + .3 * amount) * (1 - self.owner.opacity_slider.value() / 100))
        p.drawPixmap(self.large.translated(-origin), self.snapshot, QRectF(self.snapshot.rect()))


class AppIconButton(QToolButton):
    """Native accessible button; only its painted icon scales, never the layout."""
    def __init__(self, page, owner):
        super().__init__(owner)
        self.page = page
        self.owner = owner
        self.scale = 1.0
        self.pointer_activation = False
        self.colors = THEMES["dark"]
        self.setFixedSize(100, 104)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.motion = QVariantAnimation(self)
        self.motion.setDuration(160)
        self.motion.setEasingCurve(motion_curve())
        self.motion.valueChanged.connect(self.set_scale)

    def set_scale(self, value):
        self.scale = float(value)
        self.update()

    def animate_scale(self, target):
        self.motion.stop()
        if self.owner.reduce_motion.isChecked():
            self.set_scale(1.0)
            return
        self.motion.setStartValue(self.scale)
        self.motion.setEndValue(target)
        self.motion.start()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self.isDown():
            self.animate_scale(0.97)

    def mouseReleaseEvent(self, event):
        self.pointer_activation = True
        super().mouseReleaseEvent(event)
        self.pointer_activation = False
        self.animate_scale(1.0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(50, 40)
        p.scale(self.scale, self.scale)
        p.translate(-50, -40)
        box = QRectF(18, 8, 64, 64)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawRoundedRect(box.translated(0, 3), 16, 16)
        gradient = QLinearGradient(18, 8, 82, 72)
        gradient.setColorAt(0, QColor("#59c4ff" if self.page == "quota" else "#ffffff"))
        gradient.setColorAt(1, QColor("#2858df" if self.page == "quota" else "#e9edf5"))
        if self.page == "github":
            gradient.setColorAt(0, QColor("#39414b"))
            gradient.setColorAt(1, QColor("#15191e"))
        p.setBrush(gradient)
        p.drawRoundedRect(box, 16, 16)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 65), 1))
        p.drawRoundedRect(box.adjusted(.5, .5, -.5, -.5), 16, 16)
        if self.page == "quota":
            p.setPen(QPen(QColor("#ffffff"), 6, Qt.SolidLine, Qt.RoundCap))
            for x, top in ((34, 45), (50, 34), (66, 23)):
                p.drawLine(x, top, x, 55)
        elif self.page == "calendar":
            font = QFont(self.font())
            font.setPixelSize(10)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#d52c3d"))
            p.drawText(QRectF(18, 15, 64, 16), Qt.AlignCenter, "오늘")
            font.setPixelSize(32)
            font.setWeight(QFont.Medium)
            p.setFont(font)
            p.setPen(QColor("#202127"))
            p.drawText(QRectF(18, 28, 64, 39), Qt.AlignCenter, str(datetime.now().day))
        elif self.page == "github":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            cat = QPainterPath()
            cat.moveTo(32, 33)
            cat.lineTo(32, 23)
            cat.lineTo(41, 27)
            cat.cubicTo(47, 25, 53, 25, 59, 27)
            cat.lineTo(68, 23)
            cat.lineTo(68, 33)
            cat.cubicTo(74, 47, 62, 51, 55, 51)
            cat.cubicTo(58, 53, 58, 57, 58, 62)
            cat.lineTo(43, 62)
            cat.cubicTo(43, 57, 43, 53, 46, 51)
            cat.cubicTo(33, 51, 27, 43, 32, 33)
            p.drawPath(cat)
            p.setPen(QPen(QColor("#ffffff"), 4, Qt.SolidLine, Qt.RoundCap))
            tail = QPainterPath(QPointF(45, 56))
            tail.cubicTo(31, 59, 35, 48, 28, 48)
            p.drawPath(tail)
        if self.isDown():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 25))
            p.drawRoundedRect(box, 16, 16)
        if self.hasFocus() and self.testAttribute(Qt.WA_KeyboardFocusChange):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(self.colors["antigravity"]), 2,
                          Qt.DashLine if self.hasFocus() else Qt.SolidLine))
            p.drawRoundedRect(box.adjusted(-4, -4, 4, 4), 20, 20)
        p.resetTransform()
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Medium)
        p.setFont(font)
        p.setPen(QColor(self.colors["text"]))
        p.drawText(QRectF(0, 81, 100, 20), Qt.AlignCenter, self.text())


class CircleButton(AppIconButton):
    """Share the app icons' interruptible press feedback and native button behavior."""
    def __init__(self, owner, compact=False):
        super().__init__("circle", owner)
        self.compact = compact
        self.drag_start = None
        self.dragging = False
        self.setFixedSize(28 if compact else 44, 28 if compact else 44)

    def mousePressEvent(self, event):
        if not self.compact and event.button() == Qt.LeftButton:
            self.owner.fold_transition.finish()
            self.drag_start = event.globalPosition().toPoint()
            self.drag_last = self.drag_start
            self.drag_origin = self.owner.pos()
            self.dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPosition().toPoint() - self.drag_start
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self.dragging = True
            if self.dragging:
                step = event.globalPosition().toPoint() - self.drag_last
                if abs(step.x()) >= 4:
                    self.drag_left = step.x() < 0
                self.drag_last = event.globalPosition().toPoint()
                self.setDown(False)
                self.animate_scale(1.0)
                position = self.drag_origin + delta
                screen = QApplication.screenAt(event.globalPosition().toPoint()) or self.owner.screen()
                area = screen.availableGeometry()
                position.setX(max(area.left(), min(position.x(), area.right() - self.owner.width() + 1)))
                position.setY(max(area.top(), min(position.y(), area.bottom() - self.owner.height() + 1)))
                self.owner.move(position)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        if self.dragging:
            self.dragging = False
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center = self.width() / 2
        p.translate(center, center)
        p.scale(self.scale, self.scale)
        radius = center - 3
        rect = QRectF(-radius, -radius, radius * 2, radius * 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawEllipse(rect.translated(0, 2))
        fill = QLinearGradient(0, -radius, 0, radius)
        fill.setColorAt(0, QColor("#ffffff"))
        fill.setColorAt(1, QColor("#dfe5ed" if self.isDown() else "#edf0f5"))
        p.setBrush(fill)
        p.setPen(QPen(QColor("#bac3cf"), 1))
        p.drawEllipse(rect)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawEllipse(rect.adjusted(1.5, 1.5, -1.5, -1.5))
        p.setPen(QPen(QColor("#65748b"), 1.6, Qt.SolidLine, Qt.RoundCap))
        if self.compact:
            p.drawLine(QPointF(-3, 0), QPointF(3, 0))
        else:
            p.drawRoundedRect(QRectF(-5, -5, 10, 10), 2, 2)
        if self.hasFocus() and self.testAttribute(Qt.WA_KeyboardFocusChange):
            p.setPen(QPen(QColor("#007aff"), 1.5))
            p.drawEllipse(rect.adjusted(-1.5, -1.5, 1.5, 1.5))


class PetBubble(QWidget):
    """Non-interactive speech bubble; never takes focus from the desktop."""
    def __init__(self, pet):
        super().__init__(pet, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.on_right = True
        self.tail_y = 40
        self.waiting = False
        self.placement_anchor = None
        self.placement_area = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        self.text = QLabel(self)
        self.text.setTextFormat(Qt.RichText)
        self.text.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.text.setOpenExternalLinks(False)
        self.text.linkActivated.connect(pet.owner.open_activity)
        self.text.setWordWrap(True)
        self.text.setFixedWidth(260)
        self.text.setStyleSheet("background: transparent; font-size: 12px;")
        layout.addWidget(self.text)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QTimer.singleShot(160, self.parentWidget().restore_activity)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and len(self.parentWidget().owner.agent_activity) == 1:
            self.parentWidget().owner.open_activity('0')
        super().mouseReleaseEvent(event)

    def present(self, pet, content=None):
        colors = THEMES[pet.owner.theme]
        compact = content is not None
        self.waiting = compact and any(t['status'] == 'waiting' for t in pet.owner.agent_activity)
        style = (pet.owner.theme, compact)
        changed_style = getattr(self, "presentation_style", None) != style
        if changed_style:
            self.presentation_style = style
            self.layout().setContentsMargins(22, 12 if compact else 16, 22, 12 if compact else 16)
            self.text.setFixedWidth(208)
            self.text.setStyleSheet(f"background: transparent; color: {colors['text']}; font-size: 11px;")
        text = pet.owner.pet_summary() if content is None else content
        if changed_style or self.text.text() != text:
            self.text.setText(text)
            self.adjustSize()
        # Place it while hidden: showing first would flash the previous position.
        self.reposition()
        self.show()

    def reposition(self):
        pet = self.parentWidget()
        anchor = pet.mapToGlobal(pet.rect().center())
        screen = QApplication.screenAt(anchor) or pet.screen()
        area = screen.availableGeometry().adjusted(8, 8, -8, -8)
        self.on_right = anchor.x() + 54 + self.width() <= area.right()
        x = anchor.x() + 54 if self.on_right else anchor.x() - 54 - self.width()
        y = max(area.top(), min(anchor.y() - 40, area.bottom() - self.height() + 1))
        self.placement_anchor, self.placement_area = anchor, area
        self.tail_y = max(28, min(anchor.y() - y, self.height() - 28))
        self.move(max(area.left(), min(x, area.right() - self.width() + 1)), y)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(10, 3, self.width() - 20, self.height() - 8)
        colors = THEMES[self.parentWidget().owner.theme]
        path = QPainterPath()
        path.addRoundedRect(body, 16, 16)
        tail = QPainterPath()
        x = body.left() if self.on_right else body.right()
        tip = 1 if self.on_right else self.width() - 1
        tail.moveTo(x, self.tail_y - 6)
        tail.lineTo(tip, self.tail_y)
        tail.lineTo(x, self.tail_y + 6)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawPath(path.translated(0, 3))
        p.setPen(QPen(QColor(colors['warning'] if self.waiting else colors['border']), 1.5 if self.waiting else 1))
        p.setBrush(QColor(colors['card']))
        p.drawPath(path)


class ComputerPet(CircleButton):
    """Pixel mascot with speech bubble and drag/click separation."""
    def __init__(self, owner):
        super().__init__(owner)
        self.setFixedSize(96, 96)
        atlas()
        self.animation_state = 0
        self.cycles = 0
        self.status_row = 3
        self.preview_row = None
        self.frame = 0
        self.idle = QTimer(self)
        self.idle.setInterval(120)
        self.idle.timeout.connect(self.tick)
        self.bubble_timer = QTimer(self)
        self.bubble_timer.setSingleShot(True)
        self.bubble_timer.setInterval(350)
        self.bubble_timer.timeout.connect(self.show_bubble)
        self.bubble = PetBubble(self)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.sync_animation()
        self.bubble_timer.start()

    def leaveEvent(self, event):
        self.bubble_timer.stop()
        super().leaveEvent(event)
        self.sync_animation()
        QTimer.singleShot(160, self.restore_activity)

    def restore_activity(self):
        if not self.underMouse() and not self.bubble.underMouse():
            self.show_activity()

    def show_activity(self):
        if not self.isVisible() or self.dragging or self.isDown() or self.underMouse():
            return
        if self.owner.agent_activity:
            colors = THEMES[self.owner.theme]
            rows = []
            for index, task in enumerate(self.owner.agent_activity[:4]):
                key, status = task['agent'], task['label']
                suffix = '.png' if key == 'antigravity' else '-white.svg' if self.owner.theme == 'dark' else '.svg'
                asset = Path(__file__).parent / 'assets' / (key + suffix)
                name = 'Codex' if key == 'codex' else SERVICES[key][0]
                short_status = self.fontMetrics().elidedText(status, Qt.ElideRight, 166)
                color = colors['warning'] if task['status'] == 'waiting' else colors['muted']
                minutes, seconds = divmod(task['elapsed'], 60)
                elapsed = f'{minutes}분 {seconds:02d}초' if minutes else f'{seconds}초'
                if minutes >= 60:
                    elapsed = f'{minutes // 60}시간 {minutes % 60}분'
                elapsed = ('감지 후 ' if task['estimated'] else '') + elapsed
                rows.append(f'<tr><td width="27" valign="middle"><a href="{index}"><img src="{asset.as_uri()}" width="20" height="20"></a></td>'
                            f'<td><a href="{index}" style="color:{colors["text"]}; text-decoration:none;"><b>{escape(name)}</b></a><br>'
                            f'<a href="{index}" style="color:{color}; text-decoration:none; font-size:10px;">{escape(short_status)}</a><br>'
                            f'<a href="{index}" style="color:{colors["muted"]}; font-size:9px; text-decoration:none;">{elapsed} 경과</a></td></tr>')
            if len(self.owner.agent_activity) > 4:
                rows.append('<tr><td colspan="2"><a href="more">전체 작업 보기</a></td></tr>')
            self.bubble.present(self, '<qt><table width="100%" cellspacing="3" cellpadding="0">' + ''.join(rows) + '</table></qt>')
        else:
            self.bubble.hide()

    def show_bubble(self):
        if self.isVisible() and not self.dragging and not self.isDown():
            self.bubble.present(self)

    def mousePressEvent(self, event):
        self.preview_row = None
        self.bubble_timer.stop()
        self.bubble.hide()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        previous = animation_row(self)
        super().mouseMoveEvent(event)
        if self.dragging and animation_row(self) != previous:
            self.sync_animation()

    def mouseReleaseEvent(self, event):
        was_dragging = self.dragging
        super().mouseReleaseEvent(event)
        if was_dragging:
            self.sync_animation()
            self.show_activity()

    def sync_animation(self):
        self.animation_state = animation_row(self)
        self.cycles = self.frame = 0
        self.set_frame_interval()
        self.update()

    def set_status(self, row):
        self.status_row = row
        if self.isVisible():
            self.sync_animation()

    def set_frame_interval(self):
        if self.owner.reduce_motion.isChecked() and self.preview_row is None:
            self.idle.stop()
            return
        row = display_row(self)
        self.idle.setInterval(DURATIONS[row][self.frame] * (12 if row == 0 and self.preview_row is None else 1))
        if self.isVisible():
            self.idle.start()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        action = menu.addAction("빠른 일정 입력")
        animations = menu.addMenu("애니메이션 미리보기")
        previews = {animations.addAction(name): row for row, name in enumerate(ANIMATION_NAMES)}
        chosen = menu.exec(event.globalPos())
        if chosen == action:
            if self.owner.collapsed:
                self.owner.toggle_collapsed()
            self.owner.set_page("calendar")
            self.owner.calendar.quick_input.setFocus()
        elif chosen in previews:
            self.play_animation(previews[chosen])

    def play_animation(self, row):
        if row not in range(len(DURATIONS)):
            return
        self.preview_row = row
        self.animation_state = row
        self.cycles = 0
        self.frame = 0
        self.idle.start(DURATIONS[row][0])
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_animation()
        # Folding changes the parent size after this event; anchor to the final layout.
        QTimer.singleShot(0, self.show_activity)

    def hideEvent(self, event):
        self.idle.stop()
        self.preview_row = None
        self.bubble_timer.stop()
        self.bubble.hide()
        super().hideEvent(event)

    def tick(self):
        state = animation_row(self)
        if state != self.animation_state:
            self.sync_animation()
            return
        row = display_row(self)
        if self.owner.reduce_motion.isChecked() and self.preview_row is None:
            self.frame = 0
        else:
            self.frame = (self.frame + 1) % len(DURATIONS[row])
            if self.frame == 0:
                if self.preview_row is not None:
                    self.preview_row = None
                    self.sync_animation()
                    return
                if state and self.cycles < 3:
                    self.cycles += 1
        self.set_frame_interval()
        self.update()

    def mood(self):
        now = datetime.now().astimezone()
        if self.owner.calendar.loaded_day == now.date():
            for item in self.owner.calendar.events:
                raw = item.get("start", {}).get("dateTime")
                if raw and 0 < (datetime.fromisoformat(raw.replace("Z", "+00:00")) - now).total_seconds() <= 600:
                    return "schedule"
        return "low" if any(self.owner.pet_low.values()) else "happy"

    def paintEvent(self, event):
        paint_computer(self)


class HomeButton(AppIconButton):
    def __init__(self, owner):
        super().__init__("home", owner)
        self.setFixedSize(40, 36)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(20, 18)
        p.scale(self.scale, self.scale)
        p.setPen(QPen(QColor(self.colors["antigravity"] if self.hasFocus() else self.colors["border"]), 1))
        p.setBrush(QColor(self.colors["selected"] if self.isDown() else self.colors["control"]))
        p.drawRoundedRect(QRectF(-19, -17, 38, 34), 10, 10)
        self.icon().paint(p, -11, -11, 22, 22)


def activate_existing(socket_path, mode="compact"):
    # Reply "ok": the running widget is shown. "switch": it closes so this mode can start.
    client = QLocalSocket()
    client.connectToServer(str(socket_path))
    if not client.waitForConnected(1500):
        return ""
    client.write(mode.encode() + b"\n")
    client.flush()
    while not client.canReadLine() and client.waitForReadyRead(1500):
        pass
    reply = client.readLine().data().decode(errors="replace").strip()
    client.abort()
    return reply


def listen_for_activation(widget, socket_path, mode="compact"):
    # Called only by the process holding the instance lock; stale removal is safe.
    QLocalServer.removeServer(str(socket_path))
    server = QLocalServer(widget)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(str(socket_path)):
        raise RuntimeError("위젯 창 활성화 연결을 열지 못했습니다: " + server.errorString())

    def answer(client):
        if not client.canReadLine():
            return
        request = client.readLine().data().decode(errors="replace").strip()
        if not server.isListening():
            # Closing: no reply, so the new launch keeps waiting for the lock.
            client.abort()
            return
        switch = request in ("compact", "desktop") and request != mode
        client.write(b"switch\n" if switch else b"ok\n")
        client.flush()
        client.disconnectFromServer()
        if switch:
            QTimer.singleShot(0, widget.close)
        else:
            widget.bring_to_front()

    def activate():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            client.disconnected.connect(client.deleteLater)
            client.readyRead.connect(lambda client=client: answer(client))
            answer(client)

    server.newConnection.connect(activate)
    widget.activation_server = server
    return server


class Fetch(QThread):
    result = Signal(str, object, str)

    def __init__(self, key, parent):
        super().__init__(parent)
        self.key = key

    def run(self):
        try:
            self.result.emit(self.key, PROVIDERS[self.key](), "")
        except QuotaError as error:
            self.result.emit(self.key, None, str(error))
        except Exception:
            # CLI payloads and exceptions can contain credentials: never display them.
            self.result.emit(self.key, None, "응답 형식·연결 확인 필요")


class ResizeGrip(QSizeGrip):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = self.palette().color(QPalette.WindowText)
        color.setAlpha(130)
        painter.setPen(QPen(color, 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(5, 14, 14, 5)
        painter.drawLine(11, 14, 14, 11)


class Card(QWidget):
    billing_requested = Signal(str)

    def __init__(self, key):
        super().__init__()
        title, subtitle, _, url = SERVICES[key]
        self.key = key
        self.setProperty("service", key)
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.setSpacing(10)
        badge = QPushButton() if key == "codex" else label("", "badge")
        badge.setObjectName("badge")
        badge.setFixedSize(34, 34)
        asset = logo_asset(key)
        if key == "codex":
            badge.setIcon(QIcon(str(asset)))
            badge.setIconSize(QSize(26, 26))
            badge.setCursor(Qt.PointingHandCursor)
            badge.setAccessibleName("ChatGPT 프로그램 열기")
            badge.setToolTip("ChatGPT 열기")
            badge.clicked.connect(self.open_chatgpt)
        else:
            badge.setAlignment(Qt.AlignCenter)
            badge.setPixmap(QIcon(str(asset)).pixmap(QSize(26, 26), self.devicePixelRatioF()))
            badge.setAccessibleName(title + " 로고")
        heading.addWidget(badge)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(title, "serviceTitle"))
        self.plan = label("플랜 확인 중", "caption")
        titles.addWidget(self.plan)
        heading.addLayout(titles, 1)
        link = QPushButton("↗")
        link.setObjectName("link")
        link.setFixedSize(28, 28)
        link.setAccessibleName(title + " 서비스 페이지 열기")
        link.setToolTip(title + " 서비스 페이지 열기")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        heading.addWidget(link)
        layout.addLayout(heading)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        self.summary_label = label(subtitle, "secondary")
        summary.addWidget(self.summary_label, 1)
        self.amount = label("—", "amount")
        self.amount.setWordWrap(False)
        summary.addWidget(self.amount)
        layout.addLayout(summary)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        layout.addWidget(self.bar)
        foot = QHBoxLayout()
        self.reset = label("로그인된 계정에서 조회 중", "caption")
        foot.addWidget(self.reset, 1)
        if key == "claude_max":
            self.login_button = QPushButton("계정 로그인")
            self.login_button.setAccessibleName("Claude Max 5x 계정 별도 로그인")
            self.login_button.setToolTip("브라우저에서 개인 Max 계정으로 로그인한 뒤 새로고침하세요")
            self.login_button.clicked.connect(self.open_claude_login)
            foot.addWidget(self.login_button)
        self.details_button = QPushButton("상세")
        self.details_button.setObjectName("detailsButton")
        self.details_button.setCheckable(True)
        self.details_button.setAccessibleName(title + " 기간별 한도 펼치기")
        foot.addWidget(self.details_button)
        layout.addLayout(foot)
        billing_row = QHBoxLayout()
        self.billing = label("다음 결제일 미설정", "caption")
        billing_row.addWidget(self.billing, 1)
        self.billing_button = QPushButton("결제일 설정")
        self.billing_button.setObjectName("detailsButton")
        self.billing_button.setAccessibleName(title + " 결제일 설정")
        self.billing_button.clicked.connect(lambda: self.billing_requested.emit(self.key))
        billing_row.addWidget(self.billing_button)
        layout.addLayout(billing_row)
        self.details = QWidget()
        self.rows = QVBoxLayout(self.details)
        self.rows.setContentsMargins(0, 2, 0, 0)
        self.rows.setSpacing(10)
        self.details.hide()
        layout.addWidget(self.details)
        self.details_button.toggled.connect(self.toggle_details)
        self.status = label("", "status")
        self.status.hide()
        layout.addWidget(self.status)
        self.last_success = None
        self.data = []
        self.captions = []
        self.error = ""

    def open_claude_login(self):
        try:
            profile = claude_max_dir()
            profile.mkdir(mode=0o700, parents=True, exist_ok=True)
            profile.chmod(0o700)
            env = QProcessEnvironment()
            for name, value in claude_environment(profile).items():
                env.insert(name, value)
            process = QProcess(self)
            process.setProcessEnvironment(env)
            process.setWorkingDirectory(str(profile))
            process.setProgram(executable("terminator"))
            process.setArguments(["--no-dbus", "--title", "Claude Max 5x 로그인", "-x",
                                  executable("rtk"), "proxy", executable("claude"), "auth", "login"])
            result = process.startDetached()
            started = result[0] if isinstance(result, tuple) else result
            process.deleteLater()
            if not started:
                raise OSError("terminal launch failed")
            self.status.setText("브라우저에서 Max 계정 로그인 후 새로고침해 주세요")
            self.status.show()
        except (OSError, QuotaError):
            QMessageBox.warning(self, "Claude Max 로그인", "로그인 터미널을 열지 못했습니다. Claude Code와 Terminator 설치를 확인해 주세요.")

    def open_chatgpt(self):
        started, _ = QProcess.startDetached("chatgpt", [])
        if not started:
            QMessageBox.warning(self, "ChatGPT", "ChatGPT 프로그램을 실행하지 못했습니다. 설치 상태를 확인해주세요.")

    def toggle_details(self, expanded):
        self.details.setVisible(expanded)
        self.details_button.setText("접기" if expanded else "상세")

    def set_warning(self, warning):
        self.setProperty("warning", warning)
        for item in (self, self.status, self.bar):
            item.style().unpolish(item)
            item.style().polish(item)
        self.update()

    def update_result(self, rows, error):
        self.error = error or ""
        if error:
            if not self.last_success:
                self.reset.setText("아직 조회된 한도가 없습니다")
            previous = f" · 마지막 성공 {self.last_success}" if self.last_success else ""
            self.status.setText(error + previous)
            self.status.show()
            self.set_warning(True)
            self.setToolTip("표시된 값은 마지막 성공 당시 값입니다." if self.last_success else error)
            return
        def priority(row):
            weekly = "주간" in row["label"] or row["label"].startswith("이번 주")
            overall = "전체 모델" in row["label"] or row["label"] == "Codex · 주간"
            remaining = row["remaining"]
            return (not weekly, not (weekly and overall),
                    remaining if remaining is not None else float("inf"))

        rows = sorted(rows, key=priority)
        self.data = rows
        primary = rows[0]
        remaining = primary["remaining"]
        self.amount.setText("무제한" if remaining is None else f"{remaining:g}%")
        self.amount.setAccessibleName("남은 한도 " + self.amount.text())
        self.summary_label.setText(primary["label"] + "\n" + ("제한 없음" if remaining is None else "남은 한도"))
        self.bar.setVisible(remaining is not None)
        self.bar.setValue(round(remaining * 10) if remaining is not None else 1000)
        self.bar.setAccessibleName(primary["label"] + " 남은 한도")
        self.reset.setText(reset_line(primary) if remaining is not None else "사용량 제한 없음")
        for item in (self.amount, self.bar):
            set_risk(item, risk_level(remaining))
        self.set_warning(any(row["remaining"] is not None and row["remaining"] <= 20 for row in rows))
        while self.rows.count():
            self.rows.takeAt(0).widget().deleteLater()
        self.captions = []
        for row in rows:
            box = QWidget()
            stack = QVBoxLayout(box)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(3)
            line = QHBoxLayout()
            line.addWidget(label(row["label"], "rowTitle"), 1)
            remaining = row["remaining"]
            amount = "무제한" if remaining is None else f"{remaining:g}% 남음"
            value = label(amount, "rowValue")
            value.setWordWrap(False)
            set_risk(value, risk_level(remaining))
            line.addWidget(value)
            stack.addLayout(line)
            if remaining is not None:
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setValue(round(remaining * 10))
                bar.setTextVisible(False)
                bar.setFixedHeight(4)
                bar.setAccessibleName(row["label"] + " " + amount)
                set_risk(bar, risk_level(remaining))
                stack.addWidget(bar)
                caption = label(reset_line(row), "caption")
                self.captions.append((caption, row))
                stack.addWidget(caption)
            self.rows.addWidget(box)
        self.last_success = datetime.now().strftime("%m/%d %H:%M:%S")
        self.status.setText("갱신 " + self.last_success)
        self.status.hide()
        self.details.setToolTip("마지막 갱신 " + self.last_success)
        self.setToolTip("")

    def refresh_countdown(self, now=None):
        if self.data and self.data[0]["remaining"] is not None:
            self.reset.setText(reset_line(self.data[0], now))
        for caption, row in self.captions:
            caption.setText(reset_line(row, now))


class QuotaRow(QFrame):
    activated = Signal(str)

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.setProperty("service", key)
        self.setObjectName("quotaRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        stack = QVBoxLayout(self)
        stack.setContentsMargins(12, 9, 12, 9)
        stack.setSpacing(4)
        line = QHBoxLayout()
        line.setSpacing(10)
        logo = label("", "badge")
        logo.setFixedSize(26, 26)
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(QIcon(str(logo_asset(key))).pixmap(QSize(20, 20), self.devicePixelRatioF()))
        line.addWidget(logo)
        line.addWidget(label(SERVICES[key][0], "serviceTitle"), 1)
        self.amount = label("—", "quotaAmount")
        self.amount.setWordWrap(False)
        line.addWidget(self.amount)
        stack.addLayout(line)
        self.caption = label("", "caption")
        stack.addWidget(self.caption)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        policy = self.bar.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.bar.setSizePolicy(policy)
        stack.addWidget(self.bar)

    def update_card(self, card):
        rows, remaining = card.data, None
        if rows:
            primary = rows[0]
            remaining = primary["remaining"]
            amount = "무제한" if remaining is None else f"{remaining:g}%"
            when = "제한 없음" if remaining is None else time_left(primary.get("reset_at")) or primary["reset"]
            caption = " · ".join(v for v in (primary["label"], when) if v)
            tip = [" · ".join(v for v in (f"{row['label']}: " + ("무제한" if row["remaining"] is None else f"{row['remaining']:g}% 남음"),
                                           reset_line(row)) if v) for row in rows]
        else:
            amount, caption, tip = ("조회 실패" if card.error else "—"), card.reset.text(), []
        self.amount.setText(amount)
        self.caption.setText(caption)
        self.bar.setVisible(remaining is not None)
        self.bar.setValue(round(remaining * 10) if remaining is not None else 0)
        for item in (self.amount, self.bar):
            # A failed lookup with nothing to show is flagged like a low quota.
            set_risk(item, "low" if card.error and not rows else risk_level(remaining))
        self.setToolTip("\n".join(([card.error] if card.error else []) + tip))
        self.setAccessibleName(f"{SERVICES[self.key][0]} {amount} · 카드로 자세히 보기")

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit(self.key)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit(self.key)
        else:
            super().keyPressEvent(event)


class ActivityFetch(QThread):
    result = Signal(list)

    def run(self):
        self.result.emit(read_activity(detailed=True))


class Widget(QWidget):
    def __init__(self, smoke=False):
        super().__init__()
        self.smoke = smoke
        # Logout or kill skips closeEvent; save the window shortly after it settles.
        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.setInterval(1000)
        self.geometry_timer.timeout.connect(self.save_geometry)
        self.activation_server = None
        self.settings_prefix = getattr(self, "settings_prefix", "")
        self.settings = QSettings("wego", "ai-quota-widget")
        self.notifications = Notifications(self.settings, self.send_notification, persist=not smoke)
        self.jobs = []
        self.pet_low = {}
        self.pet_quotas = {}
        self.agent_activity = []
        self.activity_tracker = ActivityTracker()
        self.activity_job = None
        self.collapsed = False
        self.fold_transition = FoldTransition(self)
        self.api_plans = {}
        self.account_names = {}
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon.fromTheme("utilities-system-monitor"))
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_QuitOnClose, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setObjectName("window")
        self.setMinimumSize(360, 440)
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(430, min(780, area.height() - 40))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.surface = QFrame()
        self.surface.setObjectName("surface")
        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 65))
        self.surface.setGraphicsEffect(shadow)
        outer.addWidget(self.surface)
        self.restore_button = ComputerPet(self)
        self.restore_button.setAccessibleName("흰색 미니 컴퓨터 · 클릭하여 위젯 열기")
        self.restore_button.clicked.connect(self.toggle_collapsed)
        outer.addWidget(self.restore_button, 0, Qt.AlignCenter)
        self.restore_button.hide()
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(18, 18, 18, 12)
        layout.setSpacing(12)
        title = QHBoxLayout()
        title.addStretch(1)
        menu_button = QToolButton()
        menu_button.setText("설정")
        menu_button.setAccessibleName("위젯 설정")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(menu_button)
        self.auto = self.menu.addAction("로그인 시 자동 시작")
        self.auto.setCheckable(True)
        self.auto.setChecked(self.autostart_path().exists())
        self.auto.toggled.connect(self.set_autostart)
        self.reduce_motion = self.menu.addAction("동작 줄이기")
        self.reduce_motion.setCheckable(True)
        self.reduce_motion.setChecked(self.settings.value("reduce_motion", False, type=bool))
        self.reduce_motion.toggled.connect(self.set_reduced_motion)
        alerts = self.menu.addMenu("알림")
        calendar_alerts = alerts.addMenu("일정 시작 알림")
        self.reminder_actions = []
        for minutes, text in ((0, "끄기"), (5, "5분 전"), (10, "10분 전")):
            action = calendar_alerts.addAction(text)
            action.setCheckable(True)
            action.setChecked(self.settings.value("notify/calendar", 10, type=int) == minutes)
            action.triggered.connect(lambda checked=False, m=minutes: self.set_reminder(m))
            self.reminder_actions.append((minutes, action))
        for key, service in SERVICES.items():
            if key not in PROVIDERS:
                continue
            group = alerts.addMenu(service[0])
            for threshold in (20, 10):
                action = group.addAction(f"잔여 {threshold}% 이하")
                action.setCheckable(True)
                setting = f"notify/{key}/{threshold}"
                action.setChecked(self.settings.value(setting, True, type=bool))
                action.toggled.connect(lambda enabled, name=setting: self.settings.setValue(name, enabled))
        self.waiting_action = alerts.addAction("에이전트 승인 대기")
        self.waiting_action.setCheckable(True)
        self.waiting_action.setChecked(self.settings.value("notify/agents/waiting", True, type=bool))
        self.waiting_action.toggled.connect(lambda enabled: self.settings.setValue("notify/agents/waiting", enabled))
        self.menu.addSeparator()
        opacity_panel = QWidget()
        opacity_panel.setMinimumWidth(230)
        opacity_layout = QVBoxLayout(opacity_panel)
        opacity_layout.setContentsMargins(14, 8, 14, 10)
        self.opacity_text = label("투명도 0% · 선명하게")
        opacity_layout.addWidget(self.opacity_text)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 70)
        self.opacity_slider.setSingleStep(5)
        self.opacity_slider.setAccessibleName("위젯 투명도")
        self.opacity_slider.setToolTip("0%는 불투명, 70%는 가장 투명하게 표시")
        opacity_layout.addWidget(self.opacity_slider)
        opacity_action = QWidgetAction(self.menu)
        opacity_action.setDefaultWidget(opacity_panel)
        self.menu.addAction(opacity_action)
        self.opacity_slider.valueChanged.connect(self.set_transparency)
        try:
            transparency = int(self.settings.value("transparency", 0))
        except (TypeError, ValueError):
            transparency = 0
        self.set_transparency(transparency)
        self.menu.addSeparator()
        plan_action = self.menu.addAction("Antigravity 플랜 직접 설정…")
        plan_action.triggered.connect(self.edit_antigravity_plan)
        sizes = self.menu.addMenu("위젯 크기")
        for name, width, height in (("작게", 360, 580), ("기본", 430, 780), ("크게", 520, 900)):
            action = sizes.addAction(name)
            action.triggered.connect(lambda checked=False, size=QSize(width, height): self.set_feature_size(size))
        menu_button.setMenu(self.menu)
        self.collapse_button = CircleButton(self, compact=True)
        self.collapse_button.setAccessibleName("미니 컴퓨터 캐릭터로 접기")
        self.collapse_button.setToolTip("미니 컴퓨터로 접기 · 항상 위에 표시")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        title.addWidget(self.collapse_button)
        title.addWidget(menu_button)
        close = QPushButton("×")
        close.setFixedSize(28, 28)
        close.setAccessibleName("위젯 닫기")
        close.setToolTip("닫기")
        close.clicked.connect(self.close)
        title.addWidget(close)
        layout.addLayout(title)
        appearance = QHBoxLayout()
        self.clock = label("", "clock")
        self.clock.setWordWrap(False)
        self.clock.setAccessibleName("현재 날짜와 시간")
        self.clock.setToolTip("PC 현지 시간")
        appearance.addWidget(self.clock, 1)
        self.update_clock()
        segment = QFrame()
        segment.setObjectName("segment")
        segment_layout = QHBoxLayout(segment)
        segment_layout.setContentsMargins(3, 3, 3, 3)
        segment_layout.setSpacing(2)
        self.theme_group = QButtonGroup(self)
        self.theme_buttons = {}
        for key, text in (("light", "라이트"), ("dark", "다크")):
            button = QPushButton(text)
            button.setObjectName("themeButton")
            button.setCheckable(True)
            button.setAccessibleName(text + " 모드")
            button.clicked.connect(lambda checked=False, mode=key: self.set_theme(mode))
            self.theme_group.addButton(button)
            self.theme_buttons[key] = button
            segment_layout.addWidget(button)
        title.insertWidget(0, segment)
        layout.addLayout(appearance)
        self.page_heading = label("", "pageHeading")
        layout.addWidget(self.page_heading)
        self.pages = QStackedWidget()
        self.page_opacity = PageFade(self.pages)
        self.page_motion = QVariantAnimation(self)
        self.page_motion.valueChanged.connect(self.page_opacity.setOpacity)
        self.page_motion.setDuration(160)
        self.page_motion.setEasingCurve(motion_curve())
        self.quota_page = QWidget()
        quota_layout = QVBoxLayout(self.quota_page)
        quota_layout.setContentsMargins(0, 0, 0, 0)
        quota_layout.setSpacing(12)
        self.quota_view_switch = QFrame()
        self.quota_view_switch.setObjectName("segment")
        switch_layout = QHBoxLayout(self.quota_view_switch)
        switch_layout.setContentsMargins(3, 3, 3, 3)
        switch_layout.setSpacing(2)
        self.quota_view_group = QButtonGroup(self)
        self.quota_view_buttons = {}
        for view, text in (("cards", "카드"), ("list", "목록")):
            button = QPushButton(text)
            button.setObjectName("themeButton")
            button.setCheckable(True)
            button.setAccessibleName(text + " 보기")
            button.clicked.connect(lambda checked=False, v=view: self.set_quota_view(v))
            self.quota_view_group.addButton(button)
            self.quota_view_buttons[view] = button
            switch_layout.addWidget(button)
        quota_layout.addWidget(self.quota_view_switch, 0, Qt.AlignLeft)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("content")
        cards = QVBoxLayout(content)
        cards.setContentsMargins(0, 0, 2, 0)
        cards.setSpacing(12)
        self.calendar = CalendarPanel(self)
        self.calendar.activity.connect(self.restore_button.set_status)
        self.cards = {key: Card(key) for key in SERVICES}
        self.show_plan("antigravity")
        for card in self.cards.values():
            cards.addWidget(card)
            card.billing_requested.connect(self.edit_billing)
        self.update_billing()
        cards.addStretch()
        self.scroll.setWidget(content)
        quota_layout.addWidget(self.scroll, 1)
        self.quota_list = QWidget()
        list_layout = QVBoxLayout(self.quota_list)
        list_layout.setContentsMargins(0, 0, 2, 0)
        list_layout.setSpacing(8)
        self.quota_rows = {key: QuotaRow(key) for key in SERVICES}
        for key, row in self.quota_rows.items():
            row.activated.connect(self.show_card)
            row.update_card(self.cards[key])
            list_layout.addWidget(row)
        list_layout.addStretch()
        quota_layout.addWidget(self.quota_list, 1)
        footer = QHBoxLayout()
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.clicked.connect(self.refresh)
        footer.addWidget(self.refresh_button)
        self.updated = label("3분마다 자동 갱신", "caption")
        self.updated.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer.addWidget(self.updated, 1)
        quota_layout.addLayout(footer)
        self.pages.addWidget(self.quota_page)
        self.calendar_scroll = QScrollArea()
        self.calendar_scroll.setWidgetResizable(True)
        self.calendar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.calendar_scroll.viewport().setAutoFillBackground(False)
        calendar_content = QWidget()
        calendar_layout = QVBoxLayout(calendar_content)
        calendar_layout.setContentsMargins(0, 0, 2, 0)
        calendar_layout.addWidget(self.calendar)
        calendar_layout.addStretch()
        self.calendar_scroll.setWidget(calendar_content)
        self.pages.addWidget(self.calendar_scroll)
        layout.addWidget(self.pages, 1)
        self.dock = QFrame()
        self.dock.setObjectName("navigationDock")
        dock_layout = QGridLayout(self.dock)
        dock_layout.setContentsMargins(12, 24, 12, 22)
        dock_layout.setHorizontalSpacing(24)
        dock_layout.setVerticalSpacing(20)
        dock_layout.setColumnStretch(0, 1)
        dock_layout.setColumnStretch(3, 1)
        self.page_buttons = {}
        self.page_group = QButtonGroup(self)
        apps = [("quota", "AI 한도"), ("calendar", "캘린더")]
        apps.extend((key, value[0]) for key, value in WEB_APPS.items())
        for index, (key, text) in enumerate(apps):
            button = AppIconButton(key, self)
            button.setObjectName("pageButton")
            button.setText(text)
            button.setCheckable(key not in WEB_APPS)
            button.setAccessibleName(text + (" 웹사이트 열기" if key in WEB_APPS else " 화면"))
            button.setToolTip(text + (" · 기본 브라우저에서 열기" if key in WEB_APPS else ""))
            if key in WEB_APPS:
                button.clicked.connect(lambda checked=False, app=key: self.open_web_app(app))
            else:
                button.clicked.connect(lambda checked=False, page=key, source=button: self.set_page(
                    page, animate=source.pointer_activation))
                self.page_group.addButton(button)
            self.page_buttons[key] = button
            if index == len(apps) - 1 and len(apps) % 2:
                dock_layout.addWidget(button, index // 2, 1, 1, 2, Qt.AlignCenter)
            else:
                dock_layout.addWidget(button, index // 2, index % 2 + 1)
        self.home_page = QWidget()
        home_layout = QVBoxLayout(self.home_page)
        home_layout.setContentsMargins(0, 12, 0, 0)
        home_layout.addStretch(1)
        home_layout.addWidget(self.dock)
        home_layout.addStretch(2)
        self.pages.addWidget(self.home_page)
        bottom = QHBoxLayout()
        self.note = label("", "caption")
        self.note.hide()
        bottom.addWidget(self.note, 1)
        bottom.addStretch(1)
        self.home_button = HomeButton(self)
        self.home_button.setFixedSize(40, 36)
        self.home_button.setIconSize(QSize(22, 22))
        self.home_button.setToolTip("앱 메뉴로 돌아가기")
        self.home_button.setAccessibleName("앱 아이콘 메뉴로 돌아가기")
        self.home_button.clicked.connect(lambda: self.set_page("home", animate=self.home_button.pointer_activation))
        bottom.addWidget(self.home_button)
        self.grip = ResizeGrip(self)
        self.grip.setFixedSize(18, 18)
        self.grip.setToolTip("드래그하여 위젯 크기 조절")
        self.grip.setAccessibleName("위젯 크기 조절")
        bottom.addWidget(self.grip)
        layout.addLayout(bottom)
        self.set_page("home")
        self.theme = self.settings.value("theme", "dark")
        self.set_theme(self.theme if self.theme in THEMES else "dark")
        self.set_quota_view(self.settings.value("quota_view", "cards"), persist=False)
        geometry = None if smoke else self.settings.value(self.settings_prefix + "geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            area = QApplication.primaryScreen().availableGeometry()
            self.move(area.right() - self.width() - 20, area.top() + 30)
        if not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in QApplication.screens()):
            self.move(QApplication.primaryScreen().availableGeometry().topLeft())
        self.feature_size = self.settings.value(self.settings_prefix + "feature_size", self.size())
        if not isinstance(self.feature_size, QSize):
            self.feature_size = QSize(430, 780)
        self.resize(self.width(), 440)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(180_000)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(5000)
        self.activity_timer.timeout.connect(self.refresh_activity)
        if not smoke:
            self.activity_timer.start()
            QTimer.singleShot(0, self.refresh_activity)
        QTimer.singleShot(0, self.refresh)
        if not smoke:
            QTimer.singleShot(0, self.calendar.refresh)

    def refresh_activity(self):
        if self.activity_job is not None:
            return
        self.activity_job = ActivityFetch(self)
        self.activity_job.result.connect(self.received_activity)
        self.activity_job.finished.connect(self.finished_activity)
        self.activity_job.start()

    def received_activity(self, activity):
        self.agent_activity, notices = self.activity_tracker.update(activity)
        for item in notices:
            if item['status'] == 'waiting':
                if self.settings.value("notify/agents/waiting", True, type=bool):
                    self.send_notification(SERVICES[item['agent']][0] + ' · 승인·입력 대기', item['label'])
                continue
            self.send_notification(SERVICES[item['agent']][0] + (' · 작업 실패' if item['status'] == 'failed' else ' · 작업 완료'), item['label'])
            self.restore_button.set_status(5 if item['status'] == 'failed' else 8)
        if self.restore_button.isVisible():
            if animation_row(self.restore_button) != self.restore_button.animation_state:
                self.restore_button.sync_animation()
            self.restore_button.update()
        self.restore_button.show_activity()

    def open_activity(self, link):
        tasks = list(self.agent_activity)
        if link == 'more':
            menu = QMenu(self)
            choices = {menu.addAction(SERVICES[t['agent']][0] + ' · ' + t['label']): i for i, t in enumerate(tasks)}
            action = menu.exec(self.restore_button.bubble.pos())
            if action not in choices:
                return
            index = choices[action]
        else:
            try:
                index = int(link)
            except ValueError:
                return
        if not 0 <= index < len(tasks):
            return
        task = tasks[index]
        windows = None
        try:
            windows = XWindows()
            candidates = windows.candidates(task['agent'], task['target'])
            if not candidates:
                raise OSError('연결된 작업 창을 찾지 못했습니다. 해당 터미널이나 앱을 직접 열어주세요.')
            if len(candidates) == 1:
                chosen = candidates[0][0]
            else:
                menu = QMenu(self)
                choices = {menu.addAction(title): wid for wid, title in candidates}
                action = menu.exec(self.restore_button.bubble.pos())
                if action not in choices:
                    return
                chosen = choices[action]
            windows.activate(chosen)
        except OSError as error:
            QMessageBox.information(self, '작업 창 이동', str(error))
        finally:
            if windows is not None:
                windows.close()

    def finished_activity(self):
        self.activity_job.deleteLater()
        self.activity_job = None

    def update_clock(self):
        now = datetime.now().astimezone()
        quiet = self.collapsed or self.isMinimized()
        if not quiet:
            self.clock.setText(now.strftime("%Y.%m.%d %H:%M:%S"))
        if hasattr(self, "clock_timer"):
            interval = 60_000 if quiet else 1_000
            if self.clock_timer.interval() != interval:
                self.clock_timer.setInterval(interval)
        if hasattr(self, "page_buttons") and getattr(self, "clock_day", None) != now.date():
            self.page_buttons["calendar"].update()
            self.clock_day = now.date()
        if hasattr(self, "cards") and getattr(self, "billing_day", None) != now.date():
            self.update_billing()
        minute = now.replace(second=0, microsecond=0)
        if hasattr(self, "cards") and getattr(self, "clock_minute", None) != minute:
            self.clock_minute = minute
            self.update_countdowns()
        if quiet and self.reduce_motion.isChecked():
            self.restore_button.update()

    def update_countdowns(self):
        for card in self.cards.values():
            card.refresh_countdown()
        self.update_quota_list()

    def update_quota_list(self):
        for key, row in self.quota_rows.items():
            row.update_card(self.cards[key])

    def set_quota_view(self, view, persist=True):
        view = view if view in ("cards", "list") else "cards"
        self.scroll.setVisible(view == "cards")
        self.quota_list.setVisible(view == "list")
        self.quota_view_buttons[view].setChecked(True)
        if persist and not self.smoke:
            self.settings.setValue("quota_view", view)

    def show_card(self, key):
        self.set_quota_view("cards")
        card = self.cards[key]
        # The cards were laid out while hidden; scroll once the page has been re-laid out.
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(card, 0, 12))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "clock"):
            self.update_clock()

    def send_notification(self, title, body):
        if self.smoke:
            return False
        # Desktop notifications interpret markup; calendar titles are untrusted text.
        started, _ = QProcess.startDetached("notify-send", ["--app-name=check AI", "--icon=utilities-system-monitor",
                                                            "--", escape(title), escape(body)])
        if not started:
            self.note.setText("알림 전송 실패 · notify-send 설치 확인")
            self.note.show()
        return started

    def pet_summary(self):
        colors = THEMES[self.theme]
        now = datetime.now().astimezone()
        lines = []
        if self.calendar.loaded_day != now.date():
            lines.append("일정: 연결 또는 갱신 필요")
        else:
            upcoming = []
            for event in self.calendar.events:
                raw = event.get("start", {}).get("dateTime")
                if raw:
                    start = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
                    if start >= now:
                        upcoming.append((start, str(event.get("summary") or "제목 없음")))
            if upcoming:
                start, title = min(upcoming)
                lines.append(f"다음 일정 · {start:%m/%d %H:%M}\n{title[:80]}")
            else:
                lines.append("조회된 다음 시간 지정 일정 없음")
        schedule = "<br>".join(escape(line).replace("\n", "<br>") for line in lines)
        rows = []
        for key, info in SERVICES.items():
            value = self.pet_quotas.get(key, "조회 전")
            rows.append(f'<tr><td>{escape(info[0])}</td><td align="right"><b>{escape(value)}</b></td></tr>')
        return (f'<qt><span style="color:{colors["antigravity"]}; font-size:11px;">MY DESK</span>'
                '<br><span style="font-size:16px;"><b>오늘 한눈에</b></span>'
                f'<p><span style="color:{colors["antigravity"]};">다가오는 일정</span><br>' + schedule + '</p>'
                f'<p><span style="color:{colors["antigravity"]};">AI 남은 한도</span></p>'
                '<table width="100%" cellspacing="0" cellpadding="4">' + ''.join(rows) + '</table>'
                f'<p style="color:{colors["muted"]}; font-size:10px;">클릭: 열기 · 드래그: 이동<br>우클릭: 빠른 일정 입력</p></qt>')

    def set_reminder(self, minutes):
        self.settings.setValue("notify/calendar", minutes)
        for value, action in self.reminder_actions:
            action.setChecked(value == minutes)

    def set_reduced_motion(self, enabled):
        self.restore_button.sync_animation()
        if not self.smoke:
            self.settings.setValue("reduce_motion", enabled)
        if enabled:
            self.fold_transition.finish()
            self.setWindowOpacity(1 if self.collapsed else 1 - self.opacity_slider.value() / 100)
            self.page_motion.stop()
            self.page_opacity.setOpacity(1.0)
            for button in (*self.page_buttons.values(), self.collapse_button, self.restore_button, self.home_button):
                button.motion.stop()
                button.set_scale(1.0)

    def open_web_app(self, app):
        name, url = WEB_APPS[app]
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, name, "브라우저를 열지 못했습니다. 기본 브라우저 설정을 확인해주세요.")

    def set_page(self, page, animate=False):
        index = {"quota": 0, "calendar": 1, "home": 2}[page]
        changed = self.pages.currentIndex() != index
        if changed and hasattr(self, "feature_size"):
            if page == "home":
                self.feature_size = self.size()
                self.settings.setValue(self.settings_prefix + "feature_size", self.feature_size)
                self.resize(self.width(), 440)
            elif self.pages.currentIndex() == 2:
                self.restore_feature_size()
        if changed and animate and not self.reduce_motion.isChecked():
            self.page_opacity.snapshot = self.pages.grab()
            self.page_opacity.setGeometry(self.pages.rect())
        self.pages.setCurrentIndex(index)
        if changed:
            self.page_motion.stop()
            if animate and not self.reduce_motion.isChecked():
                self.page_opacity.raise_()
                self.page_motion.setStartValue(0.0)
                self.page_motion.setEndValue(1.0)
                self.page_motion.start()
            else:
                self.page_opacity.setOpacity(1.0)
        self.page_heading.setVisible(page != "home")
        self.home_button.setVisible(page != "home")
        if page == "home":
            selected = self.page_group.checkedButton() or self.page_buttons["quota"]
            selected.setFocus(Qt.OtherFocusReason)
        else:
            self.page_buttons[page].setChecked(True)
            self.page_heading.setText("AI 한도" if page == "quota" else "일정")
            self.home_button.setFocus(Qt.OtherFocusReason)
        if hasattr(self, "theme"):
            self.update_page_icons()

    def restore_feature_size(self):
        area = self.screen().availableGeometry()
        self.resize(self.feature_size.width(), min(self.feature_size.height(), area.height() - 20))
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def set_feature_size(self, size):
        # Size presets apply to the AI 한도·일정 pages; the home page stays 440 tall.
        self.feature_size = size
        if self.pages.currentIndex() == 2:
            self.settings.setValue(self.settings_prefix + "feature_size", size)
        else:
            self.restore_feature_size()

    def update_page_icons(self):
        colors = THEMES[self.theme]
        home = QPixmap(48, 48)
        home.fill(Qt.transparent)
        painter = QPainter(home)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(colors["text"]), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for line in ((6, 22, 24, 7), (24, 7, 42, 22), (11, 19, 11, 40),
                     (37, 19, 37, 40), (11, 40, 19, 40), (29, 40, 37, 40),
                     (19, 40, 19, 28), (19, 28, 29, 28), (29, 28, 29, 40)):
            painter.drawLine(*line)
        painter.end()
        self.home_button.setIcon(QIcon(home))
        self.home_button.colors = colors
        for page, button in self.page_buttons.items():
            button.colors = colors
            button.update()

    def set_theme(self, theme):
        self.theme = theme
        colors = THEMES[theme]
        if self.restore_button.bubble.isVisible():
            if self.restore_button.underMouse():
                self.restore_button.bubble.present(self.restore_button)
            else:
                self.restore_button.show_activity()
        self.theme_buttons[theme].setChecked(True)
        if not self.smoke:
            self.settings.setValue("theme", theme)
        self.setStyleSheet(f"""
            QWidget {{color:{colors['text']}; font-size:12px; background:transparent;}}
            QFrame#surface {{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors['background']},stop:1 {colors['card']}); border:1px solid {colors['border']}; border-radius:32px;}}
            QWidget#card {{background:{colors['card']}; border:1px solid {colors['border']}; border-radius:22px;}}
            QLabel#pageHeading {{font-size:28px; font-weight:700; letter-spacing:-1px; padding:2px 0 6px 0;}}
            QFrame#navigationDock {{background:transparent; border:0;}}
            QLabel#clock {{font-size:20px; font-weight:600;}}
            QLabel#serviceTitle {{font-size:14px; font-weight:700;}}
            QLabel#secondary {{color:{colors['muted']}; font-size:11px;}}
            QLabel#caption, QLabel#status {{color:{colors['muted']}; font-size:11px;}}
            QLabel#calendarListing {{font-size:13px; padding:12px 2px;}}
            QWidget#eventCard {{background:{colors['background']}; border:1px solid {colors['border']}; border-radius:14px;}}
            QWidget#eventCard[current='true'] {{border:1px solid {colors['antigravity']};}}
            QLabel#eventTime {{color:{colors['antigravity']}; font-size:12px; font-weight:600;}}
            QLabel#success {{color:{colors['codex']}; padding:8px; font-weight:600;}}
            QComboBox {{background:{colors['control']}; border:1px solid {colors['border']}; border-radius:8px; padding:8px;}}
            QComboBox QAbstractItemView {{background:{colors['card']}; color:{colors['text']}; selection-background-color:{colors['selected']};}}
            QLabel#amount {{font-family:'DejaVu Sans'; font-size:26px; font-weight:600; letter-spacing:-2px;}}
            QLabel#quotaAmount {{font-family:'DejaVu Sans'; font-size:18px; font-weight:600;}}
            QFrame#quotaRow {{background:{colors['card']}; border:1px solid {colors['border']}; border-radius:14px;}}
            QFrame#quotaRow:hover, QFrame#quotaRow:focus {{border-color:{colors['antigravity']};}}
            QLabel#rowTitle {{font-size:11px;}}
            QLabel#rowValue {{font-size:10px; font-weight:600;}}
            QLabel#badge {{border-radius:11px; background:#ffffff;}}
            QPushButton#badge {{border-radius:11px; background:#ffffff; padding:0;}}
            QPushButton, QToolButton {{border:1px solid transparent; border-radius:10px; padding:5px 9px; background:{colors['control']}; font-size:11px;}}
            QPushButton:hover, QToolButton:hover {{border-color:{colors['border']};}}
            QPushButton:pressed, QToolButton:pressed {{background:{colors['selected']};}}
            QPushButton:focus, QToolButton:focus {{border-color:{colors['antigravity']};}}
            QPushButton:disabled {{color:{colors['muted']};}}
            QPushButton#link {{background:transparent; font-size:17px; padding:0; color:{colors['muted']};}}
            QPushButton#detailsButton {{background:transparent; color:{colors['muted']}; padding:2px 4px; font-size:10px;}}
            QFrame#segment {{background:{colors['control']}; border-radius:12px;}}
            QPushButton#themeButton {{background:transparent; padding:4px 9px; border-radius:9px;}}
            QPushButton#themeButton:checked {{background:{colors['selected']};}}
            QProgressBar {{background:{colors['track']}; border:0; border-radius:3px;}}
            QProgressBar::chunk {{border-radius:3px;}}
            QScrollArea {{border:0;}}
            QScrollBar:vertical {{width:4px; background:transparent; margin:0;}}
            QScrollBar::handle:vertical {{background:{colors['border']}; border-radius:2px; min-height:24px;}}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{background:transparent;}}
            QMenu {{background:{colors['card']}; border:1px solid {colors['border']}; padding:6px;}}
            QDialog, QCalendarWidget {{background:{colors['card']};}}
            QLabel#eventHeading {{font-size:22px; font-weight:700;}}
            QPushButton#keywordButton {{padding:7px 5px; border:1px solid {colors['border']}; border-radius:8px;}}
            QPushButton#keywordButton:checked {{background:{colors['selected']}; border-color:{colors['antigravity']}; color:{colors['antigravity']}; font-weight:600;}}
            QPushButton#eventSubmit {{background:{colors['antigravity']}; color:{colors['card']}; font-size:13px; font-weight:700;}}
            QPushButton#eventSubmit:disabled {{background:{colors['control']}; color:{colors['muted']};}}
            QPushButton#calendarAdd {{background:{colors['antigravity']}; color:{colors['card']}; font-weight:700;}}
            QPushButton#calendarAdd:disabled {{background:{colors['control']}; color:{colors['muted']};}}
            QCalendarWidget {{border:1px solid {colors['border']}; border-radius:12px; padding:8px;}}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{background:{colors['control']}; border-radius:8px; padding:4px;}}
            QCalendarWidget QToolButton {{background:transparent; font-size:14px; font-weight:600; padding:7px; border-radius:6px;}}
            QCalendarWidget QToolButton:hover {{background:{colors['selected']};}}
            QCalendarWidget QAbstractItemView {{background:{colors['card']}; alternate-background-color:{colors['control']}; color:{colors['text']}; selection-background-color:{colors['antigravity']}; selection-color:{colors['card']}; font-size:14px; border:0; outline:0;}}
            QCalendarWidget QSpinBox {{background:{colors['control']}; color:{colors['text']}; font-size:14px; border:1px solid {colors['border']}; padding:4px;}}
            QLineEdit, QDateTimeEdit, QDateEdit {{background:{colors['control']}; border:1px solid {colors['border']}; border-radius:10px; padding:8px;}}
            QLineEdit:focus, QDateTimeEdit:focus, QDateEdit:focus {{border-color:{colors['antigravity']};}}
            QCheckBox::indicator {{width:14px; height:14px; border:1px solid {colors['muted']}; border-radius:3px; background:{colors['control']};}}
            QCheckBox::indicator:checked {{background:{colors['antigravity']};}}
            QMenu::item {{padding:8px 20px; border-radius:6px;}}
            QMenu::item:selected {{background:{colors['control']};}}
            QSlider::groove:horizontal {{height:5px; background:{colors['track']}; border-radius:2px;}}
            QSlider::sub-page:horizontal {{background:{colors['antigravity']}; border-radius:2px;}}
            QSlider::handle:horizontal {{background:{colors['text']}; width:16px; margin:-6px 0; border-radius:8px;}}
            QSlider:focus {{border:1px solid {colors['antigravity']}; border-radius:4px;}}
            QToolTip {{background:{colors['card']}; color:{colors['text']}; border:1px solid {colors['border']}; padding:6px;}}
        """ + "\n".join(
            f"QWidget[service='{key}'] QLabel#badge, QWidget[service='{key}'] QLabel#rowValue {{color:{colors[key]};}} "
            f"QWidget[service='{key}'] QProgressBar::chunk {{background:{colors[key]};}}"
            for key in SERVICES
        ) + f"\nQWidget[warning='true'] QLabel#status {{color:{colors['warning']};}}" + "\n".join(
            # Per-service selectors are needed to outrank the service-colored chunk and rowValue rules.
            f"QProgressBar[risk='{risk}']::chunk, QWidget[service='{key}'] QProgressBar[risk='{risk}']::chunk {{background:{colors[token]};}} "
            f"QLabel[risk='{risk}'], QWidget[service='{key}'] QLabel#rowValue[risk='{risk}'] {{color:{colors[token]};}}"
            for risk, token in (("low", "warning"), ("critical", "danger")) for key in SERVICES
        ))
        self.update_page_icons()

    def set_transparency(self, value):
        self.fold_transition.finish()
        value = max(0, min(70, int(value)))
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(value)
        self.opacity_slider.blockSignals(False)
        self.opacity_text.setText(f"투명도 {value}%" + (" · 선명하게" if value == 0 else ""))
        self.setWindowOpacity(1 - value / 100)
        if not self.smoke:
            self.settings.setValue("transparency", value)

    def billing_record(self, key):
        try:
            record = json.loads(self.settings.value("billing/" + key, "null"))
        except (TypeError, ValueError):
            return None
        return record if next_payment(record) else None

    def update_billing(self):
        self.billing_day = datetime.now().date()
        for key, card in self.cards.items():
            record = self.billing_record(key)
            card.billing.setText(payment_summary(record, self.billing_day))
            card.billing.setToolTip("이 카드에 직접 설정한 결제 예정일" if record else "서비스에서 확인한 결제일을 직접 설정하세요")
            card.billing_button.setText("수정" if record else "결제일 설정")

    def edit_billing(self, key):
        dialog = BillingDialog(self, SERVICES[key][0], self.billing_record(key))
        if dialog.exec() == BillingDialog.Accepted:
            self.save_billing(key, dialog.selection())
        dialog.deleteLater()

    def save_billing(self, key, record):
        if record is None:
            self.settings.remove("billing/" + key)
        else:
            self.settings.setValue("billing/" + key, json.dumps(record))
        self.update_billing()

    def show_plan(self, key):
        manual = self.settings.value("antigravity_plan", "") if key == "antigravity" else ""
        raw = self.api_plans.get(key)
        if manual:
            text = f"플랜 · {manual} (직접 설정)"
        elif isinstance(raw, str) and raw.strip():
            text = "플랜 · " + PLAN_NAMES.get("claude" if key == "claude_max" else key, {}).get(raw, raw)
        else:
            text = "플랜 정보 미제공" if key == "antigravity" else "플랜 확인 안 됨"
        account = self.account_names.get(key, "")
        self.cards[key].plan.setText(text + ("\n" + account if account else ""))
        self.cards[key].plan.setToolTip("사용자가 입력한 플랜" if manual else
                                       f"서비스 응답: {raw}" if raw else "한도만으로 플랜을 추정하지 않습니다.")

    def edit_antigravity_plan(self):
        value, accepted = QInputDialog.getText(
            self, "Antigravity 플랜", "사용 중인 플랜 이름 (예: Google AI Pro)\n비워 두면 자동 조회로 돌아갑니다.",
            text=self.settings.value("antigravity_plan", ""))
        if accepted:
            self.settings.setValue("antigravity_plan", value.strip()[:100])
            self.show_plan("antigravity")

    @staticmethod
    def autostart_path():
        return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart/ai-quota-widget.desktop"

    def set_autostart(self, enabled):
        try:
            path = self.autostart_path()
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text((Path(__file__).parent / "ai-quota-widget.desktop").read_text())
            else:
                path.unlink(missing_ok=True)
        except OSError:
            self.auto.blockSignals(True)
            self.auto.setChecked(not enabled)
            self.auto.blockSignals(False)
            self.note.setText("자동 시작 설정 저장 실패 · 폴더 권한 확인")
            self.note.show()

    def toggle_collapsed(self):
        self.fold_transition.finish()
        source = self.restore_button if self.collapsed else self.collapse_button
        animate = source.pointer_activation and not self.reduce_motion.isChecked()
        large_rect = self.geometry() if not self.collapsed else self.expanded_rect
        small_rect = self.geometry() if self.collapsed else None
        snapshot = self.grab() if not self.collapsed and animate else None
        if not self.collapsed:
            self.expanded_geometry = self.saveGeometry()
            self.expanded_rect = self.geometry()
            self.page_motion.stop()
            self.page_opacity.hide()
            self.surface.hide()
            self.layout().setContentsMargins(6, 6, 6, 6)
            self.restore_button.show()
            self.setMinimumSize(0, 0)
            self.setFixedSize(108, 108)
            self.collapsed = True
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.setAttribute(Qt.WA_QuitOnClose, True)
            self.setWindowOpacity(1)
            self.move(self.expanded_rect.topLeft())
        else:
            self.restore_button.hide()
            self.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(360, 440)
            self.layout().setContentsMargins(12, 12, 12, 12)
            self.surface.show()
            self.collapsed = False
            self.setGeometry(self.expanded_rect)
            self.set_transparency(self.opacity_slider.value())
        self.show()
        self.raise_()
        self.activateWindow()
        target = self.restore_button if self.collapsed else self.collapse_button
        target.setFocus()
        target.motion.stop()
        target.set_scale(0.97 if animate else 1.0)
        if animate:
            target.animate_scale(1.0)
            self.layout().activate()
            self.fold_transition.begin(snapshot if self.collapsed else self.grab(), large_rect,
                                       self.geometry() if self.collapsed else small_rect, not self.collapsed)
        self.update_clock()

    def bring_to_front(self):
        if self.collapsed:
            self.toggle_collapsed()
        self.showNormal()
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                  max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))
        self.raise_()
        self.activateWindow()

    def refresh(self):
        if self.jobs:
            return
        self.restore_button.set_status(7)
        self.quota_failed = False
        self.refresh_button.setEnabled(False)
        for key in PROVIDERS:
            job = Fetch(key, self)
            job.result.connect(self.received)
            job.finished.connect(self.finished)
            self.jobs.append(job)
            job.start()

    def received(self, key, snapshot, error):
        self.quota_failed = getattr(self, "quota_failed", False) or bool(error)
        card = self.cards[key]
        card.update_result(snapshot["rows"] if snapshot else None, error)
        self.update_quota_list()
        remaining = card.data[0]["remaining"] if card.data else None
        self.pet_quotas[key] = "조회 실패" if error else f"{remaining:g}% 남음" if remaining is not None else "무제한"
        if not error:
            self.pet_low[key] = any(isinstance(row.get("remaining"), (int, float)) and row["remaining"] <= 20
                                    for row in snapshot["rows"])
            self.api_plans[key] = snapshot.get("plan")
            self.account_names[key] = snapshot.get("account", "")
            self.updated.setText(datetime.now().strftime("%H:%M 갱신") + " · 3분 간격")
            self.notifications.quota(key, SERVICES[key][0], snapshot["rows"])
        self.show_plan(key)

    def finished(self):
        job = self.sender()
        self.jobs.remove(job)
        job.deleteLater()
        self.refresh_button.setEnabled(not self.jobs)
        if not self.jobs:
            self.restore_button.set_status(5 if self.quota_failed else 8)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edges = self.resize_edges(event.position())
            if edges:
                self.windowHandle().startSystemResize(edges)
            elif event.position().y() < 78:
                self.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self.geometry_timer.start()
        if getattr(self, "collapsed", False) and self.restore_button.bubble.isVisible():
            self.restore_button.bubble.reposition()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.geometry_timer.start()

    def save_geometry(self):
        # Collapsing and expanding are synchronous, so the debounced save never sees the pet's interim size.
        if self.smoke:
            return
        if self.pages.currentIndex() != 2:
            self.settings.setValue(self.settings_prefix + "feature_size", self.expanded_rect.size() if self.collapsed else self.size())
        self.settings.setValue(self.settings_prefix + "geometry", self.expanded_geometry if self.collapsed else self.saveGeometry())

    def resize_edges(self, point):
        edges = Qt.Edges()
        if self.collapsed:
            return edges
        if point.x() < 12:
            edges |= Qt.LeftEdge
        elif point.x() >= self.width() - 12:
            edges |= Qt.RightEdge
        if point.y() < 12:
            edges |= Qt.TopEdge
        elif point.y() >= self.height() - 12:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        edges = self.resize_edges(event.position())
        if edges in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge):
            cursor = Qt.SizeFDiagCursor
        elif edges in (Qt.TopEdge | Qt.RightEdge, Qt.BottomEdge | Qt.LeftEdge):
            cursor = Qt.SizeBDiagCursor
        elif edges & (Qt.LeftEdge | Qt.RightEdge):
            cursor = Qt.SizeHorCursor
        elif edges:
            cursor = Qt.SizeVerCursor
        else:
            cursor = Qt.ArrowCursor
        self.setCursor(cursor)
        super().mouseMoveEvent(event)

    def closeEvent(self, event):
        if self.calendar.job and not getattr(self.calendar.job, "read_only", False):
            message = "Google 작업 완료 후 닫아 주세요. 로그인 대기는 최대 3분입니다."
            self.calendar.status.setText(message)
            # The calendar status is only visible on its page.
            self.note.setText(message)
            self.note.show()
            event.ignore()
            return
        self.calendar.timer.stop()
        self.fold_transition.finish()
        self.fold_transition.close()
        self.calendar.day_timer.stop()
        self.geometry_timer.stop()
        self.save_geometry()
        self.timer.stop()
        self.clock_timer.stop()
        self.activity_timer.stop()
        # Hide and stop answering activations before waiting: a relaunch then waits for the lock.
        # Qt still emits lastWindowClosed once the close is accepted.
        self.hide()
        if self.activation_server is not None:
            self.activation_server.close()
        if self.activity_job is not None:
            self.activity_job.wait()
        if self.calendar.job is not None:
            self.calendar.job.wait()
        # Workers have a 30-second deadline; retain their QObjects until they finish.
        for job in self.jobs:
            job.wait()
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    appearance = parser.add_mutually_exclusive_group()
    appearance.add_argument("--compact", action="store_true", help="Open the compact widget (default)")
    appearance.add_argument("--desktop", action="store_true", help="Open the optional desktop dashboard")
    parser.add_argument("--check", action="store_true", help="Read live quotas without opening a window")
    parser.add_argument("--smoke", metavar="PNG", help="Render the real widget offscreen, then exit")
    parser.add_argument("--theme", choices=THEMES, help="Initial appearance")
    args = parser.parse_args()
    if args.check:
        failed = False
        for key, provider in PROVIDERS.items():
            try:
                print(json.dumps({"service": key, **provider()}, ensure_ascii=False))
            except Exception as error:
                failed = True
                print(json.dumps({"service": key, "error": str(error) if isinstance(error, QuotaError) else "Invalid response"},
                                 ensure_ascii=False))
        return int(failed)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setFont(QFont("Noto Sans CJK KR", 10))
    # Keep one desktop instance. No quota data or credentials written here.
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache"))
    runtime.mkdir(parents=True, exist_ok=True)
    socket_path = runtime / "ai-quota-widget.sock"
    mode = "desktop" if args.desktop else "compact"
    with (runtime / ("ai-quota-widget-smoke.lock" if args.smoke else "ai-quota-widget.lock")).open("w") as lock:
        # A starting, closing or mode-switching instance holds the lock without answering "ok".
        deadline = time.monotonic() + 35
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if args.smoke or activate_existing(socket_path, mode) == "ok":
                    return 0
                if time.monotonic() > deadline:
                    QMessageBox.warning(None, APP_NAME, "기존 위젯에 연결하지 못했습니다.\n기존 위젯을 종료한 뒤 다시 실행해 주세요.")
                    return 1
                time.sleep(0.2)
        if args.desktop:
            from desktop import DesktopWindow
            widget = DesktopWindow(smoke=bool(args.smoke))
        else:
            widget = Widget(smoke=bool(args.smoke))
        if args.theme:
            widget.set_theme(args.theme)
        widget.show()
        if not args.smoke:
            server = listen_for_activation(widget, socket_path, mode)
            widget.bring_to_front()
        if args.smoke:
            widget.resize(1160, 800) if args.desktop else widget.resize(430, 780)
            def capture():
                if widget.jobs:
                    QTimer.singleShot(100, capture)
                    return
                widget.grab().save(args.smoke)
                widget.close()
                app.quit()
            QTimer.singleShot(100, capture)
        result = app.exec()
        if not args.smoke:
            server.close()
        return result


if __name__ == "__main__":
    sys.exit(main())
