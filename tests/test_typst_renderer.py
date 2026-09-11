from datetime import datetime
from importlib import resources
import shutil
import subprocess

import cv2
import pytest
from pypdf import PdfReader
import zxingcpp

from omrexams.generate import Generate
from omrexams.utils.markdown import Document
from omrexams.utils.qrdecoder import decode_bottom_right
from omrexams.utils.typst import TypstQuestionRenderer


def test_typst_renderer_preserves_answers_and_permutation():
    source = """---
## Quanto fa $\\frac{2}{2} + 1$?
- [ ] 3
- [x] 4
---
"""
    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova",
        student_no="123",
        student_name="Mario Rossi",
        language="italian",
        warning="Avvertenza personalizzata",
        qr_eclevel="H",
        shuffle=False,
    )

    document = renderer.render(Document(source))

    assert renderer.questions[0]["answers"] == [False, True]
    assert renderer.questions[0]["permutation"] == [0, 1]
    assert document.source.count("#question(") == 1
    assert "show-roi: false" in document.source
    assert "warning: [Avvertenza personalizzata]" in document.source
    assert 'qr-error-correction: "H"' in document.source
    assert 'language: "it"' in document.source
    assert '#mi("\\\\frac{2}{2} + 1")' in document.source
    assert "#block[#strong[A)] 3]" in document.source
    assert "#block[#strong[B)] 4]" in document.source


def test_typst_renderer_supports_horizontal_answers_and_inline_title():
    source = """---
## Titolo sulla riga del numero
* [ ] Prima
* [x] Seconda
* [ ] Terza
---
"""
    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova",
        student_no="123",
        shuffle=False,
    )

    document = renderer.render(Document(source))

    assert '#question(1, ("A", "B", "C",), [Titolo sulla riga del numero])[' in document.source
    assert "#grid(columns: (1fr,) * 3" in document.source
    assert "#block[#strong[A)]" not in document.source


def test_typst_renderer_wraps_soft_breaks_and_draws_answer_lines():
    source = """---
### Domanda aperta
Prima riga nel sorgente
che continua nello stesso paragrafo.

{lines:2.5cm}
---
"""
    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova",
        student_no="123",
        language="italian",
        shuffle=False,
    )

    document = renderer.render(Document(source))

    assert "Prima riga nel sorgente che continua nello stesso paragrafo." in document.source
    assert "#answer-lines(2.5cm)" in document.source


def test_typst_engine_rejects_latex_extensions(tmp_path):
    config = {
        "exam": {"engine": "typst"},
        "packages": {"graphicx": []},
    }

    with pytest.raises(ValueError, match="LaTeX-specific"):
        Generate(config, str(tmp_path), str(tmp_path / "exam"), test=True)


def test_typst_renderer_rejects_invalid_qr_error_correction():
    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova",
        student_no="123",
        qr_eclevel="invalid",
    )

    with pytest.raises(ValueError, match="qr_eclevel"):
        renderer.render(Document(""))


def test_typst_page_qr_payload_matches_decoder_contract():
    metadata = decode_bottom_right(
        "(405,105)-(540,649)/(539,785)/14,1,1-10"
    )

    assert metadata["p0"].tolist() == [405, 105]
    assert metadata["p1"].tolist() == [540, 649]
    assert metadata["page"] == 1
    assert metadata["range"] == (1, 10)


def test_typst_test_document_enables_roi_and_disables_shuffle():
    source = """---
## Domanda di test
- [ ] Prima
- [x] Seconda
---
"""
    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova",
        student_no="0",
        student_name="",
        test=True,
        shuffle=True,
    )

    document = renderer.render(Document(source))

    assert renderer.questions[0]["permutation"] == [0, 1]
    assert "show-roi: true" in document.source


def test_typst_multipage_qr_payloads_match_page_questions(tmp_path):
    assert shutil.which("typst"), "typst executable is required for integration tests"
    pdftoppm = shutil.which("pdftoppm")
    assert pdftoppm, "pdftoppm executable is required for integration tests"

    blocks = []
    for number in range(1, 7):
        new_page = "[NEWPAGE]\n" if number in (3, 5) else ""
        code = ""
        if number == 2:
            code = (
                "Valuta `square(4)` nel seguente frammento:\n\n"
                "```python\n"
                "def square(value):\n"
                "    return value * value\n"
                "```\n"
            )
        elif number == 4:
            code = (
                "Considera questo ciclo:\n\n"
                "```c\n"
                "for (int index = 0; index < 3; ++index) {\n"
                "    total += index;\n"
                "}\n"
                "```\n"
            )
        blocks.append(
            f"---\n{new_page}## Domanda {number}\n"
            f"{code}"
            "- [x] Corretta\n"
            "- [ ] Errata\n"
        )
    blocks.append(
        "---\n[NEWPAGE]\n### Domanda aperta\n"
        "Questa risposta può continuare sulla pagina seguente.\n"
    )

    renderer = TypstQuestionRenderer(
        date=datetime(2026, 9, 11),
        exam="Prova multipagina",
        student_no="42",
        student_name="Mario Rossi",
        qr_eclevel="H",
        shuffle=False,
    )
    document = renderer.render(Document("".join(blocks)))
    assert document.source.count("#pagebreak()") == 3
    assert '#raw(block: true, lang: "python"' in document.source
    assert '#raw(block: true, lang: "c"' in document.source

    template = resources.files("omrexams").joinpath("typst", "omrexam.typ")
    (tmp_path / "omrexam.typ").write_bytes(template.read_bytes())
    output = tmp_path / "multipage"
    document.generate_pdf(str(output))

    pdf = PdfReader(output.with_suffix(".pdf"))
    assert len(pdf.pages) == 4
    expected_questions = ((1, 2), (3, 4), (5, 6), (0, 0))

    subprocess.run(
        [pdftoppm, "-png", "-r", "144", str(output.with_suffix(".pdf")), str(output)],
        check=True,
        capture_output=True,
    )

    for page_number, expected_range in enumerate(expected_questions, start=1):
        image = cv2.imread(str(tmp_path / f"multipage-{page_number}.png"))
        assert image is not None
        codes = [
            code
            for code in zxingcpp.read_barcodes(image)
            if code.format == zxingcpp.BarcodeFormat.QRCode
        ]
        assert len(codes) == 2

        top_left = next(code for code in codes if code.text.startswith("42,"))
        bottom_right = next(code for code in codes if code is not top_left)
        metadata = decode_bottom_right(bottom_right.text)

        assert metadata is not None
        assert metadata["page"] == page_number
        assert metadata["range"] == expected_range
        assert metadata["qrwidth"] == 539
        assert metadata["qrheight"] == 785
        assert metadata["bsize"] == 14

        page_text = pdf.pages[page_number - 1].extract_text()
        if expected_range == (0, 0):
            assert "Domanda aperta" in page_text
        else:
            for question_number in expected_range:
                assert f"Domanda {question_number}" in page_text
        if page_number == 1:
            assert "def square" in page_text
            assert "square(4)" in page_text
        if page_number == 2:
            assert "for (int index" in page_text