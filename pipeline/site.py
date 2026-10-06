"""Build the static website from the gold layer.

Usage:
    python -m pipeline.site [--db PATH] [--reports DIR] [--out DIR]

Reads the warehouse built by pipeline.run and the reports it wrote, and writes
a single-page site (index.html) plus CSV downloads of the gold tables. GitHub
Actions publishes the output folder to GitHub Pages.
"""

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yaml
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version

from pipeline.clustering import FEATURES
from pipeline.sources import DEFAULT_DB_PATH, PROJECT_ROOT, REPORTS_DIR

SITE_DIR = PROJECT_ROOT / "site"
REPO_URL = "https://github.com/Lisasilva/Crimes-in-India-data-platform"

# Colours: two categorical slots for the two clusters, ink and grid for chrome.
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK_2, INK_3, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
FONT = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"

CRIME_LABELS = {
    "rape": "Rape",
    "kidnapping_abduction": "Kidnapping & abduction",
    "dowry_deaths": "Dowry deaths",
    "assault_on_women": "Assault on women",
    "insult_to_modesty": "Insult to modesty",
    "cruelty_by_husband": "Cruelty by husband or relatives",
}


# --- data --------------------------------------------------------------------


def load(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    crimes = ", ".join(f"'{c}'" for c in FEATURES)
    return {
        "national": con.sql(
            f"SELECT * FROM gold.mart_national_trend WHERE crime_code IN ({crimes}) ORDER BY year"
        ).df(),
        "state_year": con.sql(
            f"""
            SELECT analysis_unit, year,
                   CASE WHEN count(cases) = count(*) THEN sum(rate_per_100k_women) END AS rate
            FROM gold.fct_crimes_against_women
            WHERE crime_code IN ({crimes})
            GROUP BY ALL ORDER BY analysis_unit, year
            """
        ).df(),
        "clusters": con.sql("SELECT * FROM gold.state_clusters").df(),
        "scores": con.sql("SELECT * FROM gold.cluster_scores ORDER BY k").df(),
        "fact": con.sql("SELECT * FROM gold.fct_crimes_against_women ORDER BY analysis_unit, year, crime_code").df(),
        "states": con.sql("SELECT * FROM gold.dim_state ORDER BY analysis_unit").df(),
        "corrections": con.sql(
            """
            SELECT analysis_unit, year, crime_code, data_issue
            FROM gold.fct_crimes_against_women
            WHERE data_issue IS NOT NULL
            """
        ).df(),
        "relabelled": con.sql(
            "SELECT count(*) AS n FROM gold.fct_crimes_against_women WHERE was_relabelled"
        ).df(),
        "comparison": con.sql(
            "SELECT comparison, count(*) AS n FROM gold.mart_source_comparison GROUP BY 1"
        ).df(),
        "profile_years": con.sql(
            "SELECT min(year) AS first, max(year) AS last FROM gold.fct_crimes_against_women"
        ).df(),
    }


# --- charts ------------------------------------------------------------------


def base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=24, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=INK_2),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=INK)),
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, tickcolor=GRID, ticks="outside", zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def national_trend_charts(national: pd.DataFrame) -> list[tuple[str, go.Figure]]:
    """One small chart per crime type; the page lays them out in a responsive grid."""
    charts = []
    for crime in FEATURES:
        d = national[national["crime_code"] == crime]
        fig = go.Figure(
            go.Scatter(
                x=d["year"], y=d["rate_per_100k_women"], mode="lines+markers",
                line=dict(color=BLUE, width=2), marker=dict(size=5, color=BLUE), connectgaps=False,
                hovertemplate="%{x}: %{y:.1f} per 100,000 women<extra></extra>",
            )
        )
        base_layout(fig, 220)
        fig.update_layout(margin=dict(l=8, r=8, t=8, b=8))
        fig.update_yaxes(rangemode="tozero")
        fig.update_xaxes(dtick=5, range=[2000.5, 2021.5])
        charts.append((CRIME_LABELS[crime], fig))
    return charts


