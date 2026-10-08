"""Build the website from a full pipeline run and check what a visitor would see."""

from pipeline.run import main as run_pipeline
from pipeline.site import main as build_site


def test_site_builds_with_charts_and_downloads(tmp_path):
    db, reports, out = tmp_path / "crime.duckdb", tmp_path / "reports", tmp_path / "site"
    assert run_pipeline(["--db", str(db), "--reports", str(reports)]) == 0
    assert build_site(["--db", str(db), "--reports", str(reports), "--out", str(out)]) == 0

    page = (out / "index.html").read_text()
    for view in ("all_crimes", "women"):
        for chart in ("trend-0", "ranking", "map", "heatmap", "explorer"):
            assert f'id="chart-{view}-{chart}"' in page
    assert "Unusual states (DBSCAN)." in page
    # One reset button per zoomable chart card: trends, map, heatmap and explorer, in each view.
    assert page.count('class="reset"') == 8
    for view in ("all_crimes", "women"):
        assert f'data-theme="{view}"' in page
    assert "Respect is not measured by the names we give women" in page
    assert "Other methods checked" in page
    assert page.count('class="pullquote"') == 2
    assert "sidenav" not in page
    # Data quality and the build steps appear once, in the all crimes view only.
    assert 'class="closing band only-all_crimes"' in page
    for name in ("crimes.csv", "crimes_against_women.csv", "state_clusters.csv", "national_trend.csv"):
        assert (out / "data" / name).stat().st_size > 0
