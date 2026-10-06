from pipeline.ingest import ingest
from pipeline.quality import CRITICAL, has_critical_failures, run_checks, write_report
from pipeline.sources import KAGGLE_STATE_CRIMES
from tests.conftest import kaggle_csv


def check(con, tmp_path, write_source, *rows, header=None):
    text = kaggle_csv(*rows)
    if header:
        text = text.replace(text.splitlines()[0], header, 1)
    entry = write_source("kaggle_state_crimes", "k.csv", text)
    ingest(con, raw_dir=tmp_path, manifest=[entry])
    return {r.check: r for r in run_checks(con, [KAGGLE_STATE_CRIMES])}


CLEAN = ["0,Kerala,2001,10,5,1,20,3,40,1", "1,Kerala,2002,11,6,2,22,3,41,1"]


def test_clean_data_passes_every_check(con, tmp_path, write_source):
    results = check(con, tmp_path, write_source, *CLEAN)
    assert [name for name, r in results.items() if not r.passed] == []


def test_schema_drift_is_critical(con, tmp_path, write_source):
    results = check(con, tmp_path, write_source, *CLEAN, header=",State,Year,Rape,KA,DD,AoW,AoM,DV,WT")
    assert not results["schema_matches_contract"].passed
    assert has_critical_failures(results.values())


def test_duplicates_are_found_even_when_state_case_differs(con, tmp_path, write_source):
    results = check(con, tmp_path, write_source, *CLEAN, "2,KERALA,2002,1,1,1,1,1,1,1")
    assert results["keys_are_unique"].failing_rows == 2
    assert results["keys_are_unique"].severity == CRITICAL


def test_invalid_and_negative_numbers(con, tmp_path, write_source):
    results = check(con, tmp_path, write_source, "0,Kerala,2001,ten,5,1,20,3,40,1", "1,Kerala,2002,-4,6,2,22,3,41,1")
    assert results["numbers_are_valid"].failing_rows == 1
    assert results["counts_not_negative"].failing_rows == 1


def test_copied_year_and_sudden_jump_are_warnings(con, tmp_path, write_source):
    results = check(
        con, tmp_path, write_source,
        "0,Kerala,2001,10,5,1,20,3,40,1",
        "1,Kerala,2002,10,5,1,20,3,40,1",
        "2,Kerala,2003,10,5,1,20,3,900,1",
    )
    assert results["no_copied_years"].failing_rows == 1
    assert results["no_sudden_jumps"].failing_rows == 1
    assert not has_critical_failures(results.values())


def test_missing_years_and_empty_measures(con, tmp_path, write_source):
    results = check(
        con, tmp_path, write_source,
        "0,Kerala,2001,10,5,1,0,3,40,1", "1,Kerala,2002,11,6,2,25,3,41,1",
        "2,Goa,2001,1,1,1,0,1,1,1",
    )
    assert results["every_state_every_year"].examples[0]["missing_years"] == [2002]
    assert {"year": 2001, "column_name": "AoW"} in results["no_empty_measure_years"].examples


def test_report_is_written_in_both_formats(con, tmp_path, write_source):
    results = check(con, tmp_path, write_source, *CLEAN)
    json_path, md_path = write_report(list(results.values()), tmp_path / "reports")
    assert json_path.exists()
    assert "| kaggle_state_crimes | keys_are_unique | critical | pass |" in md_path.read_text()
