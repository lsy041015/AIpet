"""Procedural pixel mascot: the orange Claude critter drawn cell by cell (12x8 base grid, 6 px cells)."""
import random
from functools import lru_cache
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QRadialGradient

SIZE, PX, GROUND, COLS = 96, 6, 13, 16  # frame px, px per cell, grid row of the feet, grid columns
BODY, SHADE, EYE = QColor("#D97757"), QColor("#B4593B"), QColor("#000000")
GLOW, BANG, SNORE = QColor("#ff6a1a"), QColor("#ffb454"), QColor("#f2d3c4")

# 'o' body, 'd' dark orange, '#' eye, '_' half-closed eye, '-' thin closed eye on body, '!' alert, 'z' snore.
# Rows are bottom-aligned on GROUND.
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


def eyes(rows, ch):
    return tuple(line.replace("#", ch) for line in rows)


def face(eye_row, rows=BASE_POSE):
    return (rows[0], eye_row) + tuple(rows[2:])


def flip(rows):
    """Mirror rows within their own width so the figure keeps its grid column."""
    width = max(map(len, rows))
    return tuple(line.ljust(width, ".")[::-1] for line in rows)


def blink(rows, x=2):
    """Half-closed, shut, half-closed: a natural lid close and open."""
    return [F(eyes(rows, ch), x=x) for ch in "_-_"]


EYES_L, EYES_R = "..#oooo#oo..", "..oo#oooo#.."  # eyes shifted one cell
TURN_R = WALK_BODY + (LEG, LEG)  # 3/4 view facing right
TURN_L = flip(TURN_R)
DIP = (TOP, EYES, ARM, ARM, TOP, LEG, LEG)  # one row shorter: anticipation and landing squash
SIT = STAND  # legs hidden, body on the ground
ARMS_UP = ("o.oooooooo.o", "o.o#oooo#o.o", ARM, ARM, TOP, TOP, LEG, LEG)
TALL = ("o..........o", "o.oooooooo.o", "o.o-oooo-o.o", ARM, ARM, TOP, TOP, TOP, LEG, LEG)
WIGGLE = (TOP, "ooo#oooo#o..", "oooooooooo..", "..oooooooooo", "..oooooooooo", TOP, LEG, LEG)
STEP_L, STEP_R = BASE_POSE[:7] + ("..o.o.......",), BASE_POSE[:7] + (".......o.o..",)
Z_GLYPH = [(x, y) for y, line in enumerate(("#####", "...#.", "..#..", ".#...", "#####"))
           for x, ch in enumerate(line) if ch == "#"]  # 2 px dots: a 10 px z inside 2x2 cells


def snooze(row, column):
    """Sitting asleep with a small z (2x2 cells) whose top is on grid row `row`; the sitting head starts on row 8."""
    return F(("." * column + "z",) + ("",) * (7 - row) + eyes(SIT, "-"))


def stroll():
    walk = [F(WALK_BODY + legs, x=x) for legs, x in zip(WALK_LEGS * 2, (2, 2, 2, 3, 3, 3, 3, 4, 4, 4))]
    back = [dict(f, rows=flip(f["rows"]), x=6 - f["x"]) for f in walk]
    return ([F(BASE_POSE), F(face(EYES_R)), F(TURN_R)] + walk +
            [F(TURN_R, x=4), F(face(EYES_R), x=4), F(BASE_POSE, x=4), F(face(EYES_L), x=4), F(TURN_L, x=4)] + back +
            [F(TURN_L), F(face(EYES_L)), F(BASE_POSE)])


def work(eye_row=WORK_HEAD[1], hand=0):
    return F((WORK_HEAD[0], eye_row) + TYPING[hand] + (WORK_BASE,), x=0)


WORK_FRONT = "..o#oooo#o....d."
WORK_REACH = ("o.oooooooo.o....", "o.o#oooo#o.o..d.", "oooooooooooo.d..", "..oooooooo..d...", WORK_BASE)
WORK_STRETCH = ("o..........o....", "o.oooooooo.o....", "o.o-oooo-o.o..d.", "oooooooooooo.d..", "..oooooooo..d...",
                WORK_BASE)
# Front-facing sit beside the shut laptop: the step between standing and typing.
SITTING = ("..oooooooo....", "..o#oooo#o....", "oooooooooooo..", "oooooooooooo..", "..oooooooo....", "..oooooooodddd")
FAIL = [F(BASE_POSE), F((TOP, FLAT) + BASE_POSE[2:], dim=True), F((TOP, FLAT, ARM, ARM, TOP, LEG), dim=True),
        F((TOP, FLAT, ARM, ARM), dim=True)]

