import tempfile
import unittest
from pathlib import Path

from omrexams.generate import Generate


class GenerateQuestionRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.question_file = Path(self.temp_dir.name) / "questions.md"
        self.question_file.write_text(
            "# Exam\n"
            "---\n"
            "## Closed one\nAnswer one\n"
            "---\n"
            "### Open two\nAnswer space\n"
            "---\n"
            "## Closed three\nAnswer three\n",
            encoding="utf-8",
        )
        self.generator = Generate.__new__(Generate)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_range_is_one_based_inclusive_and_applied_before_question_type_filter(self):
        rule = {"range": [2, 3]}

        closed = self.generator.load_questions(self.question_file, rule)
        opened = self.generator.load_open_questions(self.question_file, rule)

        self.assertEqual(len(closed), 1)
        self.assertIn("Closed three", closed[0])
        self.assertEqual(len(opened), 1)
        self.assertIn("Open two", opened[0])

    def test_missing_end_selects_through_last_question(self):
        opened = self.generator.load_open_questions(
            self.question_file, {"range": [2, None]}
        )

        self.assertEqual(len(opened), 1)
        self.assertIn("Open two", opened[0])

    def test_selected_questions_keep_their_original_type_index(self):
        closed_entries = self.generator._load_question_entries(
            self.question_file, {"range": [2, 3]}
        )

        self.assertEqual(closed_entries[0][1], 1)

    def test_invalid_ranges_are_reported(self):
        for question_range in ([0, 1], [3, 2], ["first", 2]):
            with self.subTest(question_range=question_range):
                with self.assertRaises(ValueError):
                    self.generator.load_questions(
                        self.question_file, {"range": question_range}
                    )

    def test_multiple_rules_for_same_file_are_preserved(self):
        self.generator.config = {
            "questions": [
                {"from": "questions.md", "draw": 1, "range": [1, 1]},
                {"from": "questions.md", "draw": 1, "range": [3, 3]},
            ]
        }
        self.generator.questions_path = self.temp_dir.name

        rules = self.generator.load_rules()

        self.assertEqual(len(rules), 2)
        self.assertEqual([rule["range"] for _, rule in rules], [[1, 1], [3, 3]])


if __name__ == "__main__":
    unittest.main()
