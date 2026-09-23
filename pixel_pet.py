"""Original Tiny CRT frames with a display-only white/sky palette filter."""
from functools import lru_cache
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImage, QPainter, QColor, QPen

DURATIONS = ((280,110,110,140,140,320), (120,)*7+(220,), (120,)*7+(220,),
             (140,140,140,280), (140,140,140,140,280), (140,)*7+(240,),
             (150,)*5+(260,), (120,)*5+(220,), (150,)*5+(280,))
ANIMATION_NAMES = ("기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패", "확인 대기", "작업 중", "결과 확인")

@lru_cache(maxsize=1)
def atlas():
    source = QImage(str(Path(__file__).parent / "assets/tiny-crt-source.webp"))
    if source.width() != 1536 or source.height() != 1872:
        raise ValueError("Tiny CRT atlas missing or invalid")
    source = source.convertToFormat(QImage.Format_RGBA8888)
    data = bytearray(source.constBits())
    for offset in range(0, len(data), 4):
        r, g, b, a = data[offset:offset + 4]
        if not a:
            continue
        if r > g * 1.35 and b > g * 1.35 and min(r, b) > 45:
            # Discard source chroma-key background residue at display time.
            data[offset + 3] = 0
        elif g > r * 1.12 and g > b * 1.1 and g > 55:
            data[offset:offset + 3] = bytes((min(255,int(g*.55)), min(255,int(g*.88)), min(255,int(g*1.1))))
        elif r > b * 1.12 and g > b * 1.04 and r > 65:
            shade = min(255, int(r * 1.02))
            data[offset:offset + 3] = bytes((max(0,shade-6), max(0,shade-2), shade))
    return QImage(data, source.width(), source.height(), source.bytesPerLine(), QImage.Format_RGBA8888).copy()

def animation_row(widget):
    if widget.preview_row is not None:
        return widget.preview_row
    if widget.dragging:
        return 2 if getattr(widget, "drag_left", False) else 1
    if widget.underMouse() or widget.isDown():
        return 4
    if any(task['status'] == 'waiting' for task in widget.owner.agent_activity):
        return 6
    if widget.owner.agent_activity or widget.owner.jobs or widget.owner.calendar.job:
        return 7
    if widget.status_row == 5:
        return 5
    if widget.mood() == "low":
        return 6
    if widget.mood() == "schedule":
        return 8
    return widget.status_row

def display_row(widget):
    row = animation_row(widget)
    return 0 if widget.preview_row is None and widget.cycles >= 3 and row == widget.animation_state else row

def paint_computer(widget):
    row = display_row(widget)
    stationary = widget.owner.reduce_motion.isChecked() and widget.preview_row is None
    frame = 0 if stationary or animation_row(widget) != widget.animation_state else widget.frame % len(DURATIONS[row])
    p = QPainter(widget)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    p.drawImage(QRectF(4,0,88,96), atlas(), QRectF(frame*192,row*208,192,208))
    if widget.mood() != "happy":
        p.fillRect(84,2,10,10,QColor("#ffb454" if widget.mood() == "low" else "#70caff"))
    if widget.hasFocus() and widget.testAttribute(Qt.WA_KeyboardFocusChange):
        p.setPen(QPen(QColor("#70caff"),1,Qt.DashLine))
        p.drawRect(1,1,93,93)
    p.end()