def state_ranking_chart(clusters: pd.DataFrame) -> go.Figure:
    d = clusters.assign(total=clusters[FEATURES].sum(axis=1)).sort_values("total")
    colour = d["cluster_name"].map(lambda name: ORANGE if name.startswith("Higher") else BLUE)
    label = d["analysis_unit"] + d["is_unusual"].map({True: "  ◆", False: ""})
    fig = go.Figure(
        go.Bar(
            x=d["total"], y=label, orientation="h", marker=dict(color=colour, line=dict(width=0)),
            customdata=d[["cluster_name", "analysis_unit"]],
            hovertemplate="<b>%{customdata[1]}</b><br>%{x:.1f} cases per 100,000 women a year"
                          "<br>%{customdata[0]}<extra></extra>",
        )
    )
    base_layout(fig, 820)
    fig.update_layout(bargap=0.25, margin=dict(l=8, r=16, t=8, b=8))
    fig.update_xaxes(title=dict(text="Average cases per 100,000 women a year", font=dict(size=12)), showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=INK_2))
    return fig


def cluster_map_chart(clusters: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for name, colour in (("Lower crime rates", BLUE), ("Higher crime rates", ORANGE)):
        d = clusters[clusters["cluster_name"] == name]
        fig.add_trace(
            go.Scatter(
                x=d["pca_x"], y=d["pca_y"], mode="markers", name=name,
                marker=dict(size=11, color=colour, line=dict(color=SURFACE, width=2),
                            symbol=d["is_unusual"].map({True: "diamond", False: "circle"})),
                text=d["analysis_unit"],
                hovertemplate="<b>%{text}</b><br>" + name + "<extra></extra>",
            )
        )
    unusual = clusters[clusters["is_unusual"]]
    for row in unusual.itertuples():
        fig.add_annotation(x=row.pca_x, y=row.pca_y, text=row.analysis_unit, showarrow=False,
                           yshift=16, font=dict(size=12, color=INK))
    base_layout(fig, 440)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.12, x=0, font=dict(color=INK_2)),
                      margin=dict(l=8, r=8, t=8, b=8))
    pad = (clusters["pca_y"].max() - clusters["pca_y"].min()) * 0.12
    fig.update_yaxes(range=[clusters["pca_y"].min() - pad, clusters["pca_y"].max() + pad])
    fig.update_xaxes(title=dict(text="Map dimension 1", font=dict(size=12), standoff=4), showticklabels=False)
    fig.update_yaxes(title=dict(text="Map dimension 2", font=dict(size=12)), showticklabels=False, showgrid=False)
    return fig


def state_explorer_chart(state_year: pd.DataFrame, national: pd.DataFrame) -> go.Figure:
    national_total = (
        national.groupby("year")
        .agg(rate=("rate_per_100k_women", lambda s: s.sum() if s.notna().all() else None))
        .reset_index()
    )
    states = sorted(state_year["analysis_unit"].unique())
    default = states.index("Delhi") if "Delhi" in states else 0
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=national_total["year"], y=national_total["rate"], mode="lines", name="All India",
            line=dict(color=INK_3, width=2, dash="dot"), connectgaps=False,
            hovertemplate="All India, %{x}: %{y:.1f}<extra></extra>",
        )
    )
    for i, state in enumerate(states):
        d = state_year[state_year["analysis_unit"] == state]
        fig.add_trace(
            go.Scatter(
                x=d["year"], y=d["rate"], mode="lines+markers", name=state, visible=(i == default),
                line=dict(color=BLUE, width=2), marker=dict(size=6, color=BLUE), connectgaps=False,
                hovertemplate=state + ", %{x}: %{y:.1f}<extra></extra>",
            )
        )
    buttons = [
        dict(label=state, method="update",
             args=[{"visible": [True] + [j == i for j in range(len(states))]}])
        for i, state in enumerate(states)
    ]
    base_layout(fig, 380)
    fig.update_layout(
        showlegend=True, legend=dict(orientation="h", y=1.12, x=0.32, font=dict(color=INK_2)),
        margin=dict(l=8, r=8, t=56, b=8),
        updatemenus=[dict(buttons=buttons, active=default, x=0, y=1.18, xanchor="left", yanchor="top",
                          bgcolor="white", bordercolor=GRID, font=dict(color=INK))],
    )
    fig.update_yaxes(title=dict(text="Cases per 100,000 women", font=dict(size=12)), rangemode="tozero")
    return fig


def chart_html(fig: go.Figure, div_id: str) -> str:
    return fig.to_html(
        full_html=False, include_plotlyjs=False, div_id=div_id,
        config={"displayModeBar": False, "responsive": True},
    )


# --- page --------------------------------------------------------------------


def esc(value) -> str:
    return html.escape(str(value))


