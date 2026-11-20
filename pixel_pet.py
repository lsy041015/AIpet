"""Procedural pixel mascot: a wide orange blocky creature with a neon glow."""
from functools import lru_cache
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QRadialGradient

DURATIONS = ((280,110,110,140,140,320), (120,)*7+(220,), (120,)*7+(220,),
             (140,140,140,280), (140,140,140,140,280), (140,)*7+(240,),
             (150,)*5+(260,), (120,)*5+(220,), (150,)*5+(280,))
ANIMATION_NAMES = ("기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패", "확인 대기", "작업 중", "결과 확인")

SIZE, PX, GROUND = 96, 5, 84  # frame px, px per mascot cell, feet baseline
BODY, SHADE, EYE = QColor("#D97757"), QColor("#B4593B"), QColor("#2B1A14")
GLOW, BANG, SPARK = QColor("#ff6a1a"), QColor("#ffb454"), QColor("#ffe3c4")
LEGS = (1, 3, 8, 10)  # leg columns inside the 12-cell body: two left, two right
EYES = (2, 9)

BASE = dict(dy=0, sink=0, lean=0, h=6, wide=0, legs=(0, 0, 0, 0), arm_l="mid", arm_r="mid",
            eyes="open", look=0, dim=False, bang=False, sparks=())
UP = dict(arm_l="up", arm_r="up")
STEP = [dict(legs=(1, 0, 1, 0), sink=-2, arm_l="down"), dict(sink=1), dict(legs=(0, 1, 0, 1), sink=-2, arm_r="down"), dict(sink=1)]
FAIL = dict(dim=True, arm_l="down", arm_r="down")
POSES = (
    [{}, dict(sink=2), dict(sink=2), dict(eyes="blink"), {}, dict(sink=1)],
    [dict(s, look=1, lean=2) for s in STEP * 2],
    None,  # mirrored walk right
    [dict(arm_r="high"), dict(arm_r="up"), dict(arm_r="high", eyes="happy"), dict(arm_r="up", eyes="happy")],
    [dict(sink=4, h=5, wide=1), dict(UP, dy=-10, sink=-2), dict(UP, dy=-16, eyes="happy"), dict(dy=-7, sink=-2), dict(sink=4, h=5, wide=1)],
    [dict(UP, dy=-3), dict(FAIL, eyes="x"), dict(FAIL, eyes="x", sink=2), dict(FAIL, eyes="x", h=5, sink=3),
     dict(FAIL, eyes="flat", h=5, wide=1, sink=4), dict(FAIL, eyes="flat", h=4, wide=1, sink=5),
     dict(FAIL, eyes="x", h=5, wide=1, sink=4), dict(FAIL, eyes="flat", h=4, wide=1, sink=5)],
    [dict(look=-1, bang=True), dict(look=-1), dict(bang=True), dict(look=1, bang=True), dict(look=1), dict(bang=True)],
    [dict(legs=(1, 0, 0, 0), arm_l="down", sparks=((-30, -20),)), dict(legs=(0, 0, 0, 1), arm_r="down", sparks=((28, -26),)),
     dict(legs=(1, 0, 0, 0), arm_l="down", sparks=((-24, -30), (32, -14))), dict(legs=(0, 0, 0, 1), arm_r="down"),
     dict(legs=(1, 0, 0, 0), arm_l="down", sparks=((30, -24),)), dict(legs=(0, 0, 0, 1), arm_r="down", sparks=((-32, -14), (24, -32)))],
    [dict(eyes="happy", sink=3), dict(UP, eyes="happy", dy=-6, sink=-2), dict(UP, eyes="happy", dy=-11, sparks=((-30, -18), (30, -22))),
     dict(eyes="happy", dy=-5), dict(eyes="happy", sink=3), dict(eyes="happy", sparks=((-26, -26), (32, -12)))],
)


