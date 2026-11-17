import json
import random
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yaml
from click.testing import CliRunner

from omrexams.cli import cli
from omrexams.generate import Generate
from omrexams.profiles import config_for_profile, select_student_profiles, validate_profiles
from omrexams.configuration import load_config, save_config_directory
from omrexams.students import read_student_dataframe


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for filename, count in (("whole.md", 3), ("reduced.md", 2)):
            content = "---\n".join(
                f"## Question {number}\n- [x] Yes\n- [ ] No\n"
                for number in range(count)
            )
            (self.root / filename).write_text(
                "---\n" + content + "---\n### Open question\n{lines:1cm}\n---\n",
                encoding="utf-8",
            )
        self.config = {
            "exam": {"name": "Synthetic", "language": "italian", "shuffle_questions": True},
            "easyreading": False,
            "questions": [{"from": "unused.md", "draw": 1}],
            "profiles": {
                "intero": {
                    "questions": [{"from": "whole.md", "draw": 3}],
                    "exam": {"max_questions": 2, "max_open_questions": 1, "page_limits": 2},
                },
                "ridotto": {
                    "questions": [{"from": "reduced.md", "draw": 2}],
                    "exam": {"max_questions": 1, "max_open_questions": 0, "page_limits": 1},
                    "easyreading": True,
                    "dyslexia": True,
                },
            },
            "profile_selector": {
                "column": "Profilo", "default": "intero",
                "mapping": {"intero": "intero", "ridotto": "ridotto", "pieno": "ridotto"},
            },
        }

    def test_excel_selector_uses_mapping_blanks_and_original_rows(self):
        frame = pd.DataFrame({" PROFILO ": [" intero ", "RIDOTTO", "", None, float("nan"), "pieno"]})
        self.assertEqual(
            select_student_profiles(frame, self.config),
            ["intero", "ridotto", "intero", "intero", "intero", "ridotto"],
        )
        with self.assertRaisesRegex(ValueError, "non trovata"):
            select_student_profiles(pd.DataFrame({"Other": ["ridotto"]}), self.config)
        frame = pd.DataFrame({"Profilo": ["unknown"]}, index=[7])
        with self.assertRaisesRegex(ValueError, "riga Excel 12"):
            select_student_profiles(frame, self.config, first_data_row=5)
        self.assertEqual(select_student_profiles(frame, {"exam": {}}), [None])

    def test_invalid_profiles_fail_before_overwriting_generation_json(self):
        output = self.root / "exam.json"
        output.write_text("original")
        for alter in (
            lambda config: config["profile_selector"].update(default="missing"),
            lambda config: config["profile_selector"]["mapping"].update(pieno="missing"),
            lambda config: config["profile_selector"]["mapping"].update(Pieno="intero"),
            lambda config: config["profiles"]["ridotto"]["exam"].update(page_limits=0),
            lambda config: config["profiles"]["ridotto"].update(easyreading="yes"),
        ):
            with self.subTest(alter=alter):
                config = deepcopy(self.config)
                alter(config)
                with self.assertRaises(ValueError):
                    Generate(config, str(self.root), str(self.root / "exam"))
                self.assertEqual(output.read_text(), "original")

    def test_ordered_conditions_stop_at_first_match_and_fall_back_to_default(self):
        frame = pd.DataFrame({
            " Flag ": [".", "yes", None, "  ", float("nan"), "no"],
            "Category": ["special", " SPECIAL ", "special", "other", None, "other"],
        }, index=[7, 10, 15, 20, 30, 40])
        self.config["profile_selector"] = {
            "default": "intero",
            "rules": [
                {"column": "Flag", "operator": "contains", "value": ".", "profile": "ridotto"},
                {"column": "category", "operator": "equals", "value": "special", "profile": "intero"},
                {"column": "Flag", "operator": "not_empty", "profile": "ridotto"},
            ],
        }
        original = deepcopy(self.config)
        self.assertEqual(select_student_profiles(frame, self.config), ["ridotto", "intero", "intero", "intero", "intero", "ridotto"])
        self.assertEqual(self.config, original)
        rules = self.config["profile_selector"]["rules"]
        rules[0], rules[1] = rules[1], rules[0]
        self.assertEqual(select_student_profiles(frame, self.config)[0], "intero")
        self.config["profile_selector"]["rules"] = [{"column": "Flag", "operator": "empty", "profile": "ridotto"}]
        self.assertEqual(select_student_profiles(frame, self.config), ["intero", "intero", "ridotto", "ridotto", "ridotto", "intero"])
        self.config["profile_selector"]["rules"] = []
        self.assertEqual(select_student_profiles(frame, self.config), ["intero"] * 6)

    def test_invalid_ordered_conditions_and_missing_columns_fail_explicitly(self):
        for rule in (
            {"column": "", "operator": "empty", "profile": "ridotto"},
            {"column": "Flag", "operator": "equals", "value": " ", "profile": "ridotto"},
            {"column": "Flag", "operator": "unknown", "profile": "ridotto"},
            {"column": "Flag", "operator": "empty", "profile": "missing"},
        ):
            with self.subTest(rule=rule):
                self.config["profile_selector"] = {"default": "intero", "rules": [rule]}
                with self.assertRaises(ValueError):
                    validate_profiles(self.config)
        self.config["profile_selector"] = {
            "default": "intero", "rules": [{"column": "Flag", "operator": "empty", "profile": "ridotto"}]
        }
        with self.assertRaisesRegex(ValueError, "non trovata"):
            select_student_profiles(pd.DataFrame({"Other": ["value"]}), self.config)
        with self.assertRaisesRegex(ValueError, "duplicata"):
            select_student_profiles(pd.DataFrame([["a", "b"]], columns=["Flag", " flag "]), self.config)

    def test_ordered_conditions_handle_missing_dates_and_nullable_numbers(self):
        frame = pd.DataFrame({
            "Date": pd.to_datetime(["2026-01-01", None]),
            "Number": pd.Series([1, pd.NA], dtype="Int64"),
        })
        self.config["profile_selector"] = {
            "default": "intero",
            "rules": [
                {"column": "Date", "operator": "empty", "profile": "ridotto"},
                {"column": "Number", "operator": "equals", "value": "1", "profile": "ridotto"},
            ],
        }
        self.assertEqual(select_student_profiles(frame, self.config), ["ridotto", "ridotto"])

    def test_mixed_question_banks_caps_fonts_and_json_are_isolated_and_deterministic(self):
        original = deepcopy(self.config)
        generator = Generate(
            self.config, str(self.root), str(self.root / "exam"),
            students=[("1", "Synthetic One", "intero"), ("2", "Synthetic Two", "ridotto")],
        )
        with patch.object(generator, "_generate_loaded_exams"):
            generator.generate_exams()
        for student, count, answers_count, source in (
            (generator.students[0], 2, 2, "whole.md"),
            (generator.students[1], 1, 1, "reduced.md"),
        ):
            profile = generator.profile_generators[student[2]]
            random.seed(42 + int(student[0]))
            document, questions, answers = profile.create_exam(student, profile.config.get("dyslexia", False))
            random.seed(42 + int(student[0]))
            _, repeated_questions, repeated_answers = profile.create_exam(student, profile.config.get("dyslexia", False))
            self.assertEqual((questions, answers), (repeated_questions, repeated_answers))
            self.assertEqual(len(questions), count)
            self.assertEqual(len(answers), answers_count)
            self.assertTrue(all(question[0] == source for question in questions))
            self.assertEqual("easyreading" in document.dumps(), student[2] == "ridotto")
            self.assertEqual("Open question" in document.dumps(), student[2] == "intero")
            generator.append_exam(student, questions, answers)
        saved = json.loads((self.root / "exam.json").read_text())
        self.assertEqual([exam["profile"] for exam in saved["exams"].values()], ["intero", "ridotto"])
        self.assertEqual(self.config, original)

    def test_font_inheritance_anonymous_default_and_legacy_count(self):
        self.config["easyreading"] = True
        self.config["profiles"]["intero"]["easyreading"] = False
        del self.config["profiles"]["ridotto"]["easyreading"]
        self.assertFalse(config_for_profile(self.config, "intero")["easyreading"])
        self.assertTrue(config_for_profile(self.config, "ridotto")["easyreading"])
        generator = Generate(
            self.config, str(self.root), str(self.root / "exam"), students=[("1", "Anonymous")]
        )
        self.assertEqual(generator.students[0][2], "intero")
        with self.assertRaisesRegex(ValueError, "prime N righe"):
            Generate(self.config, str(self.root), str(self.root / "exam"), dyslexia_count=1)
        validate_profiles({"exam": {}, "easyreading": True})

    def test_cli_reads_standard_excel_headers_and_passes_selected_profiles(self):
        excel = self.root / "students.xlsx"
        pd.DataFrame({
            "id": ["1", "2"], "name": ["Synthetic", "Synthetic"],
            "surname": ["One", "Two"], "Profilo": ["", "pieno"],
        }).to_excel(excel, index=False)
        self.config["excel"] = {"fields": {"id": "id", "name": "name", "surname": "surname"}}
        config_file = self.root / "config.yaml"
        for selector in (
            self.config["profile_selector"],
            {"default": "intero", "rules": [{"column": "Profilo", "operator": "equals", "value": "pieno", "profile": "ridotto"}]},
        ):
            with self.subTest(selector=selector):
                self.config["profile_selector"] = selector
                config_file.write_text(yaml.safe_dump(self.config))
                with patch("omrexams.cli.Generate") as generate:
                    result = CliRunner().invoke(cli, [
                        "generate", "--config", str(config_file), "--students", str(excel),
                        "--date", "2026-01-01", "--seed", "42", "--additional", "0",
                        "--output_prefix", str(self.root / "cli-exam"), str(self.root),
                    ])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual([student[2] for student in generate.call_args.kwargs["students"]], ["intero", "ridotto"])

    def test_cli_overfull_check_is_opt_in_for_generation_and_test(self):
        config_file = self.root / "config.yaml"
        config_file.write_text(yaml.safe_dump(self.config))
        for command in ("generate", "test"):
            for enabled in (False, True):
                with self.subTest(command=command, enabled=enabled):
                    options = ["--config", str(config_file)]
                    if command == "generate":
                        options += ["--count", "1", "--date", "2026-01-01",
                                    "--output_prefix", str(self.root / "cli-exam")]
                    else:
                        options += ["--output", str(self.root / "cli-test.pdf")]
                    if enabled:
                        options.append("--check-overfull")
                    with patch("omrexams.cli.Generate") as generate:
                        result = CliRunner().invoke(cli, [command, *options, str(self.root)])
                    self.assertEqual(result.exit_code, 0, result.output)
                    self.assertEqual(generate.call_args.kwargs["check_overfull"], enabled)

    def test_modular_config_supports_arbitrary_variants_templates_and_seed_override(self):
        self.config["profiles"]["third"] = {"exam": {"max_questions": 0}}
        self.config.update(seed=0, header="Shared header", footer="Shared footer", preamble="Shared preamble")
        root = self.root / "config"
        save_config_directory(self.config, root)
        main = yaml.safe_load((root / "config.yaml").read_text())
        self.assertEqual(main["profiles"]["third"], "variants/third.yaml")
        self.assertEqual(main["header_file"], "templates/header.md")
        self.assertEqual(load_config(root / "config.yaml"), self.config)
        generator = Generate(self.config, str(self.root), str(self.root / "exam"))
        self.assertEqual(generator.seed, 0)
        overridden = Generate(self.config, str(self.root), str(self.root / "override"), seed=7)
        self.assertEqual(overridden.seed, 7)

    def test_missing_external_or_conflicting_parts_are_explicit_errors(self):
        root = self.root / "config"
        root.mkdir()
        for reference in ("variants/missing.yaml", "../outside.yaml"):
            config = deepcopy(self.config)
            config["profiles"]["intero"] = reference
            (root / "config.yaml").write_text(yaml.safe_dump(config))
            with self.assertRaises(ValueError):
                load_config(root / "config.yaml")
        save_config_directory(self.config, root)
        main = yaml.safe_load((root / "config.yaml").read_text())
        main.update(header="inline", header_file="templates/header.md")
        (root / "templates").mkdir()
        (root / "templates" / "header.md").write_text("separate")
        (root / "config.yaml").write_text(yaml.safe_dump(main))
        with self.assertRaisesRegex(ValueError, "solo header"):
            load_config(root / "config.yaml")

    def test_saving_rejects_symlinked_parts_without_writing_outside_config(self):
        root = self.root / "config"
        root.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (root / "variants").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "simbolico"):
            save_config_directory(self.config, root)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((root / "config.yaml").exists())

    def test_compensative_metadata_selects_empty_filled_and_explicit_extra_variant(self):
        config = deepcopy(self.config)
        config["profiles"]["third"] = {}
        config["profile_selector"] = {
            "column": "Misure Compensative", "default": "intero",
            "non_empty": "ridotto", "mapping": {"specific": "third"},
        }
        frame = pd.DataFrame({"Misure Compensative": ["", None, "  ", "any measures", "SPECIFIC"]})
        self.assertEqual(select_student_profiles(frame, config), ["intero", "intero", "intero", "ridotto", "third"])
        config["profile_selector"]["non_empty"] = "missing"
        with self.assertRaises(ValueError):
            select_student_profiles(frame, config)

    def test_combined_cap_counts_closed_and_open_questions(self):
        self.config["profiles"]["intero"]["exam"] = {"max_total_questions": 3}
        generator = Generate(self.config, str(self.root), str(self.root / "exam"), students=[("1", "Synthetic", "intero")])
        with patch.object(generator, "_generate_loaded_exams"):
            generator.generate_exams()
        profile = generator.profile_generators["intero"]
        with patch.object(profile, "draw_questions", side_effect=[
            [("whole.md", 0, "## Closed one\n- [x] Yes\n- [ ] No\n"),
             ("whole.md", 1, "## Closed two\n- [x] Yes\n- [ ] No\n")],
            [("whole.md", 0, "### Open one\n{lines:1cm}\n"),
             ("whole.md", 1, "### Open two\n{lines:1cm}\n")],
        ]):
            document, questions, _ = profile.create_exam(generator.students[0])
        self.assertEqual(len(questions), 2)
        self.assertEqual(sum(label in document.dumps() for label in ("Open one", "Open two")), 1)

    def test_excel_headers_and_generation_share_marker_and_row_offset(self):
        excel = self.root / "headers.xlsx"
        pd.DataFrame([
            ["marker", None, None],
            ["Matricola", "Nome", "Misure Compensative"],
            [1, "Synthetic", None],
        ]).to_excel(excel, index=False, header=False)
        frame, first_row = read_student_dataframe(excel, {"skip_until": "marker", "on_column": 0})
        self.assertEqual(list(frame.columns), ["Matricola", "Nome", "Misure Compensative"])
        self.assertEqual(first_row, 3)
        with self.assertRaisesRegex(ValueError, "non trovato"):
            read_student_dataframe(excel, {"skip_until": "missing"})
        empty = self.root / "empty.xlsx"
        pd.DataFrame().to_excel(empty, index=False)
        with self.assertRaisesRegex(ValueError, "intestazione"):
            read_student_dataframe(empty)

    def test_excel_header_detection_handles_generic_labels_and_preserves_configured_row(self):
        excel = self.root / "metadata.xlsx"
        for offset in (23, 123):
            with self.subTest(offset=offset):
                rows = [["Export metadata", "value", None, None, None] for _ in range(offset)]
                rows += [
                    ["Identifier", None, "Given label", "Family label", "Custom flag"],
                    [1, "unlabelled value", "Synthetic", "Student", "example"],
                ]
                pd.DataFrame(rows).to_excel(excel, index=False, header=False)
                frame, first_row = read_student_dataframe(excel)
                self.assertEqual(list(frame.columns), ["Identifier", "Unnamed: 1", "Given label", "Family label", "Custom flag"])
                self.assertEqual(first_row, offset + 2)
                frame, first_row = read_student_dataframe(excel, {"skip_rows": offset})
                self.assertEqual(first_row, offset + 2)
                self.assertEqual(len(frame), 1)


if __name__ == "__main__":
    unittest.main()
