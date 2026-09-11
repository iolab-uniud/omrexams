import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import cv2
from pypdf import PdfReader
import pytest
from tinydb import Query, TinyDB

from omrexams.utils import qrdecoder

EXPECTED_ANSWERS = [["A"], ["B"], ["B"], ["C"], ["C"], ["D"], ["D"], ["A"]]
EXPECTED_RANGES = [(1, 2), (3, 4), (5, 6), (7, 8), (0, 0), (0, 0)]
EXAMPLE_SCRIPT = Path(__file__).parents[1] / "examples" / "generate_examples.py"
EXAMPLE_SPEC = importlib.util.spec_from_file_location("generate_examples", EXAMPLE_SCRIPT)
assert EXAMPLE_SPEC is not None and EXAMPLE_SPEC.loader is not None
EXAMPLE_MODULE = importlib.util.module_from_spec(EXAMPLE_SPEC)
EXAMPLE_SPEC.loader.exec_module(EXAMPLE_MODULE)
generate_engine = EXAMPLE_MODULE.generate_engine


@pytest.mark.parametrize("engine", ["latex", "typst"])
@pytest.mark.parametrize("paper", ["a4", "a3"])
@pytest.mark.parametrize("interface", ["cli", "api"])
def test_generate_sort_correct_workflow(engine, paper, interface, tmp_path):
    required = ["gs", "latexmk", "xelatex"] if engine == "latex" else ["gs", "typst"]
    missing = [executable for executable in required if shutil.which(executable) is None]
    assert not missing, f"Missing required {engine} integration tools: {', '.join(missing)}"

    output = tmp_path / "generated"
    if interface == "cli":
        subprocess.run(
            [
                sys.executable,
                str(EXAMPLE_SCRIPT),
                "--engine",
                engine,
                "--output",
                str(output),
                "--paper",
                paper,
            ],
            cwd=tmp_path,
            check=True,
        )
    else:
        generate_engine(engine, paper.upper(), output)

    destination = tmp_path / "generated" / engine / paper

    exam_pdf = destination / "exam.pdf"
    corrected_pdf = destination / "corrected.pdf"
    sorted_pages = sorted((destination / "sorted").glob("*.png"))
    expected_sheets = 6 if paper == "a4" else 4
    assert len(PdfReader(exam_pdf).pages) == expected_sheets
    assert len(sorted_pages) == 6
    assert len(PdfReader(corrected_pdf).pages) == 6

    page_metadata = [qrdecoder.decode(cv2.imread(str(page))) for page in sorted_pages]
    page_metadata.sort(key=lambda metadata: metadata["page"])
    assert [metadata["range"] for metadata in page_metadata] == EXPECTED_RANGES

    exam_text = "\n".join(page.extract_text() or "" for page in PdfReader(exam_pdf).pages)
    assert "Progetta una funzione di validazione" in exam_text
    assert "Analizza una scelta progettuale" in exam_text

    with TinyDB(destination / "exam.json") as database:
        correction = database.table("correction").get(Query().student_id == "1001")

    assert correction is not None
    assert correction["given_answers"] == EXPECTED_ANSWERS
    assert correction["doubtful"] == [False] * 8