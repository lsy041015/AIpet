"""Procedural pixel mascot: an orange blocky creature with a neon glow."""
from functools import lru_cache
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QRadialGradient

# Idle (row 0) runs at 12x these values when live, so its blink stays short.
DURATIONS = ((300, 250, 300, 250, 20, 200), (83,)*8, (83,)*8,
             (120, 100, 100, 100, 100, 160), (110, 90, 120, 90, 150), (120,)*7+(300,),
             (100, 83, 83, 400, 83, 83, 83, 83, 400, 140), (90, 90, 90, 90, 140, 90), (110, 90, 120, 90, 120, 240))
ANIMATION_NAMES = ("기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패", "확인 대기", "작업 중", "결과 확인")

SIZE, PX, GROUND, WIDTH, LEG = 96, 6, 88, 11, 3  # frame px, px per cell, feet baseline, body cells, leg cells
BODY, SHADE, EYE = QColor("#D97757"), QColor("#BF6649"), QColor("#2B1A14")
GLOW, BANG = QColor("#ff6a1a"), QColor("#ffb454")
LID, LID_EDGE = QColor("#9a9a9a"), QColor("#6c6c6c")
LEGS = (1, 3, 7, 9)  # two left, two right, wider middle gap
EYES = (3, 7)
ARMS = {"up": ((0, 0), (0, 1), (1, -1), (1, 0)), "wave": ((0, 1), (0, 2), (1, 0), (1, 1))}  # (cells outward, row)

BASE = dict(dy=0, sink=0, lean=0, h=6, wide=0, legs=(0, 0, 0, 0), arm_l="mid", arm_r="mid",
            eyes="open", look=0, eye_dy=0, dim=False, bang=False, laptop=False)
UP = dict(arm_l="up", arm_r="up")
HAPPY = dict(eyes="happy")
STEP = [dict(legs=(1, 0, 1, 0), sink=-1), {}, dict(legs=(0, 1, 0, 1), sink=-1), {}]
FAIL = dict(dim=True, arm_l="down", arm_r="down")
LOOK = [(0, 1), (-3, 1), (-6, 1), (-6, 1), (-3, 0), (0, 0), (3, 1), (6, 1), (6, 1), (0, 0)]
WORK = dict(laptop=True, eye_dy=2, sink=LEG * PX - 4)
POSES = (
    [{}, dict(sink=1), {}, dict(sink=1), dict(eyes="blink"), {}],
    [dict(s, look=2 * PX, lean=2, arm_l="none") for s in STEP * 2],
    None,  # mirrored walk right
    [dict(sink=4, h=5, wide=1), dict(HAPPY, arm_r="up"), dict(HAPPY, arm_r="wave"), dict(HAPPY, arm_r="up"),
     dict(HAPPY, arm_r="wave"), dict(HAPPY)],
    [dict(sink=4, h=5, wide=1), dict(UP, dy=-10, sink=-2), dict(UP, eyes="happy", dy=-16), dict(dy=-7, sink=-2), dict(sink=4, h=5, wide=1)],
    [dict(UP, dy=-3), dict(FAIL, eyes="x"), dict(FAIL, eyes="x", sink=3), dict(FAIL, eyes="x", h=5, sink=5),
     dict(FAIL, eyes="flat", h=5, wide=1, sink=8), dict(FAIL, eyes="flat", h=4, wide=1, sink=10),
     dict(FAIL, eyes="x", h=5, wide=1, sink=8), dict(FAIL, eyes="flat", h=4, wide=1, sink=10)],
    [dict(look=look, bang=bool(bang), sink=1 if i in (3, 8) else 0, eyes="blink" if i == 9 else "open")
     for i, (look, bang) in enumerate(LOOK)],
    [dict(WORK, arm_l=-1, arm_r=1), dict(WORK, arm_l=1, arm_r=-1), dict(WORK, arm_l=-1, arm_r=1),
     dict(WORK, arm_l=1, arm_r=-1), dict(WORK, eyes="blink"), dict(WORK, arm_l=1, arm_r=-1)],
    [dict(HAPPY, sink=4), dict(UP, eyes="happy", dy=-6, sink=-2), dict(HAPPY, arm_l="wave", arm_r="wave", dy=-10),
     dict(UP, eyes="happy", dy=-5), dict(HAPPY, sink=4), dict(HAPPY)],
)