# Rows 0-8 are the states the app maps to (meanings unchanged). Later rows are idle fidgets, small idle
# moves and the transitions that join two poses without a snap.
POSES = [
    [F(BASE_POSE)] + blink(BASE_POSE),
    [F(WALK_BODY + legs) for legs in WALK_LEGS],
    None,  # mirrored walk right
    [F(BASE_POSE), F(SQUASH),
     F(("...........oo", "..........oo.", TOP, EYES, "oooooooooo..", "oooooooooo..", TOP, TOP, TOP, LEG, LEG)),
     *[F((hand, WAVE_STUB, WAVE_STUB) + WAVE_BODY, x=1) for hand in (".........oo..", "........oo...") * 3],
     F(WAVE_BODY[:1] + ("...o##ooodooo", ".oooooooddooo") + WAVE_BODY[3:], x=1), F(BASE_POSE)],
    [F(SQUASH), F(TUCKED, lift=2), F(TUCKED, lift=3), F(TUCKED, lift=2), F(SQUASH), F(BASE_POSE)],
    FAIL,
    [_look(row, line, i % 2 == 0) for i, (row, line) in enumerate(LOOK)],
    [work(hand=hand) for hand in range(3)],
    [F(BASE_POSE), F(SQUASH), F(CHEER + (LEG,), lift=2), F(CHEER + (LEG,), lift=3), F(CHEER + (LEG,), lift=2),
     F((TOP, "ooo##oo##ooo", ARM, TOP, LEG, LEG)), F(CHEER + (LEG, LEG)), F(BASE_POSE)],
]
# Row 0's first frame is the still pose; live idle replaces its time with a random 2.5-7 s hold.
DURATIONS = [(2400, 50, 80, 70), (83,)*5, (83,)*5, (166,) + (83,)*9 + (166,), (83, 83, 166, 83, 83, 83),
             (166, 83, 83, 664), (249,)*8, (83,)*3, (83, 83, 83, 166, 83, 83, 249, 166)]
ANIMATION_NAMES = ["기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패", "확인 대기", "작업 중", "결과 확인"]


def _row(name, frames, durations):
    assert len(frames) == len(durations), name
    POSES.append(frames)
    DURATIONS.append(tuple(durations))
    ANIMATION_NAMES.append(name)
    return len(POSES) - 1


GLANCE_L = _row("왼쪽 흘끗", [F(BASE_POSE), F(face(EYES_L))], (1500, 1100))
GLANCE_R = _row("오른쪽 흘끗", [F(BASE_POSE), F(face(EYES_R))], (1500, 1100))
LOOK_AROUND = _row("두리번거리기", [F(BASE_POSE), F(face(EYES_L)), F(TURN_L), F(face(EYES_L)), F(BASE_POSE),
                               F(face(EYES_R)), F(TURN_R), F(face(EYES_R)), F(BASE_POSE)],
                   (200, 110, 900, 110, 220, 110, 900, 110, 200))
HOP = _row("콩 뛰기", [F(BASE_POSE), F(DIP), F(BASE_POSE), F(TUCKED, lift=1), F(TUCKED, lift=2), F(TUCKED, lift=1),
                     F(BASE_POSE), F(DIP), F(BASE_POSE)], (120, 120, 50, 60, 150, 60, 50, 120, 160))
STRETCH = _row("기지개", [F(BASE_POSE), F(DIP), F(ARMS_UP), F(TALL), F(ARMS_UP), F(BASE_POSE), F(DIP), F(BASE_POSE)],
               (180, 140, 120, 1100, 150, 120, 110, 200))
SOLO_WAVE = _row("혼자 손 흔들기", [F(BASE_POSE)] + POSES[3][2:10] + [F(BASE_POSE)], (160, 120) + (140,) * 6 + (140, 200))
WIGGLE_ARMS = _row("팔 흔들기", [F(BASE_POSE), F(WIGGLE), F(BASE_POSE), F(flip(WIGGLE))] * 2 + [F(BASE_POSE)],
                   (150,) + (130,) * 7 + (180,))
TURN = _row("옆모습", [F(BASE_POSE), F(face(EYES_R)), F(TURN_R)] + blink(TURN_R) +
            [F(TURN_R), F(face(EYES_R)), F(BASE_POSE)], (200, 120, 1300, 50, 80, 70, 700, 120, 200))
SHUFFLE = _row("제자리 걸음", [F(BASE_POSE), F(STEP_L), F(BASE_POSE), F(STEP_R)] * 2 + [F(BASE_POSE)],
               (150,) + (170,) * 7 + (200,))
STROLL = _row("산책", stroll(), (200, 120, 200) + (110,) * 10 + (500, 120, 200, 120, 200) + (110,) * 10 + (200, 120, 200))
SIT_DOWN = _row("앉기", [F(BASE_POSE), F(TUCKED), F(SIT)] + blink(SIT) + [F(SIT), F(TUCKED), F(BASE_POSE)],
                (180, 130, 1600, 50, 80, 70, 1400, 130, 200))
