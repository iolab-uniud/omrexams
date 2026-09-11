import json
import os
import random
import re
import shutil
import subprocess

import click
from mistletoe.base_renderer import BaseRenderer

from .crypt import binary_encrypt
from .markdown import (
    LatexCommand,
    LatexFormula,
    Lines,
    QuestionBlock,
    QuestionList,
    QuestionMarker,
    QuestionTopic,
    SaytexFormula,
)


MAX_ANSWERS = 7


def _typst_string(value):
    return json.dumps(str(value), ensure_ascii=False)


class TypstDocument:
    def __init__(self, source, root=None):
        self.source = source
        self.root = root or os.getcwd()

    def generate_pdf(self, filepath, **_kwargs):
        compiler = shutil.which("typst")
        if compiler is None:
            raise RuntimeError(
                "Typst is required for exam.engine=typst but was not found in PATH"
            )
        source_path = f"{filepath}.typ"
        output_path = f"{filepath}.pdf"
        with open(source_path, "w", encoding="utf-8") as source_file:
            source_file.write(self.source)
        subprocess.run(
            [compiler, "compile", "--root", self.root, source_path, output_path],
            check=True,
        )


class TypstRenderer(BaseRenderer):
    _special_characters = re.compile(r"([\\#$*_<>\[\]@])")

    def __init__(self, *extras, **kwargs):
        self.parameters = kwargs
        super().__init__(*extras)

    def render_raw_text(self, token, escape=True):
        if not escape:
            return token.content
        return self._special_characters.sub(r"\\\1", token.content)

    def render_paragraph(self, token):
        return f"\n{self.render_inner(token)}\n"

    def render_strong(self, token):
        return f"#strong[{self.render_inner(token)}]"

    def render_emphasis(self, token):
        return f"#emph[{self.render_inner(token)}]"

    def render_strikethrough(self, token):
        return f"#strike[{self.render_inner(token)}]"

    def render_inline_code(self, token):
        content = self.render_raw_text(token.children[0], escape=False)
        return f"#raw({_typst_string(content)})"

    def render_link(self, token):
        return f"#link({_typst_string(token.target)})[{self.render_inner(token)}]"

    def render_auto_link(self, token):
        return f"#link({_typst_string(token.target)})"

    def render_image(self, token):
        path = token.src
        if not os.path.isabs(path):
            path = os.path.join(self.parameters.get("basedir", ""), path)
        alt = self.render_to_plain(token)
        size = re.search(r"(?:width|height)=([0-9.]+(?:cm|mm|in|pt|%))", alt)
        size_arg = f", width: {size.group(1)}" if size else ", width: 100%"
        return f"\n#image({_typst_string(os.path.realpath(path))}{size_arg})\n"

    def render_heading(self, token):
        return f"\n{'=' * token.level} {self.render_inner(token).strip()}\n"

    def render_list(self, token):
        function = "enum" if token.start is not None else "list"
        return f"\n#{function}(\n{self.render_inner(token)})\n"

    def render_list_item(self, token):
        return f"[{self.render_inner(token)}],\n"

    def render_block_code(self, token):
        content = self.render_raw_text(token.children[0], escape=False)
        language = token.language or ""
        return f"\n#raw(block: true, lang: {_typst_string(language)}, {_typst_string(content)})\n"

    def render_quote(self, token):
        return f"\n#quote(block: true)[{self.render_inner(token)}]\n"

    def render_thematic_break(self, _token):
        return "\n#line(length: 100%)\n"

    def render_line_break(self, _token):
        return " \\\n"

    def render_escape_sequence(self, token):
        return self.render_inner(token)

    def render_table(self, token):
        rows = []
        if hasattr(token, "header"):
            rows.append(self.render_table_row(token.header))
        rows.extend(self.render(child) for child in token.children)
        cells = "".join(rows)
        return f"\n#table(columns: {len(token.column_align)},\n{cells})\n"

    def render_table_row(self, token):
        return "".join(self.render(child) for child in token.children)

    def render_table_cell(self, token):
        return f"[{self.render_inner(token)}],\n"

    def render_math(self, token):
        return f"${self.render_inner(token)}$"

    def render_to_plain(self, token):
        if hasattr(token, "children"):
            return "".join(self.render_to_plain(child) for child in token.children)
        return getattr(token, "content", "")


