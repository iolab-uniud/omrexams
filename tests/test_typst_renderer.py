from datetime import datetime

import pytest

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
    assert '#mi("\\\\frac{2}{2} + 1")' in document.source
    assert "#block[#strong[A)] 3]" in document.source
    assert "#block[#strong[B)] 4]" in document.source


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