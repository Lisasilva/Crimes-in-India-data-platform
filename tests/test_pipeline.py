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


def test_women_figures_match_ncrb_published_totals(built):
    _, out = built
    with duckdb.connect(str(out / "crime.duckdb")) as con:
        national = {
            year: (cases, rate)
            for year, cases, rate in con.sql(
                "SELECT year, cases, rate_per_100k_women FROM gold.mart_national_trend "
                "WHERE crime_code = 'total_against_women'"
            ).fetchall()
        }
        # Delhi 2020 rape cases; the Kaggle copy had them under "D&N Haveli".
        delhi = con.sql(
            "SELECT cases FROM gold.fct_crimes_against_women "
            "WHERE analysis_unit = 'Delhi' AND year = 2020 AND crime_code = 'rape'"
        ).fetchone()[0]
        sources = con.sql("SELECT DISTINCT source FROM gold.fct_crimes_against_women").fetchall()
        clustered = con.sql("SELECT count(*) FROM gold.state_clusters").fetchone()[0]

    # All-India total crimes against women and rate per 100,000 women, as
    # printed by NCRB (tables 5.1 and 3A.1).
    assert national[2012] == (244270, 41.74)
    assert national[2022][0] == 445256 and round(national[2022][1], 1) == 66.4
    assert national[2024][0] == 441534 and round(national[2024][1], 1) == 64.6
    assert sorted(national) == list(range(2001, 2025))
    assert delhi == 997
    assert sources == [("ncrb_women_heads",)]
    assert clustered == 35


def test_all_crimes_match_ncrb_published_totals(built):
    _, out = built
    with duckdb.connect(str(out / "crime.duckdb")) as con:
        totals = dict(con.sql(
            "SELECT year, cases FROM gold.mart_national_crime_trend WHERE crime_group = 'total_ipc'"
        ).fetchall())
        units = con.sql(
            "SELECT count(DISTINCT analysis_unit) FROM gold.fct_crimes WHERE year = 2024"
        ).fetchone()[0]
    # All-India total cognisable IPC (and from 2024 BNS) crimes, as printed by NCRB.
    assert totals[2012] == 2387188
    assert totals[2013] == 2647722
    assert totals[2020] == 4254356
    assert totals[2024] == 3544608
    assert sorted(totals) == list(range(2001, 2025))
    assert units == 35
