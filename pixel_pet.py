"""Procedural pixel mascot: the orange Claude critter drawn cell by cell (12x8 base grid, 6 px cells)."""
from functools import lru_cache
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QRadialGradient

SIZE, PX, GROUND, COLS = 96, 6, 13, 16  # frame px, px per cell, grid row of the feet, grid columns
BODY, SHADE, EYE = QColor("#D97757"), QColor("#B4593B"), QColor("#000000")
GLOW, BANG = QColor("#ff6a1a"), QColor("#ffb454")

# 'o' body, 'd' dark orange, '#' eye, '-' thin closed eye on body, '!' alert. Rows are bottom-aligned on GROUND.
TOP, EYES, ARM, LEG = "..oooooooo..", "..o#oooo#o..", "oooooooooooo", "..o.o..o.o.."
STAND = (TOP, EYES, ARM, ARM, TOP, TOP)
BASE_POSE = STAND + (LEG, LEG)
SQUASH = (TOP, "ooo#oooo#ooo", ARM, TOP, LEG, LEG)  # 4-row body, 2 rows lower, arms and eyes drop
TUCKED = STAND + (LEG,)
CHEER = ("o.oooooooo.o", "o.o##oo##o.o", ARM, ARM, TOP, TOP)  # both arms up, happy squint
FLAT = "..o--oo--o.."
ALERT = ("..........!.", "..........!.", "", "..........!.", "")


def F(rows, x=2, lift=0, bang=False, dim=False):
    """One frame: grid rows, grid column of rows[i][0], cells raised off the ground."""
    return dict(rows=(ALERT if bang else ()) + tuple(rows), x=x, lift=lift, dim=dim)


WALK_BODY = (TOP, "..oo#dooo#..", "ooooooooooo.", "ooooooooooo.", TOP, TOP)  # 3/4 view facing right
WALK_LEGS = (("..o.o..o.o..", ".o..o.oo.o..", ".o....o...o."), ("..oo.o.o.o..", "..o..o.o.o..", "......o..o.."),
             ("...o.o..o...", "...o.o..o..."), ("..o.o..o.o..", "..oo....o..."), (".o.oo..o.oo.", ".o.....o...."))
WAVE_TOP = "...oooooooo.."  # wave rows start one column left to make room for the raised arm
WAVE_BODY = (WAVE_TOP, "...o##ooodo..", ".oooooooddo..", ".oooooooooo..", WAVE_TOP, WAVE_TOP, "...o.o..o.o..", "...o.o..o.o..")
WAVE_STUB = ".........o..."
LOOK = ((1, EYES), (1, "..o#dooo#d.."), (0, "..o#dooo#d.."), (0, "..#dooo#do.."), (2, "oo#dooo#dooo"),
        (2, "ooo#oooo#ooo"), (1, "..o#dooo#d.."), (1, EYES))  # (body row, eye row) each held 3 frames
# Sitting 3/4 view facing right (rows start at grid column 0): 8x5 body, back arm, laptop base + diagonal lid.
WORK_HEAD = ("..oooooooo......", "..oo#dooo#....d.")
WORK_BASE = "..oooooooodddd.."
TYPING = ((".ooooooooo...d..", ".oooooooooood..."),   # hand rests on the laptop base
          (".ooooooooooo.d..", ".ooooooooo..d..."),   # hand lifts one cell
          (".ooooooooo...d..", ".oooooooooo.d..."))   # hand shifts one cell left


def _look(row, line, bang):
    rows = [TOP, TOP] + list(STAND[2:]) + [LEG, LEG]
    rows[row] = line
    return F(rows, bang=bang)


POSES = (
    [F(BASE_POSE), F((TOP, TOP) + BASE_POSE[2:]), F(BASE_POSE)],
    [F(WALK_BODY + legs) for legs in WALK_LEGS],
    None,  # mirrored walk right
    [F(BASE_POSE), F(SQUASH),
     F(("...........oo", "..........oo.", TOP, EYES, "oooooooooo..", "oooooooooo..", TOP, TOP, TOP, LEG, LEG)),
     *[F((hand, WAVE_STUB, WAVE_STUB) + WAVE_BODY, x=1) for hand in (".........oo..", "........oo...") * 3],
     F(WAVE_BODY[:1] + ("...o##ooodooo", ".oooooooddooo") + WAVE_BODY[3:], x=1), F(BASE_POSE)],
    [F(SQUASH), F(TUCKED, lift=2), F(TUCKED, lift=3), F(TUCKED, lift=2), F(SQUASH), F(BASE_POSE)],
    [F(BASE_POSE), F((TOP, FLAT) + BASE_POSE[2:], dim=True), F((TOP, FLAT, ARM, ARM, TOP, LEG), dim=True),
     F((TOP, FLAT, ARM, ARM), dim=True)],
    [_look(row, line, i % 2 == 0) for i, (row, line) in enumerate(LOOK)],
    [F(WORK_HEAD + hand + (WORK_BASE,), x=0) for hand in TYPING],
    [F(BASE_POSE), F(SQUASH), F(CHEER + (LEG,), lift=2), F(CHEER + (LEG,), lift=3), F(CHEER + (LEG,), lift=2),
     F((TOP, "ooo##oo##ooo", ARM, TOP, LEG, LEG)), F(CHEER + (LEG, LEG)), F(BASE_POSE)],
)
# Idle (row 0) runs at 12x these values when live: ~3 s still, a one-frame (84 ms) blink, ~2 s still.
DURATIONS = ((250, 7, 167), (83,)*5, (83,)*5, (166,) + (83,)*9 + (166,), (83, 83, 166, 83, 83, 83),
             (166, 83, 83, 664), (249,)*8, (83,)*3, (83, 83, 83, 166, 83, 83, 249, 166))
ANIMATION_NAMES = ("기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패", "확인 대기", "작업 중", "결과 확인")


def _dim(color):
    h, s, v, a = color.getHsv()
    return QColor.fromHsv(h, s // 3, v * 3 // 4, a)


def cells(frame):
    """(column, row, char) for every painted cell of a frame spec."""
    top = GROUND + 1 - frame["lift"] - len(frame["rows"])
    return [(frame["x"] + c, top + r, ch) for r, line in enumerate(frame["rows"])
            for c, ch in enumerate(line) if ch != "."]


def _draw(p, frame):
    body, shade = (_dim(BODY), _dim(SHADE)) if frame["dim"] else (BODY, SHADE)
    colors = {"o": body, "-": body, "d": shade, "#": EYE, "!": BANG}
    painted = cells(frame)
    xs, ys = [x for x, _, ch in painted if ch != "!"], [y for _, y, ch in painted if ch != "!"]
    glow = QRadialGradient(QPointF((min(xs) + max(xs) + 1) * PX / 2, (min(ys) + max(ys) + 1) * PX / 2), 48)
    alpha = 45 if frame["dim"] else 110
    for stop, a in ((0, alpha), (.45, alpha * 2 // 5), (1, 0)):
        glow.setColorAt(stop, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), a))
    p.fillRect(0, 0, SIZE, SIZE, glow)
    for x, y, ch in painted:
        p.fillRect(x * PX, y * PX, PX, PX, colors[ch])
        if ch == "-":
            p.fillRect(x * PX, y * PX + 3, PX, 2, EYE)


@lru_cache(maxsize=None)
def frame_image(row, frame):
    if row == 2:
        return frame_image(1, frame).mirrored(True, False)
    image = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    _draw(p, POSES[row][frame])
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
