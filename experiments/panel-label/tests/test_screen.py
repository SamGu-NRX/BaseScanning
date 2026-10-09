import csv

import pytest

from panel_eval import screen
from panel_eval.screen import SCREENED, tally


def row(dup="", m="0", s="0", a="0", **extra):
    return {"duplicate_of": dup, "manufacturer": m, "model": s, "amperage": a, **extra}


def write_screened(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "duplicate_of", "manufacturer", "model", "amperage"])
        writer.writerows(rows)


def test_tally_accepts_the_yes_no_spellings_a_hand_maintained_file_uses():
    rows = [row(m=" 1"), row(s="Yes"), row(a="true"), row(m="Y"), row(m="1", a="T")]
    assert tally(rows)["any field legible"] == 5


def test_tally_rejects_a_flag_value_it_cannot_read():
    with pytest.raises(ValueError, match="manufacturer"):
        tally([row(m="maybe")])


def test_tally_does_not_lose_a_unique_row_to_a_blank_duplicate_of():
    rows = [row(), row(dup=" ", m="1"), row(dup="", a="1")]
    counts = tally(rows)
    assert counts["examined"] == 3
    assert counts["any field legible"] == 2


def test_tally_raises_when_a_short_row_loses_its_flags():
    short = {"id": "p05", "duplicate_of": "", "manufacturer": None, "model": None, "amperage": None}
    with pytest.raises(ValueError, match="p05"):
        tally([row(), short])


def test_tally_names_the_missing_column_when_the_header_lacks_it():
    no_flag_column = {"duplicate_of": "", "model": "1", "amperage": "1"}
    with pytest.raises(ValueError, match="manufacturer"):
        tally([no_flag_column])


def test_tally_raises_when_duplicate_of_cites_an_id_not_in_the_file():
    rows = [row(id="p01"), row(dup="p99", id="p02")]
    with pytest.raises(ValueError, match="p99"):
        tally(rows)


def test_tally_raises_when_a_row_cites_itself_as_its_own_duplicate():
    with pytest.raises(ValueError, match="itself"):
        tally([row(id="p01", dup="p01"), row()])


def test_tally_trims_whitespace_around_ids_and_references():
    counts = tally([row(id=" p01 ", m="1"), row(id="p02", dup=" p01 ", m="1")])
    assert counts["examined"] == 1


def test_tally_still_dedupes_chains_and_counts_the_canonical_once():
    rows = [
        row(id="p01", m="1", s="1", a="1"),
        row(id="p02", dup="p01", m="1", s="1", a="1"),
        row(id="p03", dup="p02", m="1", s="1", a="1"),  # a repost of a repost
        row(id="p04", m="1"),
    ]
    assert tally(rows) == {
        "examined": 2,
        "any field legible": 2,
        "manufacturer and (model or amperage)": 1,
        "all three": 1,
    }


def test_tally_of_no_rows_is_all_zero():
    assert tally([]) == {
        "examined": 0,
        "any field legible": 0,
        "manufacturer and (model or amperage)": 0,
        "all three": 0,
    }


def test_tally_counts_the_committed_screened_csv_exactly():
    with SCREENED.open(newline="") as handle:
        assert tally(csv.DictReader(handle)) == {
            "examined": 67,
            "any field legible": 19,
            "manufacturer and (model or amperage)": 9,
            "all three": 6,
        }


def test_main_prints_the_counts_and_the_verdict(tmp_path, monkeypatch, capsys):
    target = tmp_path / "screened.csv"
    write_screened(
        target,
        [["p01", "", "1", "0", "1"], ["p02", "p01", "1", "1", "1"], ["p03", "", "0", "0", "0"]],
    )
    monkeypatch.setattr(screen, "SCREENED", target)
    screen.main()
    out = capsys.readouterr().out
    assert "examined: 2" in out
    assert "any field legible: 1" in out
    assert "manufacturer and (model or amperage): 1" in out
    assert "all three: 0" in out
    assert "is short of" in out


def test_main_says_meets_only_at_the_40_photo_bar(tmp_path, monkeypatch, capsys):
    target = tmp_path / "screened.csv"
    write_screened(target, [[f"p{i:02}", "", "1", "0", "0"] for i in range(40)])
    monkeypatch.setattr(screen, "SCREENED", target)
    screen.main()
    assert "meets" in capsys.readouterr().out

    write_screened(target, [[f"p{i:02}", "", "1", "0", "0"] for i in range(39)])
    screen.main()
    assert "is short of" in capsys.readouterr().out


def test_main_reports_a_missing_screened_file_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "SCREENED", tmp_path / "absent.csv")
    with pytest.raises(SystemExit, match="absent.csv"):
        screen.main()
