import os
import queue
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mistletoe import Document as MarkdownDocument
from pylatex import Document
from pypdf import PdfWriter

from omrexams.generate import Generate, _compile_pdf_with_warnings, _overfull_warnings
from omrexams.utils.markdown import QuestionRenderer


class LayoutDyslexiaTests(unittest.TestCase):
    def test_inline_code_is_breakable_in_questions_and_answers(self):
        markdown = (
            "---\n## Quale istruzione è equivalente a "
            "`for (i = 0; i < 10; i++) { /* istruzioni */ }`?\n"
            "- [x] `while (a | b) { x++; }`\n"
            "- [ ] Nessuna\n---\n"
        )
        for test_mode in (False, True):
            with self.subTest(test_mode=test_mode):
                with QuestionRenderer(
                    language="italian", date=datetime(2026, 1, 1), exam="Test",
                    student_no="1", student_name="Test", encrypt=False, shuffle=False,
                    test=test_mode,
                ) as renderer:
                    latex = renderer.render(MarkdownDocument(markdown)).dumps()
                self.assertIn(r"\usepackage{fvextra}", latex)
                self.assertIn(
                    r"\Verb[breaklines,breakanywhere]|for (i = 0; i < 10; i++) { /* istruzioni */ }|",
                    latex,
                )
                self.assertIn(r"\Verb[breaklines,breakanywhere]!while (a | b) { x++; }!", latex)

    def test_long_text_and_tables_fit_without_changing_code(self):
        word = "abcdefghijklmnopqrstuvwxyz" * 4
        markdown = (
            f"---\n## {word}?\n"
            f"- [x] {word}\n"
            f"- [ ] `{word}`\n\n"
            "| First | Second |\n| --- | --- |\n| Wide cell | Other cell |\n---\n"
        )
        for test_mode in (False, True):
            with self.subTest(test_mode=test_mode):
                with QuestionRenderer(
                    language="italian", date=datetime(2026, 1, 1), exam="Test",
                    student_no="1", student_name="Test", encrypt=False, shuffle=False,
                    test=test_mode,
                ) as renderer:
                    latex = renderer.render(MarkdownDocument(markdown)).dumps()
                broken_word = r"\allowbreak{}".join(
                    word[i:i + 8] for i in range(0, len(word), 8)
                )
                self.assertEqual(latex.count(broken_word), 2)
                self.assertIn(r"\Verb[breaklines,breakanywhere]|" + word + "|", latex)
                self.assertIn(r"\begin{adjustbox}{max width=\linewidth}", latex)
                self.assertIn(r"\end{adjustbox}", latex)

    def test_optical_markers_start_at_the_question_heading(self):
        markdown = (
            "---\n## First long question " + "word " * 40 + "\n"
            "Additional text before answers.\n"
            "- [x] Correct\n- [ ] Incorrect\n"
            "---\n## Second question\n"
            "* [ ] One\n* [x] Two\n* [ ] Three\n"
            "---\n### Open question\n{lines:1cm}\n---\n"
        )
        for test_mode in (False, True):
            with self.subTest(test_mode=test_mode):
                with QuestionRenderer(
                    language="italian", date=datetime(2026, 1, 1), exam="Test",
                    student_no="1", student_name="Test", encrypt=False, shuffle=False,
                    test=test_mode,
                ) as renderer:
                    latex = renderer.render(MarkdownDocument(markdown)).dumps()
                    questions = renderer.questions
                self.assertIn("\\question\n\\omrchoices{2}\nFirst long question", latex)
                self.assertIn("\\question\n\\omrchoices{3}\nSecond question", latex)
                self.assertEqual(latex.count(r"\omrchoices"), 2)
                self.assertEqual(questions[0]["answers"], [True, False])
                self.assertEqual(questions[1]["answers"], [False, True, False])

    def test_overfull_warnings_identify_questions_and_ignore_small_excess(self):
        tex = (
            "\\begin{minipage}{\\linewidth}\n\\question\nPrima domanda\n"
            "\\end{minipage}\n\\begin{minipage}{\\linewidth}\n"
            "\\question\n\\omrchoices{2}\nSeconda domanda\n\\end{minipage}\n"
        )
        log = (
            "Overfull \\hbox (14.2pt too wide) in paragraph at lines 2--3\n"
            "Overfull \\hbox (14.3pt too wide) in paragraph at lines 6--8\n"
            "Overfull \\hbox (40.0pt too wide) detected at line 20\n"
        )
        warnings = _overfull_warnings(log, tex, "Esame 3")
        self.assertEqual(len(warnings), 2)
        self.assertIn("Esame 3, domanda 2 (Seconda domanda)", warnings[0])
        self.assertIn("14.3 pt", warnings[0])
        self.assertIn("Esame 3:", warnings[1])
        self.assertNotIn("domanda", warnings[1])

    def test_compile_inspects_log_and_removes_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "exam")
            document = Document()

            def compile_document(document, filepath, **kwargs):
                self.assertFalse(kwargs["clean"])
                self.assertFalse(kwargs["clean_tex"])
                Path(f"{filepath}.tex").write_text(
                    "\\begin{minipage}{\\linewidth}\n\\question\nDomanda\n\\end{minipage}\n",
                    encoding="utf-8",
                )
                Path(f"{filepath}.log").write_text(
                    "Overfull \\hbox (30.0pt too wide) in paragraph at lines 2--3\n",
                    encoding="utf-8",
                )
                Path(f"{filepath}.pdf").write_bytes(b"test")

            with patch.object(Document, "generate_pdf", autospec=True, side_effect=compile_document):
                warnings = _compile_pdf_with_warnings(document, filename, ['-xelatex'], "Anteprima")
            self.assertIn("Anteprima, domanda 1 (Domanda)", warnings[0])
            self.assertEqual(sorted(path.suffix for path in Path(directory).iterdir()), [".pdf"])

    def test_exam_warnings_include_student_identity(self):
        students = [("123456", "Giulia Rossi"), ("2", "Anonimo")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            generator = Generate(
                config={"exam": {"name": "Test"}}, questions=str(root),
                output_prefix=str(root / "exam"), students=students, check_overfull=True,
            )
            generator.profile_generators = {}
            generator.generate_tasks_queue = queue.Queue()
            for index, student in enumerate(students):
                generator.generate_tasks_queue.put((index, student, False))
            generator.generate_tasks_queue.put((None, None, None))
            generator.error = SimpleNamespace(value=False)
            generator.results = SimpleNamespace(value=0)
            generator.results_mutex = threading.RLock()
            generator.generate_task_done = threading.Condition(generator.results_mutex)
            generator.failures = queue.Queue()
            generator.layout_warnings = queue.Queue()

            def compile_document(document, filename, compiler_args, label):
                pdf = PdfWriter()
                pdf.add_blank_page(width=100, height=100)
                pdf.write(f"{filename}.pdf")
                return _overfull_warnings(
                    "Overfull \\hbox (30.0pt too wide) in paragraph at lines 2--3\n",
                    "\\begin{minipage}{\\linewidth}\n\\question\nDomanda\n\\end{minipage}\n",
                    label,
                )

            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.object(generator, "create_exam", return_value=(Document(), [], [])), \
                     patch.object(generator, "append_exam"), \
                     patch("omrexams.generate._compile_pdf_with_warnings", side_effect=compile_document):
                    generator.worker_main_generate()
            finally:
                os.chdir(original_cwd)
            self.assertFalse(generator.error.value)
            warnings = [generator.layout_warnings.get_nowait() for _ in students]
            self.assertIn("matricola 123456", warnings[0])
            self.assertIn("Giulia Rossi", warnings[0])
            self.assertIn("domanda 1", warnings[0])
            self.assertIn("matricola 2", warnings[1])
            self.assertIn("Anonimo", warnings[1])

    def test_normal_exam_skips_overfull_check_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tmp").mkdir()
            generator = Generate(
                config={"exam": {"name": "Test"}}, questions=str(root),
                output_prefix=str(root / "exam"), students=[("1", "Test")],
            )
            generator.profile_generators = {}
            generator.generate_tasks_queue = queue.Queue()
            generator.generate_tasks_queue.put((0, ("1", "Test"), False))
            generator.generate_tasks_queue.put((None, None, None))
            generator.error = SimpleNamespace(value=False)
            generator.results = SimpleNamespace(value=0)
            generator.results_mutex = threading.RLock()
            generator.generate_task_done = threading.Condition(generator.results_mutex)
            generator.failures = queue.Queue()
            generator.layout_warnings = queue.Queue()

            def compile_document(document, filepath, **kwargs):
                self.assertNotIn("clean", kwargs)
                pdf = PdfWriter()
                pdf.add_blank_page(width=100, height=100)
                pdf.write(f"{filepath}.pdf")

            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                with patch.object(generator, "create_exam", return_value=(Document(), [], [])), \
                     patch.object(generator, "append_exam"), \
                     patch.object(Document, "generate_pdf", autospec=True, side_effect=compile_document) as normal, \
                     patch("omrexams.generate._compile_pdf_with_warnings") as checked:
                    generator.worker_main_generate()
                normal.assert_called_once()
                checked.assert_not_called()
            finally:
                os.chdir(original_cwd)
            self.assertFalse(generator.error.value)
            self.assertTrue(generator.layout_warnings.empty())

    def test_overfull_check_is_optional_for_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = root / "questions"
            questions.mkdir()
            (questions / "test.md").write_text(
                "---\n## Question\n- [x] First\n- [ ] Second\n---\n", encoding="utf-8"
            )
            config = {
                "exam": {"name": "Test", "language": "italian"},
                "questions": [{"from": "test.md", "draw": 1}],
            }
            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                for enabled in (False, True):
                    with self.subTest(enabled=enabled):
                        warnings = []
                        generator = Generate(
                            config=config, questions=str(questions),
                            output_prefix=str(root / "preview"), test=True,
                            check_overfull=enabled, warning_callback=warnings.append,
                        )

                        def compile_without_check(document, filepath, **kwargs):
                            self.assertNotIn("clean", kwargs)
                            Path(f"{filepath}.pdf").write_bytes(b"test")

                        def compile_with_check(document, filename, compiler_args, label):
                            Path(f"{filename}.pdf").write_bytes(b"test")
                            return ["Anteprima: overfull"]

                        with patch.object(Document, "generate_pdf", autospec=True, side_effect=compile_without_check) as normal, \
                             patch("omrexams.generate._compile_pdf_with_warnings", side_effect=compile_with_check) as checked:
                            generator.process()
                        self.assertEqual(normal.call_count, 0 if enabled else 1)
                        self.assertEqual(checked.call_count, 1 if enabled else 0)
                        self.assertEqual(warnings, ["Anteprima: overfull"] if enabled else [])
            finally:
                os.chdir(original_cwd)

    def test_layout_uses_selected_dyslexia_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = root / "questions"
            questions.mkdir()
            (questions / "test.md").write_text(
                "---\n## Question\nText\n* [x] First\n* [ ] Second\n---\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            original_cwd = os.getcwd()
            try:
                os.chdir(root)
                for dyslexia, easyreading in (
                    (False, False),
                    (False, True),
                    (True, False),
                    (True, True),
                ):
                    with self.subTest(dyslexia=dyslexia, easyreading=easyreading):
                        captured = []

                        def compile_document(document, filepath, **kwargs):
                            captured.append(document.dumps())
                            Path(f"{filepath}.tex").write_text(document.dumps(), encoding="utf-8")
                            Path(f"{filepath}.log").write_text("", encoding="utf-8")
                            Path(f"{filepath}.pdf").write_bytes(b"test")

                        generator = Generate(
                            config={
                                "exam": {"name": "Test", "language": "italian"},
                                "questions": [{"from": "test.md", "draw": 1}],
                                "dyslexia": dyslexia,
                                "easyreading": easyreading,
                            },
                            questions=str(questions),
                            output_prefix=str(output / "layout"),
                            test=True,
                        )
                        with patch.object(Document, "generate_pdf", autospec=True, side_effect=compile_document):
                            generator.process()
                        self.assertEqual(len(captured), 1)
                        self.assertEqual("dyslexia" in captured[0].splitlines()[0], dyslexia)
                        self.assertEqual("easyreading" in captured[0].splitlines()[0], easyreading)
                        self.assertEqual(r"\begin{choices}" in captured[0], dyslexia)
                        self.assertEqual(r"\begin{oneparchoices}" in captured[0], not dyslexia)
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
