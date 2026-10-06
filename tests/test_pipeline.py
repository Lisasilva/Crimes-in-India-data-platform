"""End-to-end run on the real raw files in data/raw: bronze, checks, dbt, clustering."""

import json

import duckdb
import pytest

from pipeline.run import main


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    exit_code = main(["--db", str(out / "crime.duckdb"), "--reports", str(out)])
    return exit_code, out


def test_pipeline_succeeds(built):
    exit_code, out = built
    assert exit_code == 0
    report = json.loads((out / "data_quality_report.json").read_text())
    assert report["summary"]["critical_failures"] == 0


def test_known_source_problems_are_still_detected(built):
    _, out = built
    report = json.loads((out / "data_quality_report.json").read_text())
    by_check = {(r["source"], r["check"]): r for r in report["results"]}
    assert not by_check[("kaggle_state_crimes", "no_copied_years")]["passed"]
    assert not by_check[("kaggle_state_crimes", "no_empty_measure_years")]["passed"]
    assert not by_check[("ncrb_cases_2001_2010", "totals_match_parts")]["passed"]
    assert not by_check[("ncrb_cases_2001_2010", "labels_consistent")]["passed"]


def test_gold_layer_has_corrected_figures(built):
    _, out = built
    with duckdb.connect(str(out / "crime.duckdb")) as con:
        # Delhi 2020 rape cases (997) were in the row labelled "D&N Haveli".
        delhi = con.sql(
            "SELECT cases FROM gold.fct_crimes_against_women "
            "WHERE analysis_unit = 'Delhi' AND year = 2020 AND crime_code = 'rape'"
        ).fetchone()[0]
        # Delhi 2001-2010 comes from the NCRB file.
        delhi_2005 = con.sql(
            "SELECT source FROM gold.fct_crimes_against_women "
            "WHERE analysis_unit = 'Delhi' AND year = 2005 AND crime_code = 'rape'"
        ).fetchone()[0]
        copied = con.sql(
            "SELECT count(*) FROM gold.fct_crimes_against_women "
            "WHERE analysis_unit = 'West Bengal' AND year = 2019 AND cases IS NOT NULL"
        ).fetchone()[0]
        clustered = con.sql("SELECT count(*) FROM gold.state_clusters").fetchone()[0]

    assert delhi == 997
    assert delhi_2005 == "ncrb_2001_2010"
    assert copied == 0
    assert clustered == 35
