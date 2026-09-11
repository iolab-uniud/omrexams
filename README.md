# OMRExams

An Optimal Marker Recognition Exams generator and corrector.

## Development

The project uses [uv](https://docs.astral.sh/uv/) for Python versions,
dependencies, virtual environments, locking, testing, and builds.

```console
uv sync
uv run pytest
uv build
```

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
the answer area.

The `omrexams test` command shows the answer ROI by default when using Typst.
Set `exam.show_roi: false` in the YAML configuration to hide it in test PDFs.

