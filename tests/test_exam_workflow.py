from pathlib import Path
import shutil
import subprocess
import sys

from pypdf import PdfReader
import pytest
from tinydb import Query, TinyDB

EXPECTED_ANSWERS = [["A"], ["B"], ["B"], ["C"], ["C"], ["D"]]
EXAMPLE_SCRIPT = Path(__file__).parents[1] / "examples" / "generate_examples.py"


@pytest.mark.parametrize("engine", ["latex", "typst"])
@pytest.mark.parametrize("paper", ["a4", "a3"])
def test_generate_sort_correct_workflow(engine, paper, tmp_path):
    required = ["gs", "latexmk", "xelatex"] if engine == "latex" else ["gs", "typst"]
    missing = [executable for executable in required if shutil.which(executable) is None]
    assert not missing, f"Missing required {engine} integration tools: {', '.join(missing)}"

    subprocess.run(
        [
            sys.executable,
            str(EXAMPLE_SCRIPT),
            "--engine",
            engine,
            "--output",
            str(tmp_path / "generated"),
            "--paper",
            paper,
        ],
        cwd=tmp_path,
        check=True,
    )
    destination = tmp_path / "generated" / engine / paper

    exam_pdf = destination / "exam.pdf"
    corrected_pdf = destination / "corrected.pdf"
    sorted_pages = sorted((destination / "sorted").glob("*.png"))
    expected_sheets = 4 if paper == "a4" else 2
    assert len(PdfReader(exam_pdf).pages) == expected_sheets
    assert len(sorted_pages) == 3
    assert len(PdfReader(corrected_pdf).pages) == 3

    with TinyDB(destination / "exam.json") as database:
        correction = database.table("correction").get(Query().student_id == "1001")

    assert correction is not None
    assert correction["given_answers"] == EXPECTED_ANSWERS
    assert correction["doubtful"] == [False] * 6