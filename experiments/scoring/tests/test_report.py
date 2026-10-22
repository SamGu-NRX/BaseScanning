"""The markdown summary keeps its shape for any identifier, and the scorer never writes over an
input. Identifiers may be any non-empty string, so the report escapes them where they land."""

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from helpers import FIXTURES

from scoring.cli import main
from scoring.report import CSV_NAMES, _cell, _code, _table, _text

RULES = FIXTURES / "rules.json"
TRUTH = FIXTURES / "truth" / "synthetic-01.json"
RESULTS = [FIXTURES / "results" / f"{name}.json" for name in ("ar-taps", "photo-depth")]

# Each quoted fixture id and the hostile id that replaces it: pipes, line breaks, backticks and
# heading marks in the pipeline, house, candidate, check and measurement ids. Threshold names are
# already restricted to lower_snake_case by the rules loader.
RENAMES = {
    '"ar-taps"': "ar|taps\n`x`",
    '"synthetic-01"': "house\n# injected",
    '"c2"': "c|2",
    '"facing_gap"': "facing`gap`",
    '"c2-facing"': "c2\r\nfacing",
}
UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "rendered"),
        [
            ("plain", "`plain`"),
            ("a`b", "``a`b``"),
            ("a``b`c", "```a``b`c```"),
            ("`edge", "`` `edge ``"),
            ("edge`", "`` edge` ``"),
            (" both ", "`  both  `"),
            ("   ", "`   `"),
            ("line\nbreak\r\nand\rreturn", "`line break and return`"),
        ],
    )
    def test_code_span(self, value: str, rendered: str):
        assert _code(value) == rendered

    def test_text_escapes_markdown_and_joins_lines(self):
        assert _text("a|b\n# c_d*[e]<f>`g`\\") == r"a\|b \# c\_d\*\[e\]\<f\>\`g\`\\"

    def test_cell_escapes_pipes_even_in_code(self):
        assert _cell(_code("a|b")) == r"`a\|b`"

    def test_table_rows_keep_the_header_width(self):
        lines = _table(["A", "B"], [[_code("x|y|z"), "1"], [_code("p\nq"), ""]])
        assert [len(UNESCAPED_PIPE.findall(line)) for line in lines] == [3, 3, 3, 3]
        assert lines[3] == "| `p q` | n/a |"


def hostile_inputs(root: Path) -> tuple[Path, Path, list[Path]]:
    """Copies of the fixtures with every id in RENAMES replaced, and the rules hash updated."""

    def rename(text: str) -> str:
        for quoted, new in RENAMES.items():
            text = text.replace(quoted, json.dumps(new))
        return text

    rules = root / "rules.json"
    rules.write_text(rename(RULES.read_text()))
    old_sha = hashlib.sha256(RULES.read_bytes()).hexdigest()
    new_sha = hashlib.sha256(rules.read_bytes()).hexdigest()
    truth = root / "truth.json"
    truth.write_text(rename(TRUTH.read_text()))
    results = []
    for source in RESULTS:
        path = root / source.name
        path.write_text(rename(source.read_text()).replace(old_sha, new_sha))
        results.append(path)
    return rules, truth, results


def test_summary_keeps_its_shape_with_hostile_ids(tmp_path: Path, capsys):
    rules, truth, results = hostile_inputs(tmp_path)
    args = ["--rules", str(rules), "--truth", str(truth), "--results", *map(str, results)]
    assert main([*args, "--out", str(tmp_path / "out")]) == 0
    lines = capsys.readouterr().out.splitlines()

    # Every table row has as many cell borders as its header.
    tables: list[list[str]] = []
    for index, line in enumerate(lines):
        if line.startswith("|") and not lines[index - 1].startswith("|"):
            tables.append([])
        if line.startswith("|"):
            tables[-1].append(line)
    assert len(tables) == 3
    for table in tables:
        widths = {len(UNESCAPED_PIPE.findall(row)) for row in table}
        assert len(widths) == 1, table

    # No id started a line of its own, so no heading, row or list item was split or injected.
    starts = ("#", "|", "- ", "Rules: ", "Captures: ", "Each ", "These ")
    prose = [line for line in lines if line and not line.startswith(starts)]
    assert all(" " in line and not line.startswith(("`", "facing", "injected")) for line in prose)
    assert r"## House house \# injected" in lines

    table_text = "\n".join(row for table in tables for row in table)
    assert r"| `` ar\|taps `x` `` |" in table_text  # a value ending in a backtick is padded
    listed = "\n".join(line for line in lines if line.startswith("- "))
    assert "- `` ar|taps `x` `` passed `` facing`gap` `` at `c|2`;" in listed
    assert "with measurement `c2 facing` missing" in listed


