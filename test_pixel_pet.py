import unittest

from PySide6.QtWidgets import QApplication

from unittest.mock import patch

import pixel_pet
from pixel_pet import (ANIMATION_NAMES, BASE_POSE, BODY, COLS, DOZE, DOZE_AFTER, DOZE_IN, DURATIONS, EYE, F, FIDGETS,
                       GLANCE_L, GLANCE_R, HOP, POSES, PREVIEW_ROWS, PX, SIZE, WAKE, Life, atlas, cells, frame_image)


def step(a, b):
    """Cells that change between two frames, letting the whole figure move by one cell."""
    sa, sb = set(cells(a)), set(cells(b))
    return min(len({(x + dx, y + dy, c) for x, y, c in sa} ^ sb) for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def run(life, until, state=0, mode="status"):
    """Play a Life like the pet's timer does; yields after every step."""
    while life.clock < until:
        life.duration()
        life.advance(state, mode)
        yield life

BASE = ("..oooooooo..",
        "..o#oooo#o..",
        "oooooooooooo",
        "oooooooooooo",
        "..oooooooo..",
        "..oooooooo..",
        "..o.o..o.o..",
        "..o.o..o.o..")


class PixelPetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_frame_counts_match_durations(self):
        self.assertEqual(len(DURATIONS), len(POSES))
        self.assertEqual(len(ANIMATION_NAMES), len(POSES))
        self.assertEqual(ANIMATION_NAMES[:9], ["기본 대기", "오른쪽 이동", "왼쪽 이동", "인사", "점프", "실패",
                                               "확인 대기", "작업 중", "결과 확인"])
        self.assertGreaterEqual(len(FIDGETS), 6)
        self.assertTrue(set(FIDGETS) <= set(PREVIEW_ROWS) and set(range(9)) <= set(PREVIEW_ROWS))
        for row, poses in enumerate(POSES):
            if poses is not None:
                self.assertEqual(len(poses), len(DURATIONS[row]), row)
                for frame in poses:  # every cell stays inside the 16x16 grid
                    for x, y, _ in cells(frame):
                        self.assertTrue(0 <= x < COLS and 0 <= y < COLS, (row, x, y))
                        if row == 7:  # working pose keeps a 1-cell margin
                            self.assertTrue(1 <= x < COLS - 1 and 1 <= y < COLS - 1, (row, x, y))
        self.assertEqual(len(DURATIONS[1]), len(DURATIONS[2]))

    def test_every_frame_renders_orange_mascot(self):
        body = BODY.rgb() & 0xFFFFFF
        for row, durations in enumerate(DURATIONS):
            images = []
            for frame in range(len(durations)):
                image = frame_image(row, frame)
                self.assertEqual((image.width(), image.height()), (SIZE, SIZE))
                for x, y in ((0, 0), (SIZE - 1, 0), (0, SIZE - 1), (SIZE - 1, SIZE - 1)):
                    self.assertEqual(image.pixelColor(x, y).alpha(), 0, (row, frame, x, y))
                pixels = {image.pixel(x, y) & 0xFFFFFF for x in range(SIZE) for y in range(SIZE)
                          if image.pixelColor(x, y).alpha() == 255}
                if not (POSES[row] or POSES[1])[frame]["dim"]:  # fail frames are desaturated on purpose
                    self.assertIn(body, pixels, (row, frame))
                images.append(image)
            self.assertNotEqual(images[0], images[1], row)
        self.assertIs(frame_image(3, 1), frame_image(3, 1))

    def test_base_pose_matches_reference_grid(self):
        image = frame_image(0, 0)
        colors = {"o": BODY.rgb() & 0xFFFFFF, "#": EYE.rgb() & 0xFFFFFF}
        left, top = (SIZE - 12 * PX) // 2, 6 * PX  # 72x48 centered, feet on grid row 13
        for r, line in enumerate(BASE):
            for c, ch in enumerate(line):
                color = image.pixelColor(left + c * PX + PX // 2, top + r * PX + PX // 2)
                if ch == ".":
                    self.assertLess(color.alpha(), 255, (r, c))
                else:
                    self.assertEqual((color.alpha(), color.rgb() & 0xFFFFFF), (255, colors[ch]), (r, c))
        half, shut = frame_image(0, 1), frame_image(0, 2)  # the lid comes down over two frames
        self.assertEqual(half.pixel(left + 3 * PX + 3, top + PX) & 0xFFFFFF, colors["o"])
        self.assertEqual(half.pixel(left + 3 * PX + 3, top + PX + 3) & 0xFFFFFF, colors["#"])
        self.assertEqual(shut.pixel(left + 3 * PX + 3, top + PX + 1) & 0xFFFFFF, colors["o"])
        self.assertEqual(shut.pixel(left + 3 * PX + 3, top + PX + 5) & 0xFFFFFF, colors["o"])

    def test_fail_dims_and_walk_left_mirrors_right(self):
        dimmed = frame_image(5, 2)
        self.assertNotIn(BODY.rgb(), {dimmed.pixel(x, y) for x in range(SIZE) for y in range(SIZE)})
        for frame in range(len(DURATIONS[1])):
            self.assertEqual(frame_image(2, frame), frame_image(1, frame).mirrored(True, False))
        self.assertEqual(atlas().width(), SIZE * max(map(len, DURATIONS)))
        self.assertEqual(atlas().pixel(SIZE * 4 + 48, SIZE * 4 + 80), frame_image(4, 4).pixel(48, 80))

    def test_new_rows_move_in_small_steps(self):
        base = F(BASE_POSE)
        for row in range(9, len(POSES)):
            frames = POSES[row]
            for i, (a, b) in enumerate(zip(frames, frames[1:])):
                self.assertLessEqual(step(a, b), 24, (ANIMATION_NAMES[row], i))
        for row in (0, GLANCE_L, GLANCE_R, DOZE_IN, *FIDGETS):  # idle moves start from and return to the base pose
            self.assertEqual(POSES[row][0], base, ANIMATION_NAMES[row])
            if row != DOZE_IN:  # dozing carries on into the snore loop; waking up stands back up
                self.assertLessEqual(step(POSES[row][-1], base), 8, ANIMATION_NAMES[row])
        self.assertEqual(POSES[WAKE][-1], base)
        self.assertEqual(POSES[DOZE_IN][-1]["rows"], POSES[DOZE][-1]["rows"])

    def test_idle_scheduler_varies_fidgets_with_cooldowns(self):
        with patch.object(pixel_pet, "DOZE_AFTER", 10 ** 9):
            life = Life(seed=11)
            fidgets, holds, glances = [], [], set()
            for life in run(life, 3_600_000):
                if life.frame == 0 and life.row in FIDGETS:
                    fidgets.append((life.row, life.clock))
                if life.frame == 0 and life.row in (0, GLANCE_L, GLANCE_R):
                    holds.append(life.hold)
                    glances.add(life.row)
        rows = [row for row, _ in fidgets]
        self.assertGreaterEqual(len(set(rows)), 6)
        self.assertTrue(all(a != b for a, b in zip(rows, rows[1:])))
        for (_, a), (_, b) in zip(fidgets, fidgets[1:]):
            self.assertTrue(15_000 <= b - a <= 80_000, b - a)  # 15-60 s apart, plus the hold and the fidget itself
        for row in set(rows):
            times = [t for r, t in fidgets if r == row]
            self.assertTrue(all(b - a >= FIDGETS[row][1] for a, b in zip(times, times[1:])), ANIMATION_NAMES[row])
        self.assertTrue(all(150 <= hold <= 7000 for hold in holds))
        self.assertGreater(sum(hold >= 2500 for hold in holds), len(holds) * .7)  # mostly long, still holds
        self.assertEqual(glances, {0, GLANCE_L, GLANCE_R})

    def test_seeded_life_replays_and_jitters_within_bounds(self):
        trace = [[(life.row, life.frame, life.wait) for life in run(Life(seed=4), 200_000)] for _ in range(2)]
        self.assertEqual(trace[0], trace[1])
        life = Life(seed=1)
        life.play(HOP)
        for frame, nominal in enumerate(DURATIONS[HOP]):
            life.frame = frame
            self.assertTrue(nominal * .88 - 1 <= life.duration() <= nominal * 1.12 + 1)
            self.assertEqual(life.duration(jitter=False), nominal)

    def test_long_quiet_dozes_and_hover_wakes(self):
        life = Life(seed=2)
        rows = [life.row for life in run(life, DOZE_AFTER + 30_000)]
        self.assertIn(DOZE_IN, rows)
        self.assertEqual(rows[-1], DOZE)
        life.restart(4, "hover")
        self.assertEqual((life.row, life.queue), (WAKE, [HOP]))
        life.restart(0, "status", reduced=True)
        self.assertEqual((life.row, life.queue), (0, []))


if __name__ == "__main__":
    unittest.main()
