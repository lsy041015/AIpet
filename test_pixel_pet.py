import unittest

from PySide6.QtWidgets import QApplication

from pixel_pet import ANIMATION_NAMES, BODY, COLS, DURATIONS, EYE, POSES, PX, SIZE, atlas, cells, frame_image

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
        self.assertEqual(len(DURATIONS), 9)
        self.assertEqual(len(ANIMATION_NAMES), 9)
        for row, poses in enumerate(POSES):
            if poses is not None:
                self.assertEqual(len(poses), len(DURATIONS[row]), row)
                for frame in poses:  # every cell stays inside the 16x16 grid
                    for x, y, _ in cells(frame):
                        self.assertTrue(0 <= x < COLS and 0 <= y < COLS, (row, x, y))
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
                if row != 5 or frame == 0:  # fail frames are desaturated on purpose
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
        blink = frame_image(0, 1)
        self.assertEqual(blink.pixel(left + 3 * PX + 3, top + PX + 3) & 0xFFFFFF, colors["o"])

    def test_fail_dims_and_walk_left_mirrors_right(self):
        dimmed = frame_image(5, 2)
        self.assertNotIn(BODY.rgb(), {dimmed.pixel(x, y) for x in range(SIZE) for y in range(SIZE)})
        for frame in range(len(DURATIONS[1])):
            self.assertEqual(frame_image(2, frame), frame_image(1, frame).mirrored(True, False))
        self.assertEqual(atlas().width(), SIZE * max(map(len, DURATIONS)))
        self.assertEqual(atlas().pixel(SIZE * 4 + 48, SIZE * 4 + 80), frame_image(4, 4).pixel(48, 80))


if __name__ == "__main__":
    unittest.main()