def _dim(color):
    h, s, v, a = color.getHsv()
    return QColor.fromHsv(h, s // 3, v * 3 // 4, a)


def _draw(p, s):
    P, w, h = PX, WIDTH + 2 * s["wide"], s["h"]
    body, shade = (_dim(BODY), _dim(SHADE)) if s["dim"] else (BODY, SHADE)
    ground = GROUND + s["dy"]
    bottom = ground - LEG * P + s["sink"]
    top, left = bottom - h * P, SIZE // 2 - w * P // 2
    glow = QRadialGradient(QPointF(SIZE / 2, top + h * P / 2), 48)
    alpha = 45 if s["dim"] else 110
    for stop, a in ((0, alpha), (.45, alpha * 2 // 5), (1, 0)):
        glow.setColorAt(stop, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), a))
    p.fillRect(0, 0, SIZE, SIZE, glow)
    if not s["laptop"]:
        for i, column in enumerate(LEGS):
            x = left + (column + s["wide"]) * P
            p.fillRect(x, bottom, P, ground - s["legs"][i] * P - bottom, shade)
    left += s["lean"]
    p.fillRect(left, top, w * P, h * P, body)
    p.fillRect(left, bottom - 2, w * P, 2, shade)
    for side, arm in ((0, s["arm_l"]), (1, s["arm_r"])):
        edge = left + w * P if side else left - P
        out = P if side else -P
        if arm in ARMS:
            for dx, row in ARMS[arm]:
                p.fillRect(edge + dx * out, top + row * P, P, P, body)
        elif arm != "none":
            y = top + (h - 2) // 2 * P + (P if arm == "down" else 0) + (arm if isinstance(arm, int) else 0)
            p.fillRect(edge, y, P, 2 * P, body)
            p.fillRect(edge, y + 2 * P - 2, P, 2, shade)
    for column in EYES:
        x, y = left + (column + s["wide"]) * P + s["look"], top + P + s["eye_dy"]
        cells = {"open": ((0, 0), (0, 1)), 
                 "x": ((-1, 0), (1, 0), (0, 1), (-1, 2), (1, 2))}.get(s["eyes"], ())
        for cx, cy in cells:
            p.fillRect(x + cx * P, y + cy * P, P, P, EYE)
        if s["eyes"] == "happy":  # thin ^ from 4px blocks
            for step in range(-2, 3):
                p.fillRect(x + 1 + 3 * step, y + 2 + 3 * abs(step), 4, 4, EYE)
        if s["eyes"] in ("blink", "flat"):
            p.fillRect(x - (P // 2 if s["eyes"] == "flat" else 0), y + P, P * (2 if s["eyes"] == "flat" else 1), 2, EYE)
    if s["bang"]:
        p.fillRect(SIZE // 2 - 2, top - 26, 4, 12, BANG)
        p.fillRect(SIZE // 2 - 2, top - 11, 4, 4, BANG)
    if s["laptop"]:
        # Lid seen from behind, sitting on a thin base in front of the mascot.
        lid = (SIZE // 2 - 5 * P, ground - 20, 10 * P, 16)
        p.fillRect(*lid, LID_EDGE)
        p.fillRect(lid[0] + 2, lid[1] + 2, lid[2] - 4, lid[3] - 2, LID)
        p.fillRect(SIZE // 2 - 2, lid[1] + 7, 4, 4, LID.lighter(125))
        p.fillRect(SIZE // 2 - 6 * P, ground - 4, 12 * P, 4, LID_EDGE)


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