def quality_rows(quality: dict) -> str:
    rows = []
    for r in quality["results"]:
        if r["passed"]:
            badge = '<span class="badge ok">Passed</span>'
        elif r["severity"] == "critical":
            badge = '<span class="badge bad">Failed</span>'
        else:
            badge = '<span class="badge warn">Warning</span>'
        rows.append(
            f"<tr><td>{esc(r['source'])}</td><td>{esc(r['check'])}</td><td>{esc(r['severity'])}</td>"
            f"<td>{badge}</td><td class='num'>{r['failing_rows']:,}</td></tr>"
        )
    return "\n".join(rows)


def build_page(data: dict[str, pd.DataFrame], quality: dict, clustering: dict) -> str:
    fact, clusters = data["fact"], data["clusters"]
    first, last = int(data["profile_years"]["first"][0]), int(data["profile_years"]["last"][0])
    total_cases = int(fact["cases"].sum())
    summary = quality["summary"]
    comparison = dict(zip(data["comparison"]["comparison"], data["comparison"]["n"]))
    corrected = int(data["relabelled"]["n"][0]) + len(data["corrections"])
    built = datetime.now(timezone.utc).strftime("%d %B %Y")
    best = clustering["best_k"]
    score_rows = "".join(
        f"<tr class='{'best' if r['k'] == best else ''}'><td>{r['k']}</td>"
        f"<td class='num'>{r['silhouette']:.3f}</td></tr>"
        for r in clustering["scores"]
    )
    cluster_lists = "".join(
        f"<p><span class='swatch' style='background:{ORANGE if name.startswith('Higher') else BLUE}'></span>"
        f"<b>{esc(name)}:</b> {esc(', '.join(sorted(group['analysis_unit'])))}</p>"
        for name, group in clusters.groupby("cluster_name")
    )

    charts = {
        "trend": "".join(
            f"<div class='mini'><h3>{esc(title)}</h3>{chart_html(fig, f'chart-trend-{i}')}</div>"
            for i, (title, fig) in enumerate(national_trend_charts(data["national"]))
        ),
        "ranking": chart_html(state_ranking_chart(clusters), "chart-ranking"),
        "map": chart_html(cluster_map_chart(clusters), "chart-map"),
        "explorer": chart_html(state_explorer_chart(data["state_year"], data["national"]), "chart-explorer"),
    }

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crimes Against Women in India</title>
<meta name="description" content="State-level crimes against women in India, {first}-{last}: cleaned NCRB data, rates per 100,000 women, and state groups found by clustering.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js" charset="utf-8"></script>
<style>
:root {{
  color-scheme: light;
  --surface: {SURFACE}; --card: #ffffff; --ink: {INK}; --ink-2: {INK_2}; --ink-3: {INK_3};
  --line: {GRID}; --blue: {BLUE}; --orange: {ORANGE};
  --ok: #0ca30c; --warn: #b07a00; --bad: #d03b3b;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--ink); font: 16px/1.6 {FONT}; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 48px 16px 64px; }}
header p.lead {{ font-size: 18px; color: var(--ink-2); max-width: 720px; }}
h1 {{ font-size: clamp(28px, 4vw, 40px); line-height: 1.15; margin: 0 0 12px; letter-spacing: -0.02em; }}
h2 {{ font-size: 24px; margin: 0 0 8px; letter-spacing: -0.01em; }}
section {{ margin-top: 56px; }}
section > p {{ color: var(--ink-2); max-width: 760px; }}
a {{ color: var(--blue); }}
.eyebrow {{ font-size: 13px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--ink-3); }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-top: 32px; }}
.tile {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }}
.tile .value {{ font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }}
.tile .label {{ font-size: 14px; color: var(--ink-2); }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; margin-top: 16px; }}
.note {{ font-size: 14px; color: var(--ink-2); }}
.minis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px 16px; }}
.mini {{ min-width: 0; }}
.mini h3 {{ font-size: 14px; font-weight: 600; margin: 4px 0 0 8px; }}
.two {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }}
@media (max-width: 760px) {{ .two {{ grid-template-columns: 1fr; }} }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
th {{ color: var(--ink-3); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.best td {{ font-weight: 700; }}
.table-wrap {{ overflow-x: auto; }}
.badge {{ display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
.badge.ok {{ color: var(--ok); background: #e9f6e9; }}
.badge.warn {{ color: var(--warn); background: #fdf3dc; }}
.badge.bad {{ color: var(--bad); background: #fbe7e7; }}
.swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 8px; }}
.flow {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: stretch; margin-top: 16px; }}
.step {{ flex: 1 1 150px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }}
.step b {{ display: block; }}
.step span {{ font-size: 13px; color: var(--ink-2); }}
details summary {{ cursor: pointer; color: var(--blue); font-weight: 500; }}
footer {{ margin-top: 64px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 14px; color: var(--ink-2); }}
</style>
</head>
<body>
<main>
<header>
  <div class="eyebrow">Data engineering project</div>
  <h1>Crimes against women in India, {first}–{last}</h1>
  <p class="lead">Official NCRB figures for every state and union territory: cleaned, checked and
  turned into rates per 100,000 women. States are then grouped by how similar their crime patterns are.
  The whole site is rebuilt from the raw files by an automated pipeline.</p>
  <div class="tiles">
    <div class="tile"><div class="value">{total_cases:,}</div><div class="label">cases recorded, {first}–{last}</div></div>
    <div class="tile"><div class="value">{len(data['states'])}</div><div class="label">states and union territories</div></div>
    <div class="tile"><div class="value">{summary['checks_passed']} / {summary['checks_run']}</div><div class="label">data quality checks passed; the rest are reported problems in the sources</div></div>
    <div class="tile"><div class="value">{corrected:,}</div><div class="label">figures corrected or set aside by reviewed rules</div></div>
  </div>
</header>

<section>
  <h2>How rates have changed across India</h2>
  <p>Cases reported per 100,000 women each year, for the six main crime types. Gaps are deliberate:
  a year is left blank when a state's figure for it is known to be wrong. For example, 2019 is blank because
  West Bengal's 2019 figures are an exact copy of 2018, and assault on women is blank for 2011 because every state reported zero.</p>
  <div class="card"><div class="minis">{charts['trend']}</div></div>
  <p class="note">Reported cases depend on whether crimes are reported and recorded, so a rising line can mean better reporting as well as more crime.
  Legal definitions also changed after the Criminal Law (Amendment) Act, 2013, which is why several lines bend around 2013–2014.</p>
</section>

<section>
  <h2>Which states have the highest rates</h2>
  <p>Average yearly cases per 100,000 women, {profile_window()}, all six crime types added together.
  Colour shows the group each state falls into (next section); ◆ marks a state whose pattern is unlike any other.</p>
  <div class="card">{charts['ranking']}</div>
</section>

<section>
  <h2>States grouped by their crime pattern</h2>
  <p>K-Means clustering compares states on all six rates at once. It tried 2 to 6 groups and kept the
  number with the best <i>silhouette score</i>, which measures how clearly the groups separate (from −1 to 1).
  DBSCAN then looked for states with too few similar neighbours.</p>
  <div class="two">
    <div class="card">{charts['map']}<p class="note">Each dot is a state, placed so that states with similar rates sit close together.
    The two map dimensions summarise the six rates (principal component analysis) and have no units.</p></div>
    <div>
      <div class="card"><table><thead><tr><th>Groups</th><th class="num">Silhouette</th></tr></thead><tbody>{score_rows}</tbody></table>
      <p class="note">A score around 0.3 means the groups are real but overlap: states sit on a spectrum rather than in tidy boxes.</p></div>
      <div class="card"><p class="note"><b>Unusual states.</b> {describe_unusual(clusters)}
      For very small territories, a handful of cases is enough to move a rate this much.</p></div>
    </div>
  </div>
  <div class="card note">{cluster_lists}</div>
</section>

<section>
  <h2>Explore a state</h2>
  <p>All six crime types together, per 100,000 women, against the all-India rate. Choose a state from the menu.</p>
  <div class="card">{charts['explorer']}</div>
</section>

<section>
  <h2>Data quality</h2>
  <p>Every run checks the raw files before anything is built. Critical checks stop the pipeline;
  warnings describe problems in the published sources, and each one is handled by a reviewed rule rather than a manual edit.</p>
  <div class="card">
    <ul class="note">
      <li><b>2020–2021 state labels were shifted.</b> From Jammu &amp; Kashmir onward, each row held the next state's figures
      (NCRB changed its state order in 2020). {int(data['relabelled']['n'][0]):,} figures were moved back to the right state.</li>
      <li><b>Copied and empty years</b> (West Bengal 2019, Jharkhand 2004, assault on women in 2011) are set to missing, never guessed.</li>
      <li><b>Delhi 2001–2010</b> was missing and is filled from a second NCRB file. Where both files have data,
      all {comparison.get('match', 0):,} figures match exactly.</li>
    </ul>
    <details><summary>Show all {summary['checks_run']} checks from the latest run</summary>
      <div class="table-wrap"><table>
        <thead><tr><th>Source</th><th>Check</th><th>Severity</th><th>Result</th><th class="num">Rows</th></tr></thead>
        <tbody>{quality_rows(quality)}</tbody>
      </table></div>
    </details>
    <p class="note">Full write-up: <a href="{REPO_URL}/blob/main/docs/data_quality.md">docs/data_quality.md</a>.</p>
  </div>
</section>

<section>
  <h2>How it is built</h2>
  <p>Everything runs in GitHub Actions on every change and once a month, using only free, open-source tools.</p>
  <div class="flow">
    <div class="step"><b>Raw files</b><span>CSV files with recorded checksums</span></div>
    <div class="step"><b>Bronze</b><span>Loaded into DuckDB as text; broken rows repaired or kept aside</span></div>
    <div class="step"><b>Checks</b><span>{summary['checks_run']} data quality checks in Python and SQL</span></div>
    <div class="step"><b>Silver &amp; gold</b><span>dbt models, reviewed corrections and dbt tests</span></div>
    <div class="step"><b>Clustering</b><span>K-Means and DBSCAN with scikit-learn</span></div>
    <div class="step"><b>This site</b><span>Plotly charts published to GitHub Pages</span></div>
  </div>
  <p class="note">Download the cleaned data:
    <a href="data/crimes_against_women.csv">crimes_against_women.csv</a> (one row per state, year and crime type),
    <a href="data/state_clusters.csv">state_clusters.csv</a>,
    <a href="data/national_trend.csv">national_trend.csv</a>.
    Code: <a href="{REPO_URL}">{REPO_URL.removeprefix('https://')}</a>.</p>
</section>

<footer>
  Source: National Crime Records Bureau (NCRB), <i>Crime in India</i>, via Kaggle copies of data.gov.in releases.
  Female population: Census of India 2011. Built {built}.
</footer>
</main>
<script>
// Charts are drawn while the page is still loading; size them to their final boxes.
window.addEventListener("load", () => {{
  document.querySelectorAll(".plotly-graph-div").forEach((div) => Plotly.Plots.resize(div));
}});
</script>
</body>
</html>
"""


def profile_window() -> str:
    """The years averaged for state profiles, as set in dbt_project.yml."""
    project = yaml.safe_load((PROJECT_ROOT / "dbt" / "dbt_project.yml").read_text())
    return f"{project['vars']['profile_start_year']}–{project['vars']['profile_end_year']}"


def describe_unusual(clusters: pd.DataFrame) -> str:
    """One sentence per unusual state: which rate sets it apart from a typical state."""
    typical = clusters[FEATURES].median()
    sentences = []
    for row in clusters[clusters["is_unusual"]].sort_values("analysis_unit").itertuples():
        ratio = pd.Series({c: getattr(row, c) for c in FEATURES}) / typical
        high, low = ratio.idxmax(), ratio.idxmin()
        sentences.append(
            f"<b>{esc(row.analysis_unit)}</b>: {esc(CRIME_LABELS[high].lower())} is {ratio[high]:.1f} times "
            f"the typical state's rate, while {esc(CRIME_LABELS[low].lower())} is "
            + ("almost zero." if ratio[low] < 0.1 else f"{ratio[low]:.1f} times.")
        )
    return " ".join(sentences) or "None in the latest run."


def export_data(data: dict[str, pd.DataFrame], out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    data["fact"].to_csv(out / "data" / "crimes_against_women.csv", index=False)
    data["clusters"].to_csv(out / "data" / "state_clusters.csv", index=False)
    data["national"].to_csv(out / "data" / "national_trend.csv", index=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--out", type=Path, default=SITE_DIR)
    args = parser.parse_args(argv)

    with duckdb.connect(str(args.db)) as con:
        data = load(con)
    quality = json.loads((args.reports / "data_quality_report.json").read_text())
    clustering = json.loads((args.reports / "clustering_report.json").read_text())

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(build_page(data, quality, clustering))
    (args.out / ".nojekyll").write_text("")
    export_data(data, args.out)
    print(f"Site written to {args.out / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