def _dim(color):
    h, s, v, a = color.getHsv()
    return QColor.fromHsv(h, s // 3, v * 3 // 4, a)


def _draw(p, s):
    P, w, h = PX, 12 + 2 * s["wide"], s["h"]
    body, shade = (_dim(BODY), _dim(SHADE)) if s["dim"] else (BODY, SHADE)
    ground = GROUND + s["dy"]
    bottom = ground - 2 * P + s["sink"]
    top, left = bottom - h * P, SIZE // 2 - w * P // 2
    glow = QRadialGradient(QPointF(SIZE / 2, top + h * P / 2), 46)
    alpha = 45 if s["dim"] else 110
    for stop, a in ((0, alpha), (.45, alpha * 2 // 5), (1, 0)):
        glow.setColorAt(stop, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), a))
    p.fillRect(0, 0, SIZE, SIZE, glow)
    for i, column in enumerate(LEGS):
        x = left + (column + s["wide"]) * P
        p.fillRect(x, bottom, P, ground - s["legs"][i] * P - bottom, shade)
    left += s["lean"]
    p.fillRect(left, top, w * P, h * P, body)
    p.fillRect(left, bottom - P, w * P, P, shade)
    for side, arm in ((0, s["arm_l"]), (1, s["arm_r"])):
        x = left + w * P if side else left - 2 * P
        y = top + (h - 2) // 2 * P + (P if arm == "down" else 0)
        if arm == "up":
            p.fillRect(x, top - P, 2 * P, 2 * P, body)
        elif arm == "high":
            p.fillRect(x + (0 if side else P), top - P, P, P, body)
            p.fillRect(x, top - 3 * P, 2 * P, 2 * P, body)
        else:
            p.fillRect(x, y, 2 * P, 2 * P, body)
            p.fillRect(x, y + P, 2 * P, P, shade)
    for column in EYES:
        x, y = left + (column + s["wide"] + s["look"]) * P, top + P
        cells = {"open": ((0, 0), (0, 1)), "happy": ((-1, 1), (0, 0), (1, 1)),
                 "x": ((-1, 0), (1, 0), (0, 1), (-1, 2), (1, 2))}.get(s["eyes"], ())
        for cx, cy in cells:
            p.fillRect(x + cx * P, y + cy * P, P, P, EYE)
        if s["eyes"] in ("blink", "flat"):
            p.fillRect(x - (P // 2 if s["eyes"] == "flat" else 0), y + P, P * (2 if s["eyes"] == "flat" else 1), 2, EYE)
    if s["bang"]:
        p.fillRect(SIZE // 2 - 2, top - 26, 4, 12, BANG)
        p.fillRect(SIZE // 2 - 2, top - 11, 4, 4, BANG)
    for dx, dy in s["sparks"]:
        cx, cy = SIZE // 2 + dx, top + 10 + dy
        p.fillRect(cx - 1, cy - 4, 2, 8, SPARK)
        p.fillRect(cx - 4, cy - 1, 8, 2, SPARK)


@lru_cache(maxsize=None)
def frame_image(row, frame):
    if row == 2:
        return frame_image(1, frame).mirrored(True, False)
    image = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    _draw(p, dict(BASE, **POSES[row][frame]))
    p.end()
    return image


@lru_cache(maxsize=1)
def atlas():
    """All frames on one sheet (row per animation); also warms the frame cache."""
    sheet = QImage(SIZE * max(map(len, DURATIONS)), SIZE * len(DURATIONS), QImage.Format_ARGB32_Premultiplied)
    sheet.fill(Qt.transparent)
    p = QPainter(sheet)
    for row, durations in enumerate(DURATIONS):
        for frame in range(len(durations)):
            p.drawImage(frame * SIZE, row * SIZE, frame_image(row, frame))
    p.end()
    return sheet

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
    p.drawImage(0, 0, frame_image(row, frame))
    if widget.mood() != "happy":
        p.fillRect(84,2,10,10,QColor("#ffb454" if widget.mood() == "low" else "#70caff"))
    if widget.hasFocus() and widget.testAttribute(Qt.WA_KeyboardFocusChange):
        p.setPen(QPen(QColor("#70caff"),1,Qt.DashLine))
        p.drawRect(1,1,93,93)
    p.end()
