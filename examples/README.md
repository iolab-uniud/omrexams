# Visual examples

This directory contains a shared multipage question fixture and matching LaTeX
and Typst configurations. Generate the original exams, artificially marked page
images, and correction reports with:

```console
uv run python examples/generate_examples.py
```

Use `--engine latex` or `--engine typst` to generate only one backend, and
`--paper a4` or `--paper a3` to select one paper layout. By default all four
combinations are generated below `examples/generated/<engine>/<paper>/`:

- `exam.pdf`: generated blank exam;
- `sorted/*.png`: pages with artificial answer marks;
- `corrected.pdf`: visual correction report;
- `exam.json`: generated questions and detected answers.

The same workflow can be called directly as a Python API:

```python
from pathlib import Path

from examples.generate_examples import generate_engine

destination = generate_engine("typst", "A4", Path("examples/generated"))
```

The automated equivalent is `tests/test_exam_workflow.py`. It verifies LaTeX
and Typst on A4 and folded A3 through both subprocess and direct function calls,
passes `paper=A3` to both generation and sorting, and checks all artificially
marked answers after correction. Every exam contains five OMR pages, including
a dedicated long-text layout example, followed by two open-question pages with
no answer markers.