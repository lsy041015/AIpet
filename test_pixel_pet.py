import unittest

from PySide6.QtWidgets import QApplication

from pixel_pet import ANIMATION_NAMES, BODY, DURATIONS, POSES, SIZE, atlas, frame_image


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
        self.assertEqual(len(DURATIONS[1]), len(DURATIONS[2]))

    def test_every_frame_renders_orange_mascot(self):
        body = BODY.rgb()
        for row, durations in enumerate(DURATIONS):
            images = []
            for frame in range(len(durations)):
                image = frame_image(row, frame)
                self.assertEqual((image.width(), image.height()), (SIZE, SIZE))
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0, (row, frame))
                pixels = {image.pixel(x, y) & 0xFFFFFF for x in range(SIZE) for y in range(SIZE)
                          if image.pixelColor(x, y).alpha() == 255}
                self.assertGreater(len(pixels), 1, (row, frame))
                if row != 5 or frame == 0:  # fail frames are desaturated on purpose
                    self.assertIn(body & 0xFFFFFF, pixels, (row, frame))
                images.append(image)
            self.assertNotEqual(images[0], images[1], row)
        self.assertIs(frame_image(3, 1), frame_image(3, 1))

    def test_fail_dims_and_walk_left_mirrors_right(self):
        dimmed = frame_image(5, 2)
        self.assertNotIn(BODY.rgb(), {dimmed.pixel(x, y) for x in range(SIZE) for y in range(SIZE)})
        self.assertEqual(frame_image(2, 0), frame_image(1, 0).mirrored(True, False))
        self.assertEqual(atlas().width(), SIZE * 8)
        self.assertEqual(atlas().pixel(SIZE * 4 + 48, SIZE * 4 + 80), frame_image(4, 4).pixel(48, 80))


if __name__ == "__main__":
    unittest.main()