class TypstQuestionRenderer(TypstRenderer):
    def __init__(self, *extras, **kwargs):
        self.record_answers = False
        self.questions = []
        custom_tokens = [
            QuestionMarker,
            QuestionTopic,
            QuestionList,
            QuestionBlock,
            Lines,
            LatexCommand,
            LatexFormula,
            SaytexFormula,
        ]
        super().__init__(*custom_tokens, *extras, **kwargs)

    def render_latex_formula(self, token):
        function = "mi" if token.symbol == "$" else "mitex"
        return f"#{function}({_typst_string(token.content)})"

    def render_latex_command(self, token):
        raise ValueError(
            f"Raw LaTeX command {token.command!r} cannot be rendered by Typst"
        )

    def render_saytex_formula(self, token):
        try:
            import saytex
            compiler = saytex.Saytex()
            latex_code = compiler.to_latex(token.content)
        except Exception:
            latex_code = token.content
        return f"#mi({_typst_string(latex_code)})"

    def render_question_marker(self, token):
        if not self.record_answers:
            raise ValueError("Question marker used outside an answer list")
        self.questions[-1]["answers"].append(token.marker != " ")
        return ""

    def render_question_topic(self, token):
        if not self.parameters.get("test", False):
            return ""
        return f"#box(stroke: 0.5pt, inset: 2pt)[#raw({_typst_string(token.id)})]"

    def render_lines(self, token):
        match = re.match(r"\\fillwithdottedlines\{([0-9.]+)([^}]+)\}", token.lines)
        if not match:
            return ""
        return f"#v({match.group(1)}{match.group(2)}, weak: false)"

    def render_question_block(self, token):
        self.questions.append(
            {"question": "", "answers": [], "permutation": [], "type": None}
        )
        inner = self.render_inner(token)
        prefix = ""
        if "\\[NEWPAGE\\]" in inner:
            inner = inner.replace("\\[NEWPAGE\\]", "")
            prefix = "#pagebreak()\n"
        if not self.questions[-1]["question"]:
            return inner
        number = len(self.questions)
        labels = tuple(chr(ord("A") + index) for index in range(len(self.questions[-1]["answers"])))
        title = self.questions[-1]["question"]
        return f"\n{prefix}#question({number}, {self._tuple(labels)}, [{title}])[{inner}]\n"

    def render_heading(self, token):
        if token.level == 1:
            return super().render_heading(token) if self.parameters.get("test", False) else ""
        if token.level > 2:
            return f"#strong[Q:] {self.render_inner(token).strip()}\n"
        inner = self.render_inner(token).strip()
        self.questions[-1]["question"] = inner
        return ""

    def render_question_list(self, token):
        self.record_answers = True
        answers = [self.render_list_item(child) for child in token.children]
        if len(answers) != len(self.questions[-1]["answers"]):
            raise ValueError(
                f"Answers mismatch for question {self.questions[-1]['question']!r} "
                f"({len(answers)}/{len(self.questions[-1]['answers'])})"
            )
        self.record_answers = False
        if len(answers) > MAX_ANSWERS:
            click.secho(
                f"Too many answers for question {self.questions[-1]['question']!r} "
                f"({len(answers)})",
                fg="yellow",
            )
        permutation = list(range(len(answers)))
        if self.parameters.get("shuffle", True) and token.start is None:
            random.shuffle(permutation)
            self.questions[-1]["answers"] = [
                self.questions[-1]["answers"][index] for index in permutation
            ]
            answers = [answers[index] for index in permutation]
        self.questions[-1]["permutation"] = permutation

        items = []
        for index, answer in enumerate(answers):
            label = chr(ord("A") + index)
            if self.parameters.get("test", False) and self.questions[-1]["answers"][index]:
                label = f"#strong[✓ {label})]"
            else:
                label = f"#strong[{label})]"
            items.append(f"[{label} {answer}]")
        if token.leader == "*" or token.leader.endswith(")"):
            return (
                "\n#grid("
                f"columns: (1fr,) * {len(items)}, "
                "column-gutter: 8pt, row-gutter: 4pt, "
                + ", ".join(items)
                + ")\n"
            )
        return "".join(f"#block{item}\n" for item in items)

    def render_list_item(self, token):
        if self.record_answers:
            return self.render_inner(token).strip()
        return super().render_list_item(token)

    def render_document(self, token):
        if self.parameters.get("test", False):
            self.parameters["shuffle"] = False
        inner = self.render_inner(token)
        self.questions = [question for question in self.questions if question["question"]]
        if self.parameters.get("test", False):
            solution = ""
        else:
            solutions = []
            for question_data in self.questions:
                solutions.append(
                    "".join(
                        chr(ord("A") + index)
                        for index, correct in enumerate(question_data["answers"])
                        if correct
                    )
                )
            if self.parameters.get("encrypt", True):
                solution = binary_encrypt(solutions, self.parameters["student_no"])
            else:
                solution = ",".join(solutions)
        source = self._document_source(inner, solution)
        return TypstDocument(source, root=os.path.abspath(os.sep))

    def _document_source(self, inner, solution):
        parameters = self.parameters
        date = parameters["date"].strftime("%d/%m/%Y")
        warning = parameters.get("warning", "")
        warning_argument = f"  warning: [{warning}],\n" if warning else ""
        qr_eclevel = str(parameters.get("qr_eclevel", "H")).upper()
        if qr_eclevel not in {"L", "M", "Q", "H"}:
            raise ValueError("qr_eclevel must be one of L, M, Q, or H")
        return (
            '#import "omrexam.typ": exam, question, mi, mitex\n\n'
            "#show: body => exam(\n"
            f"  student-id: {_typst_string(parameters.get('student_no', ''))},\n"
            f"  student-name: {_typst_string(parameters.get('student_name', ''))},\n"
            f"  exam-name: {_typst_string(parameters.get('exam', ''))},\n"
            f"  exam-date: {_typst_string(date)},\n"
            f"  solution: {_typst_string(solution)},\n"
            f"  header: [{parameters.get('header', '')}],\n"
            f"  footer: [{parameters.get('footer', '')}],\n"
            f"{warning_argument}"
            f"  qr-error-correction: {_typst_string(qr_eclevel)},\n"
            f"  show-roi: {str(parameters.get('show_roi', parameters.get('test', False))).lower()},\n"
            "  body,\n"
            ")\n\n"
            f"{parameters.get('preamble', '')}\n{inner}"
        )

    @staticmethod
    def _tuple(values):
        if not values:
            return "()"
        return "(" + ", ".join(_typst_string(value) for value in values) + ",)"


class TypstDocumentStripRenderer(TypstRenderer):
    def render_document(self, token):
        return self.render_inner(token)