DOZE_IN = _row("졸기", [F(BASE_POSE), F(TUCKED), F(SIT), F(eyes(SIT, "_")), F(eyes(SIT, "-"))],
               (200, 160, 900, 500, 900))
DOZE = _row("쿨쿨", [snooze(5, 9), snooze(4, 10), snooze(3, 11), snooze(2, 12), F(eyes(SIT, "-"))], (700,) * 5)
WAKE = _row("잠 깨기", [F(eyes(SIT, "-")), F(eyes(SIT, "_")), F(SIT), F(TUCKED), F(BASE_POSE)], (160, 140, 220, 110, 150))
WORK_LOOK = _row("작업 중 쳐다보기", [work(), work(WORK_FRONT)] + blink(work(WORK_FRONT)["rows"], x=0) +
                 [work(WORK_FRONT), work()], (200, 1400, 50, 80, 70, 700, 200))
WORK_GLANCE = _row("작업 중 흘끗", [work(), work(WORK_FRONT), work()], (200, 700, 200))
WORK_BREAK = _row("작업 중 기지개", [work(), work(WORK_FRONT), F(WORK_REACH, x=0), F(WORK_STRETCH, x=0),
                                F(WORK_REACH, x=0), work(WORK_FRONT), work()], (200, 300, 130, 1100, 130, 250, 200))
WORK_ENTER = _row("노트북 펴기", [F(BASE_POSE), F(TUCKED), F(SITTING, x=1), work(WORK_FRONT), work()],
                  (150, 110, 160, 160, 120))
WORK_LEAVE = _row("노트북 닫기", [work(), work(WORK_FRONT), F(SITTING, x=1), F(TUCKED), F(BASE_POSE)],
                  (120, 160, 160, 110, 150))
FAIL_UP = _row("다시 일어나기", [FAIL[3], F((TOP, FLAT, ARM, ARM, TOP), dim=True), FAIL[2],
                               F((TOP, FLAT, ARM, ARM, TOP, TOP, LEG)), F((TOP, FLAT) + BASE_POSE[2:]), F(BASE_POSE)],
               (300, 120, 120, 120, 160, 200))
HOVER_L = _row("커서 보기 (왼쪽)", [F(face(EYES_L))] + blink(face(EYES_L)), (2000, 50, 80, 70))
HOVER_R = _row("커서 보기 (오른쪽)", [F(face(EYES_R))] + blink(face(EYES_R)), (2000, 50, 80, 70))

HOVER = {-1: HOVER_L, 0: 0, 1: HOVER_R}
LAPTOP = {7, WORK_LOOK, WORK_GLANCE, WORK_BREAK, WORK_ENTER}
# Idle fidgets: (weight, cooldown ms). Cooldowns keep any one of them from coming back too soon.
FIDGETS = {LOOK_AROUND: (3, 60_000), HOP: (2, 50_000), STRETCH: (2, 90_000), SOLO_WAVE: (1, 120_000),
           WIGGLE_ARMS: (2, 60_000), TURN: (2, 60_000), SHUFFLE: (2, 60_000), STROLL: (1, 120_000),
           SIT_DOWN: (2, 90_000)}
WORK_BREAKS = (WORK_LOOK, WORK_GLANCE, WORK_BREAK)
# The right-click preview menu: the states, then the idle fidgets, dozing and the work breaks.
PREVIEW_ROWS = tuple(range(9)) + tuple(FIDGETS) + (DOZE_IN, *WORK_BREAKS)
DOZE_AFTER = 120_000  # ms with no state change or hover before the pet nods off


