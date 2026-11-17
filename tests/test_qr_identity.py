import unittest
from datetime import datetime

from omrexams.utils.crypt import binary_encrypt
from omrexams.utils.markdown import Document, QuestionRenderer
from omrexams.utils.qrdecoder import decode_top_left


class QRIdentityTests(unittest.TestCase):
    def test_decodes_hyphenated_identity_and_legacy_latex_escape(self):
        solutions = ["A", "BC"]
        encrypted = binary_encrypt(solutions, "0-12345")
        for identity in ("0-12345", "0{-}12345"):
            with self.subTest(identity=identity):
                decoded = decode_top_left(f"{identity},{encrypted}")
                self.assertEqual(decoded["student_id"], "0-12345")
                self.assertEqual(decoded["correct"], solutions)

    def test_renderer_preserves_hyphenated_identity_without_unescaping_name(self):
        with QuestionRenderer(
            student_no="0-12345",
            student_name="Student & Name",
            date=datetime(2026, 1, 1),
            language="italian",
            encrypt=False,
        ) as renderer:
            document = renderer.render(Document("---\n## Question\n- [x] Yes\n- [ ] No\n---\n"))
        self.assertIn(r"\student{0-12345}{Student \& Name}", document.dumps())


if __name__ == "__main__":
    unittest.main()
