# OMRExams

An Optimal Marker Recognition Exams generator and corrector.

## Installation

Install the `omrexams` command globally for the current user from this checkout:

```console
uv tool install .
omrexams --help
```

Use an editable installation while developing, so source changes are available
without reinstalling:

```console
uv tool install --force --editable .
```

If uv reports that its executable directory is not on `PATH`, run
`uv tool update-shell` once and restart the shell. Remove the installation with
`uv tool uninstall omrexams`.

The Python package and its dependencies are isolated from the macOS system
Python. External document tools are still installed separately: Typst requires
the `typst` executable, while LaTeX generation requires the configured TeX
toolchain; sorting and collation also use Ghostscript.

## Development

The project uses [uv](https://docs.astral.sh/uv/) for Python versions,
dependencies, virtual environments, locking, testing, and builds.

```console
uv sync --locked
uv run --locked pytest -q
uv build --no-sources
```

For an isolated one-shot environment, install the current project alongside
pytest explicitly:

```console
uvx --with-editable . pytest -q
```

Do not use bare `uvx pytest`: `uvx` isolates the tool and does not install this
project or runtime dependencies such as `pypdf` and OpenCV.

Runtime dependencies are declared in `project.dependencies`; development tools
live in the default `dev` dependency group. Use `uv add <package>` for runtime
dependencies and `uv add --dev <package>` for development dependencies. Commit
`uv.lock` whenever dependencies change.

The `zxing-cpp` dependency is built from source through the project-level uv
configuration, replacing the previous pip constraint workflow.

## Document engine

LaTeX remains the default document engine. To generate exams with Typst, install
the `typst` executable and select it in the YAML configuration:

```yaml
exam:
  engine: typst
  name: Example exam
  language: italian
  qr_eclevel: H

warning: |
  **Non scrivere in quest'area!**
  Usa solo per indicare le risposte definitive.
```

The Typst backend preserves question and answer shuffling, encrypted answer QR
codes, per-page OMR geometry QR codes, images, code blocks, open questions, and
PDF collation. Code blocks are rendered with Codly, including syntax
highlighting, line numbers, and language labels. The `packages` and `commands`
configuration keys contain raw LaTeX and are therefore rejected when the Typst
engine is selected. Raw LaTeX commands embedded in Markdown must likewise be
replaced with portable Markdown or native Typst content.

Typst packages are downloaded from Typst Universe on the first compilation.
Both page QR codes use error-correction level `H`. The optional top-level
`warning` value accepts Markdown and replaces the default Italian notice above
the answer area. The `exam.language` setting is also passed to Typst; for
example, `italian` selects `lang: "it"`, enabling Italian hyphenation in the
justified question text. Hyphenation is enabled explicitly in the Typst
template.

The `omrexams test` command shows the answer ROI by default when using Typst.
Set `exam.show_roi: false` in the YAML configuration to hide it in test PDFs.

Open questions can reserve visible dotted writing space with the shared Markdown
syntax `{lines:5cm}`. Normal source line wrapping remains a soft break; use the
standard Markdown hard break when a visible line break is required.

Question answer lists preserve the existing Markdown layout convention in both
backends: `- [ ]` and `+ [ ]` render vertically, while `* [ ]` and `1) [ ]`
render horizontally. Ordered answer lists are not shuffled.

The [`examples`](examples) directory contains a shared multipage fixture
and a generator for visual LaTeX/Typst output in A4 and folded A3 formats. The
integration smoke tests run the same fixture through generation, artificial
answer marking, `sort`, and `correct` for all four engine/paper combinations,
both through the CLI and through direct Python calls. The fixture has four OMR
pages plus a dedicated long-text OMR page, followed by two open-question pages.
The latter carry QR range `(0, 0)`, have no answer markers, and use the full
printable width in the Typst backend.

## Python API

The CLI is a thin entry point over the public Python classes. The equivalent
generation, sorting, and correction workflow is:

```python
from omrexams import Correct, Generate, Sort

generator = Generate(
  config,
  questions_dir,
  output_prefix,
  students=[("1001", "Ada Lovelace")],
  seed=42,
  paper="A4",
)
generator.process()

sorter = Sort(
  [f"{output_prefix}.pdf"],
  sorted_dir,
  f"{output_prefix}.json",
)
sorter.sort(300, "A4")

corrector = Correct(
  sorted_dir,
  corrected_pdf,
  f"{output_prefix}.json",
  300,
  70,
)
corrector.correct()
```

`config` is the parsed YAML mapping with `config["basedir"]` set to the
configuration directory. See
[`examples/generate_examples.py`](examples/generate_examples.py) for a complete
runnable API workflow, including synthetic answer marking.

