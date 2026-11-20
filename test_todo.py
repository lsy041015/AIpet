"""Local to-do list behavior without network or real user settings."""
import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from todo import TodoPanel


class TodoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_add_complete_delete_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat)
            panel = TodoPanel(settings)
            panel.show()
            panel.input.setText("  장보기  ")
            QTest.keyClick(panel.input, Qt.Key_Return)
            self.assertEqual([(item["text"], item["done"]) for item in panel.items], [("장보기", False)])
            self.assertEqual(panel.input.text(), "")
            panel.input.setText("회의 자료 준비")
            QTest.mouseClick(panel.add_button, Qt.LeftButton)
            self.assertEqual(panel.count.text(), "남은 할 일 2개")
            self.assertEqual([row[0]["text"] for row in panel.rows], ["장보기", "회의 자료 준비"])
            panel.rows[0][1].setChecked(True)
            self.assertEqual(panel.count.text(), "남은 할 일 1개")
            self.assertEqual([row[0]["text"] for row in panel.rows], ["장보기", "회의 자료 준비"])
            self.assertEqual(panel.rows[-1][0]["text"], "회의 자료 준비")
            active_rows = {row[1].parentWidget() for row in panel.rows}
            for row in panel.findChildren(QFrame, "todoItem"):
                if row not in active_rows:
                    self.assertTrue(row.isHidden())
            panel.close()
            panel.deleteLater()

            restored = TodoPanel(settings)
            self.assertEqual([(item["text"], item["done"]) for item in restored.items],
                             [("회의 자료 준비", False), ("장보기", True)])
            self.assertEqual([row[0]["text"] for row in restored.rows], ["장보기", "회의 자료 준비"])
            completed = next(row for row in restored.rows if row[0]["done"])
            QTest.mouseClick(completed[2], Qt.LeftButton)
            self.assertEqual([item["text"] for item in restored.items], ["회의 자료 준비"])
            self.assertEqual(len(json.loads(settings.value("todo/items"))), 1)
            restored.close()
            restored.deleteLater()

    def test_invalid_saved_list_is_kept_for_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat)
            settings.setValue("todo/items", '{"bad": "data"}')
            panel = TodoPanel(settings)
            self.assertFalse(panel.add_button.isEnabled())
            self.assertIn("읽지 못했습니다", panel.message.text())
            self.assertEqual(settings.value("todo/items"), '{"bad": "data"}')
            panel.close()
            panel.deleteLater()

    def test_empty_long_and_markup_input(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.IniFormat)
            panel = TodoPanel(settings)
            panel.input.setText("   ")
            panel.add_item()
            self.assertEqual(panel.items, [])
            panel.input.setText("<b>문서 확인</b>")
            panel.add_item()
            self.assertEqual(panel.rows[0][3].textFormat(), Qt.PlainText)
            self.assertEqual(panel.items[0]["text"], "<b>문서 확인</b>")
            panel.input.setText("가" * 201)
            panel.add_item()
            self.assertEqual(len(panel.items), 1)
            panel.close()
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