class Life:
    """Picks the row and frame the pet shows next. Time is the pet's own ms clock (the frame waits it
    played), so a seeded rng replays a session exactly."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.clock = self.wait = 0
        self.row, self.frame, self.hold, self.queue = 0, 0, None, []
        self.last, self.ready, self.hop_ready, self.look = None, {}, 0, 0
        self.restart(0, "status")

    def restart(self, state, mode, reduced=False):
        """A new state: transition rows join the old pose to the new one, then the state plays."""
        old, self.cycles, self.frame, self.hold, self.queue = self.row, 0, 0, None, []
        self.settled = state == 0
        self.quiet = self.clock
        self.next_fidget = self.clock + self.rng.uniform(15_000, 60_000)
        self.next_break = self.clock + self.rng.uniform(20_000, 40_000)
        if reduced:
            self.row = state
            return
        start = HOVER[self.look] if mode == "hover" else state
        if mode == "hover" and self.clock >= self.hop_ready:
            start, self.hop_ready = HOP, self.clock + 20_000
        rows = ([WAKE] if old in (DOZE_IN, DOZE) else [WORK_LEAVE] if old in LAPTOP and start != 7
                else [FAIL_UP] if old == 5 and start != 5 else [])
        if start == 7 and old not in LAPTOP:
            rows.append(WORK_ENTER)
        self.row, *self.queue = rows + [start]
        if self.row in HOVER.values():
            self.hold = self.rng.uniform(1500, 4000)

    def play(self, row):
        self.row, self.frame, self.hold, self.queue = row, 0, None, []

    def duration(self, jitter=True):
        """ms to show the current frame; live playback varies it by up to 12% so loops never look mechanical."""
        ms = self.hold if self.frame == 0 and self.hold else DURATIONS[self.row][self.frame]
        self.wait = max(16, round(ms * self.rng.uniform(.88, 1.12) if jitter else ms))
        return self.wait

    def advance(self, state, mode):
        """Step one frame. Returns False when a preview has finished."""
        self.clock += self.wait
        self.frame += 1
        if self.frame < len(DURATIONS[self.row]):
            return True
        self.frame, self.hold = 0, None
        if mode == "preview":
            return False
        if self.queue:
            self.row = self.queue.pop(0)
            if self.row in HOVER.values():
                self.hold = self.rng.uniform(1500, 4000)
            return True
        finished = self.row
        self.cycles += finished == state
        self.row, self.hold = self.next_row(finished, state, mode)
        return True

    def look_at(self, side):
        """While hovered, the eyes follow the cursor side. Returns True when the shown frame changed."""
        changed, self.look = side != self.look, side
        if changed and self.row in HOVER.values() and not self.queue:
            self.play(HOVER[side])
            self.hold = self.rng.uniform(1500, 4000)
            return True
        return False

    def next_row(self, finished, state, mode):
        if mode == "hover":
            return HOVER[self.look], self.rng.uniform(1500, 4000)
        if mode == "held":  # dragged or agents running: the state row keeps going
            if state != 7:
                return state, None
            if self.clock >= self.next_break:
                self.next_break = self.clock + self.rng.uniform(20_000, 40_000)
                return self.rng.choice([row for row in WORK_BREAKS if row != finished]), None
            return 7, (self.rng.uniform(300, 1200) if self.rng.random() < .3 else None)  # typing pauses
        if not self.settled:
            if self.cycles < 3:
                return state, None
            self.settled = True
            if state in (5, 7):
                return (FAIL_UP if state == 5 else WORK_LEAVE), None
        return self.idle(finished)

    def idle(self, finished):
        """Mostly still with blinks every 2.5-7 s, a glance now and then, a fidget every 15-60 s."""
        now, rng = self.clock, self.rng
        if finished in (DOZE_IN, DOZE):
            return DOZE, None
        if now - self.quiet >= DOZE_AFTER:
            return DOZE_IN, None
        if now >= self.next_fidget:
            options = [row for row in FIDGETS if row != self.last and self.ready.get(row, 0) <= now]
            self.next_fidget = now + rng.uniform(15_000, 60_000)
            if options:
                row = rng.choices(options, [FIDGETS[row][0] for row in options])[0]
                self.last, self.ready[row] = row, now + FIDGETS[row][1]
                return row, None
        roll = rng.random()
        if finished == 0 and roll < .15:
            return 0, rng.uniform(150, 300)  # double blink
        if roll < .35:
            return rng.choice((GLANCE_L, GLANCE_R)), rng.uniform(2500, 7000)
        return 0, rng.uniform(2500, 7000)


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
    colors = {"o": body, "-": body, "_": body, "d": shade, "#": EYE, "!": BANG}
    painted = cells(frame)
    xs, ys = [x for x, _, ch in painted if ch not in "!z"], [y for _, y, ch in painted if ch not in "!z"]
    glow = QRadialGradient(QPointF((min(xs) + max(xs) + 1) * PX / 2, (min(ys) + max(ys) + 1) * PX / 2), 48)
    alpha = 45 if frame["dim"] else 110
    for stop, a in ((0, alpha), (.45, alpha * 2 // 5), (1, 0)):
        glow.setColorAt(stop, QColor(GLOW.red(), GLOW.green(), GLOW.blue(), a))
    p.fillRect(0, 0, SIZE, SIZE, glow)
    for x, y, ch in painted:
        if ch == "z":
            for gx, gy in Z_GLYPH:
                p.fillRect(x * PX + 1 + gx * 2, y * PX + 1 + gy * 2, 2, 2, SNORE)
            continue
        p.fillRect(x * PX, y * PX, PX, PX, colors[ch])
        if ch == "-":
            p.fillRect(x * PX, y * PX + 3, PX, 2, EYE)
        elif ch == "_":  # lid halfway down
            p.fillRect(x * PX, y * PX + 2, PX, 4, EYE)


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
    return widget.life.row

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
