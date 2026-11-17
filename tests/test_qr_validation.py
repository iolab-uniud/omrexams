import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import zxingcpp
from click.testing import CliRunner
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from omrexams.cli import cli
from omrexams.qr_validation import validate_exam_qrs
from omrexams.utils.crypt import binary_encrypt


class QRValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "exam.pdf"
        self.json = self.root / "exam.json"
        self.answers = ["A", "BC"]
        self.write_json(("1", "0-12345"))

    def write_json(self, identities):
        exams = {
            str(index): {
                "student_id": identity,
                "answers": self.answers,
                "questions": [["test.md", 0, "A", [0, 1]], ["test.md", 1, "BC", [0, 1, 2]]],
            }
            for index, identity in enumerate(identities, 1)
        }
        self.json.write_text(json.dumps({"metadata": {"1": {"seed": 42}}, "exams": exams}))

    def top(self, identity, answers=None):
        return f"{identity},{binary_encrypt(self.answers if answers is None else answers, identity)}"

    def bottom(self, number=1, start=1, end=2):
        return f"(1,2)-(3,4)/(5,6)/14,{number},{start}-{end}"

    def write_pdf(self, codes, width=595.28, height=841.89):
        writer = PdfWriter()
        for pair in codes:
            page = writer.add_blank_page(width=width, height=height)
            if pair is None:
                continue
            commands = ["0 g"]
            for text, x, y in ((pair[0], 30, 700), (pair[1], 450, 130)):
                barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
                pixels = np.asarray(zxingcpp.write_barcode_to_image(barcode))
                for row, column in np.argwhere(pixels == 0):
                    commands.append(f"{x + int(column) * 2} {y - int(row) * 2} 2 2 re f")
            stream = DecodedStreamObject()
            stream.set_data("\n".join(commands).encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        with self.pdf.open("wb") as source:
            writer.write(source)

    def valid_pdf(self):
        legacy = self.top("0-12345").replace("0-12345,", "0{-}12345,")
        self.write_pdf([(self.top("1"), self.bottom()), None, (legacy, self.bottom()), None])

    def test_real_qrs_match_json_including_legacy_hyphen_and_padding(self):
        self.valid_pdf()
        original_pdf, original_json = self.pdf.read_bytes(), self.json.read_bytes()
        progress = []
        report = validate_exam_qrs(self.pdf, lambda *args: progress.append(args))
        self.assertTrue(report["valid"], report["issues"])
        self.assertEqual((report["qr_pages"], report["padding_pages"], report["exams_checked"]), (2, 2, 2))
        self.assertEqual(report["seed"], 42)
        self.assertEqual(progress[-1][:2], (4, 4))
        self.assertEqual(self.pdf.read_bytes(), original_pdf)
        self.assertEqual(self.json.read_bytes(), original_json)

    def test_reports_unreadable_qr_and_wrong_answers_without_stopping_early(self):
        self.valid_pdf()
        with patch("omrexams.qr_validation.decode_page_qrs", side_effect=[
            ValueError("QR non leggibile"),
            (self.top("0-12345", ["B", "BC"]), self.bottom()),
        ]) as decode:
            report = validate_exam_qrs(self.pdf)
        self.assertFalse(report["valid"])
        self.assertEqual(decode.call_count, 2)
        self.assertTrue(any(issue["page"] == 1 and "QR non leggibile" in issue["message"] for issue in report["issues"]))
        self.assertTrue(any(issue["page"] == 3 and "discordanti" in issue["message"] for issue in report["issues"]))
        self.assertTrue(any("non presente nel PDF" in issue["message"] for issue in report["issues"]))

    def test_reports_duplicate_exams_invalid_page_sequence_and_missing_question_ranges(self):
        self.write_json(("1",))
        self.write_pdf([(self.top("1"), self.bottom())] * 3)
        with patch("omrexams.qr_validation.decode_page_qrs", side_effect=[
            (self.top("1"), self.bottom(start=1, end=1)),
            (self.top("1"), self.bottom(number=3, start=1, end=1)),
            (self.top("1"), self.bottom(start=1, end=1)),
        ]):
            report = validate_exam_qrs(self.pdf)
        messages = [issue["message"] for issue in report["issues"]]
        self.assertFalse(report["valid"])
        for expected in ("Sequenza", "più di una volta", "sovrapposti", "non coperte"):
            self.assertTrue(any(expected in message for message in messages), expected)

    def test_split_pdf_checks_only_its_subset_against_shared_json(self):
        self.write_pdf([(self.top("1"), self.bottom()), None])
        chunk = self.root / "exam-01.pdf"
        self.pdf.rename(chunk)
        report = validate_exam_qrs(chunk)
        self.assertTrue(report["valid"], report["issues"])
        self.assertTrue(report["is_chunk"])
        self.assertEqual((report["exams_checked"], report["json_exams"]), (1, 2))

    def test_rejects_a3_and_missing_or_inconsistent_json(self):
        self.write_pdf([(self.top("1"), self.bottom())], width=1190, height=842)
        report = validate_exam_qrs(self.pdf)
        self.assertFalse(report["valid"])
        self.assertIn("A4", report["issues"][0]["message"])
        self.json.unlink()
        with self.assertRaisesRegex(ValueError, "JSON.*non trovato"):
            validate_exam_qrs(self.pdf)
        self.write_json(("1",))
        data = json.loads(self.json.read_text())
        data["exams"]["1"]["questions"][0][2] = "B"
        self.json.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "questions e answers discordanti"):
            validate_exam_qrs(self.pdf)

    def test_cli_json_output_and_exit_code_reflect_validation_result(self):
        self.valid_pdf()
        runner = CliRunner()
        result = runner.invoke(cli, ["check-qrs", str(self.pdf), "--json-output"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.output)["valid"])
        self.write_pdf([(self.top("1", ["B", "BC"]), self.bottom()), None])
        result = runner.invoke(cli, ["check-qrs", str(self.pdf), "--json-file", str(self.json), "--json-output"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertFalse(json.loads(result.output)["valid"])


if __name__ == "__main__":
    unittest.main()