class TestScorerNeverOverwritesAnInput:
    ROLES = ("rules", "survey", "results")

    def inputs(self, root: Path) -> dict[str, Path]:
        paths = {"rules": root / "rules.json", "survey": root / "truth.json"}
        paths["results"] = root / "ar-taps.json"
        paths["rules"].write_bytes(RULES.read_bytes())
        paths["survey"].write_bytes(TRUTH.read_bytes())
        paths["results"].write_bytes(RESULTS[0].read_bytes())
        return paths

    def run(self, paths: dict[str, Path], out: Path) -> int:
        args = ["--rules", str(paths["rules"]), "--truth", str(paths["survey"])]
        return main([*args, "--results", str(paths["results"]), "--out", str(out)])

    def check_refused(self, paths: dict[str, Path], out: Path, role: str, capsys) -> None:
        before = {name: path.read_bytes() for name, path in paths.items()}
        assert self.run(paths, out) == 2
        captured = capsys.readouterr()
        assert f" is the {role} " in captured.err
        assert "would overwrite an input" in captured.err
        assert captured.out == ""  # refused before the summary was printed
        assert {name: path.read_bytes() for name, path in paths.items()} == before

    @pytest.mark.parametrize("name", CSV_NAMES)
    @pytest.mark.parametrize("role", ROLES)
    def test_input_named_as_an_output(self, tmp_path: Path, role: str, name: str, capsys):
        out = tmp_path / "out"
        out.mkdir()
        paths = self.inputs(tmp_path)
        moved = out / name
        paths[role].rename(moved)
        paths[role] = moved
        self.check_refused(paths, out, role, capsys)

    @pytest.mark.parametrize("role", ROLES)
    def test_other_spelling_of_the_output_directory(self, tmp_path: Path, role: str, capsys):
        out = tmp_path / "out"
        (out / "sub").mkdir(parents=True)
        paths = self.inputs(tmp_path)
        moved = out / "runs.csv"
        paths[role].rename(moved)
        paths[role] = moved
        self.check_refused(paths, out / "sub" / "..", role, capsys)

    @pytest.mark.parametrize("name", CSV_NAMES)
    @pytest.mark.parametrize("role", ROLES)
    def test_symlink(self, tmp_path: Path, role: str, name: str, capsys):
        out = tmp_path / "out"
        out.mkdir()
        paths = self.inputs(tmp_path)
        (out / name).symlink_to(paths[role])
        self.check_refused(paths, out, role, capsys)

    @pytest.mark.parametrize("name", CSV_NAMES)
    @pytest.mark.parametrize("role", ROLES)
    def test_hard_link(self, tmp_path: Path, role: str, name: str, capsys):
        out = tmp_path / "out"
        out.mkdir()
        paths = self.inputs(tmp_path)
        os.link(paths[role], out / name)
        self.check_refused(paths, out, role, capsys)

    def test_every_survey_and_results_file_is_checked(self, tmp_path: Path, capsys):
        out = tmp_path / "out"
        out.mkdir()
        paths = self.inputs(tmp_path)
        second = out / "checks.csv"
        second.write_bytes(RESULTS[1].read_bytes())
        before = second.read_bytes()
        args = ["--rules", str(paths["rules"]), "--truth", str(paths["survey"]), "--results"]
        assert main([*args, str(paths["results"]), str(second), "--out", str(out)]) == 2
        assert " is the results " in capsys.readouterr().err
        assert second.read_bytes() == before

    def test_separate_output_directory_still_writes(self, tmp_path: Path, capsys):
        paths = self.inputs(tmp_path)
        out = tmp_path / "out"
        assert self.run(paths, out) == 0
        assert sorted(path.name for path in out.iterdir()) == sorted(CSV_NAMES)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("measurements.csv", "checks.csv"),
        ("measurements.csv", "runs.csv"),
        ("checks.csv", "runs.csv"),
    ],
)
@pytest.mark.parametrize("link_kind", ["symlink", "dangling_symlink", "hardlink"])
def test_cli_refuses_aliased_csv_destinations(tmp_path: Path, capsys, first, second, link_kind):
    out = tmp_path / "out"
    out.mkdir()
    target = out / first
    if link_kind != "dangling_symlink":
        target.write_text("previous report\n")
    if link_kind == "hardlink":
        os.link(target, out / second)
    else:
        (out / second).symlink_to(first)
    before = {path.name: path.read_bytes() for path in out.iterdir() if path.exists()}

    assert (
        main(
            [
                "--rules",
                str(RULES),
                "--truth",
                str(TRUTH),
                "--results",
                str(RESULTS[0]),
                "--out",
                str(out),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert "CSV output paths refer to the same file" in captured.err
    assert first in captured.err and second in captured.err
    assert captured.out == ""
    assert {path.name: path.read_bytes() for path in out.iterdir() if path.exists()} == before
