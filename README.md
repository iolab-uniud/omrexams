# OMRExams

## Modular configuration and student profiles

`omrexams.configuration.load_config` reads monolithic YAML or a main
`config/config.yaml` referencing variant YAML and header/footer/preamble files.
`save_config_directory` writes the same modular structure. Only relative references
contained in the configuration directory are accepted; missing parts are errors.

```yaml
seed: 42
exam:
  name: Exam
  language: italian
excel:
  fields: {id: ID, name: Name, surname: Surname}
profile_selector:
  default: full
  rules:
    - column: Misure Compensative
      operator: not_empty
      profile: reduced
profiles:
  full: variants/full.yaml
  reduced: variants/reduced.yaml
header_file: templates/header.md
footer_file: templates/footer.md
```

Each variant file is an object with optional `questions`, `exam`, `easyreading`
and `dyslexia` overrides. `questions` replaces the common list and uses the existing
`from`, `draw` and optional `range` rules. `exam` can override `max_questions`,
`max_open_questions`, `max_total_questions`, `page_limits`, `shuffle_questions`, `shuffle_answers` and
`separate_open_questions`; other settings remain common. Question limits may be
zero for separate closed/open caps. The combined `max_total_questions` and page
limits must be positive. The combined cap keeps closed questions first, then open
questions until the remaining capacity is exhausted. Inline variant objects remain supported.

`profile_selector.rules` is an ordered list of `{column, operator, value?, profile}`.
Each rule may use a different spreadsheet column. Operators are `equals`,
`contains` (literal substring), `empty` and `not_empty`. The first matching rule
wins; unmatched students and anonymous/additional exams use `default`. Missing or
duplicated columns, unsupported operators and blank comparison values are errors.
Selection and the web student filter share `omrexams.students.student_condition_mask`.
Rules cannot be combined with the legacy selector fields.

In legacy selectors, blank metadata selects `default`. Exact mappings take priority; other nonblank
values select the optional `non_empty` profile. Without `non_empty`, unmatched
values are errors. Mapping may be omitted when no exact-value overrides are needed.
Column names and values ignore surrounding whitespace and case. Unknown values
report the original spreadsheet row. Anonymous/additional exams use the default
profile. Named profiles are independent of student order and replace the legacy
first-N dyslexia selection. EasyReading inherits the common setting unless
explicitly overridden. Each exam's generation JSON records its `profile`.

The shared Excel reader detects a table header from distinct text labels and
consistently populated following rows, allowing unlabelled interior columns. It
does not depend on identity-column names or a 100-row ceiling. Detection is
heuristic; `excel.data_marker.skip_rows` or `skip_until` can select the header
explicitly. Returned row offsets preserve original spreadsheet error locations.

```sh
omrexams generate --config config/config.yaml --students students.xlsx questions/
omrexams generate --config config/config.yaml --students students.xlsx --seed 7 questions/
```

The config seed defaults to 42; explicit CLI or `Generate(..., seed=...)` values
override it, including zero. Comprehensive test PDFs include files from every
profile but retain the common layout and do not perform randomized selection.

An Optimal Marker Recognition Exams generator and corrector.

Inline Markdown code in questions and answers wraps automatically at the text
margin. It is rendered with `fvextra`, which must be installed in the LaTeX
environment; the backend Docker image already includes it.
Long unbroken alphanumeric text can wrap, and Markdown tables are scaled down
if needed to fit the available width. Arbitrary LaTeX and indivisible formulas
may still extend beyond the margin and should be checked in the generated PDF.
Optical answer markers are aligned with the beginning of each question.
Overfull checking is disabled by default. Enable it in the web interface, with
`--check-overfull` for the `generate` or `test` CLI command, or by passing
`check_overfull=True` to `Generate`. LaTeX overfull boxes exceeding the 0.5 cm
separation from the OMR margin produce a warning with the exam, student's name
and ID, and the question number when available. These are potential overflows:
nested boxes can produce false positives, and positioned or overlay content may
not be detected.

## Bundled fonts

The EasyReading fonts and their usage disclaimer are included in the Python package:

- [EasyReadingPRO.ttf](src/omrexams/texmf/fonts/EasyReadingPRO.ttf)
- [EasyReadingPROItalic.ttf](src/omrexams/texmf/fonts/EasyReadingPROItalic.ttf)
- [EasyReadingPRO disclaimer](src/omrexams/texmf/fonts/EasyReadingPRO-disclaimer.pdf)

The fonts are installed in the backend Docker image for XeLaTeX. Do not modify the font files.

## Validate generated A4 exams

```sh
omrexams check-qrs exam.pdf
omrexams check-qrs exam.pdf --json-file exam.json --json-output
```

The shared `omrexams.qr_validation.validate_exam_qrs` function reads both QR codes on every non-padding page and checks student IDs, page sequences, question ranges and decoded correction keys against the generation JSON. It does not modify either file. Exit code is 0 when validation passes and 1 for discrepancies or unreadable QR codes.

By default the JSON is resolved beside the PDF. Split files such as `exam-01.pdf` use `exam.json`; their report checks the students in that part, not whether all parts of the generation are present. A3 booklets are not supported.

