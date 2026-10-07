"""Build the static website from the gold layer.

Usage:
    python -m pipeline.site [--db PATH] [--reports DIR] [--out DIR]

Reads the warehouse built by pipeline.run and the reports it wrote, and writes
a single-page site (index.html) plus CSV downloads of the gold tables. The page
has two views behind a switch: all IPC/BNS crimes, and crimes against women.
GitHub Actions publishes the output folder to GitHub Pages.
"""

import argparse
import html
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yaml
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version

from pipeline.clustering import VIEWS
from pipeline.sources import DEFAULT_DB_PATH, PROJECT_ROOT, REPORTS_DIR

SITE_DIR = PROJECT_ROOT / "site"
REPO_URL = "https://github.com/Lisasilva/Crimes-in-India-data-platform"

# Colours: two categorical slots for the two clusters, ink and grid for chrome.
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK_2, INK_3, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"
FONT = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"

CRIME_LABELS = {
    "murder": "Murder",
    "attempt_to_murder": "Attempt to murder",
    "rape": "Rape",
    "kidnapping_abduction": "Kidnapping & abduction",
    "robbery": "Robbery",
    "burglary": "Burglary",
    "theft": "Theft",
    "riots": "Riots",
    "cheating": "Cheating",
    "hurt": "Hurt",
    "causing_death_by_negligence": "Death by negligence",
    "dowry_deaths": "Dowry deaths",
    "assault_on_women": "Assault on women",
    "insult_to_modesty": "Insult to modesty",
    "cruelty_by_husband": "Cruelty by husband or relatives",
}


@dataclass
class View:
    """One of the two views of the page, with the data its charts need."""

    key: str
    tab: str
    features: list[str]
    per: str                  # "people" or "women"
    total_label: str          # the line drawn in the state explorer
    national: pd.DataFrame    # year, crime, rate
    state_year: pd.DataFrame  # analysis_unit, year, rate (the total)
    clusters: pd.DataFrame
    clustering: dict

    @property
    def unit(self) -> str:
        return f"per 100,000 {self.per}"


# --- data --------------------------------------------------------------------


def load(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    return {
        "national_all": con.sql(
            """
            SELECT year, crime_group AS crime, cases, rate_per_100k AS rate
            FROM gold.mart_national_crime_trend ORDER BY year
            """
        ).df(),
        "national_women": con.sql(
            """
            SELECT year, crime_code AS crime, cases, rate_per_100k_women AS rate
            FROM gold.mart_national_trend ORDER BY year
            """
        ).df(),
        "state_year_all": con.sql(
            """
            SELECT analysis_unit, year, rate_per_100k AS rate
            FROM gold.fct_crimes WHERE crime_group = 'total_ipc' ORDER BY analysis_unit, year
            """
        ).df(),
        "state_year_women": con.sql(
            """
            SELECT analysis_unit, year, rate_per_100k_women AS rate
            FROM gold.fct_crimes_against_women WHERE crime_code = 'total_against_women'
            ORDER BY analysis_unit, year
            """
        ).df(),
        "clusters": con.sql("SELECT * FROM gold.state_clusters").df(),
        "fact_all": con.sql("SELECT * FROM gold.fct_crimes ORDER BY analysis_unit, year, crime_group").df(),
        "fact_women": con.sql(
            "SELECT * FROM gold.fct_crimes_against_women ORDER BY analysis_unit, year, crime_code"
        ).df(),
        "states": con.sql("SELECT * FROM gold.dim_state ORDER BY analysis_unit").df(),
        "overlap": con.sql(
            """
            SELECT comparison_type, comparison, count(*) AS n
            FROM gold.mart_crime_source_overlap GROUP BY ALL
            """
        ).df(),
    }


def build_views(data: dict[str, pd.DataFrame], clustering: dict) -> list[View]:
    views = []
    for key, tab, per, total_label, suffix in (
        ("all_crimes", "All crimes", "people", "All IPC/BNS crimes", "all"),
        ("women", "Crimes against women", "women", "All crimes against women", "women"),
    ):
        features = VIEWS[key][1]
        clusters = data["clusters"][data["clusters"]["view"] == key].dropna(axis=1, how="all")
        views.append(View(
            key=key, tab=tab, features=features, per=per, total_label=total_label,
            national=data[f"national_{suffix}"], state_year=data[f"state_year_{suffix}"],
            clusters=clusters.reset_index(drop=True), clustering=clustering[key],
        ))
    return views


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


def national_trend_charts(view: View) -> list[tuple[str, go.Figure]]:
    """One small chart per crime; the page lays them out in a responsive grid."""
    first, last = int(view.national["year"].min()), int(view.national["year"].max())
    charts = []
    for crime in view.features:
        d = view.national[view.national["crime"] == crime]
        fig = go.Figure(
            go.Scatter(
                x=d["year"], y=d["rate"], mode="lines+markers",
                line=dict(color=BLUE, width=2), marker=dict(size=5, color=BLUE), connectgaps=False,
                hovertemplate="%{x}: %{y:.1f} " + view.unit + "<extra></extra>",
            )
        )
        base_layout(fig, 220)
        fig.update_layout(margin=dict(l=8, r=8, t=8, b=8))
        fig.update_yaxes(rangemode="tozero")
        fig.update_xaxes(dtick=5, range=[first - 0.5, last + 0.5])
        charts.append((CRIME_LABELS[crime], fig))
    return charts


def state_ranking_chart(view: View) -> go.Figure:
    clusters = view.clusters
    d = clusters.assign(total=clusters[view.features].sum(axis=1)).sort_values("total")
    colour = d["cluster_name"].map(lambda name: ORANGE if name.startswith("Higher") else BLUE)
    label = d["analysis_unit"] + d["is_unusual"].map({True: "  ◆", False: ""})
    fig = go.Figure(
        go.Bar(
            x=d["total"], y=label, orientation="h", marker=dict(color=colour, line=dict(width=0)),
            customdata=d[["cluster_name", "analysis_unit"]],
            hovertemplate="<b>%{customdata[1]}</b><br>%{x:.1f} cases " + view.unit + " a year"
                          "<br>%{customdata[0]}<extra></extra>",
        )
    )
    base_layout(fig, 820)
    fig.update_layout(bargap=0.25, margin=dict(l=8, r=16, t=8, b=8))
    fig.update_xaxes(title=dict(text=f"Average cases {view.unit} a year", font=dict(size=12)),
                     showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=INK_2))
    return fig


def cluster_map_chart(view: View) -> go.Figure:
    clusters = view.clusters
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
    for row in clusters[clusters["is_unusual"]].itertuples():
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


def state_explorer_chart(view: View) -> go.Figure:
    total = "total_ipc" if view.key == "all_crimes" else "total_against_women"
    national_total = view.national[view.national["crime"] == total]
    state_year = view.state_year
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
    fig.update_yaxes(title=dict(text=f"Cases {view.unit}", font=dict(size=12)), rangemode="tozero")
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


VIEW_TEXT = {
    "all_crimes": {
        "trend": "Cases reported per 100,000 people each year for eleven large crime groups under the "
                 "Indian Penal Code, and from July 2024 the Bharatiya Nyaya Sanhita that replaced it.",
        "trend_note": "NCRB regrouped its crime heads in 2014, 2017 and 2024, and each group is mapped across "
                      "those changes in a reviewed table. Hurt has no 2024 point: under the new law NCRB's "
                      "grievous hurt group also counts some simple hurt, so it is not comparable.",
        "ranking": "the eleven crime groups added together",
    },
    "women": {
        "trend": "Cases reported per 100,000 women each year, for the six main crimes against women.",
        "trend_note": "Legal definitions changed with the Criminal Law (Amendment) Act, 2013, which is why "
                      "several lines bend around 2013–2014. Rates before 2012 use an estimated female "
                      "population (see Data quality).",
        "ranking": "the six crime types added together",
    },
}


def silhouette_note(score: float) -> str:
    if score >= 0.25:
        return (f"A score of {score:.2f} means the groups are real but overlap: states sit on a "
                "spectrum rather than in tidy boxes.")
    return (f"A score of {score:.2f} is weak: the two groups are a rough split of a spectrum, "
            "not clearly separate types of state.")


def view_section(view: View) -> str:
    text = VIEW_TEXT[view.key]
    c = view.clustering
    best = c["best_k"]
    score_rows = "".join(
        f"<tr class='{'best' if r['k'] == best else ''}'><td>{r['k']}</td>"
        f"<td class='num'>{r['silhouette']:.3f}</td></tr>"
        for r in c["scores"]
    )
    cluster_lists = "".join(
        f"<p><span class='swatch' style='background:{ORANGE if name.startswith('Higher') else BLUE}'></span>"
        f"<b>{esc(name)}:</b> {esc(', '.join(sorted(group['analysis_unit'])))}</p>"
        for name, group in view.clusters.groupby("cluster_name")
    )
    k = view.key
    trend = "".join(
        f"<div class='mini'><h3>{esc(title)}</h3>{chart_html(fig, f'chart-{k}-trend-{i}')}</div>"
        for i, (title, fig) in enumerate(national_trend_charts(view))
    )
    n = len(view.features)
    hidden = "" if k == "all_crimes" else " hidden"
    return f"""<div class="view" id="view-{k}" role="tabpanel"{hidden}>
<section>
  <h2>How rates have changed across India</h2>
  <p>{esc(text['trend'])}</p>
  <div class="card"><div class="minis">{trend}</div></div>
  <p class="note">Reported cases depend on whether crimes are reported and recorded, so a rising line can mean
  better reporting as well as more crime. {esc(text['trend_note'])}</p>
</section>

<section>
  <h2>Which states have the highest rates</h2>
  <p>Average yearly cases {view.unit}, {profile_window()}, {esc(text['ranking'])}.
  Colour shows the group each state falls into (next section); ◆ marks a state whose pattern is unlike any other.</p>
  <div class="card">{chart_html(state_ranking_chart(view), f'chart-{k}-ranking')}</div>
</section>

<section>
  <h2>States grouped by their crime pattern</h2>
  <p>K-Means clustering compares states on all {n} rates at once. It tried 2 to 6 groups and kept the
  number with the best <i>silhouette score</i>, which measures how clearly the groups separate (from −1 to 1).
  DBSCAN then looked for states with too few similar neighbours.</p>
  <div class="two">
    <div class="card">{chart_html(cluster_map_chart(view), f'chart-{k}-map')}<p class="note">Each dot is a state, placed so
    that states with similar rates sit close together. The two map dimensions summarise the {n} rates
    (principal component analysis) and have no units.</p></div>
    <div>
      <div class="card"><table><thead><tr><th>Groups</th><th class="num">Silhouette</th></tr></thead><tbody>{score_rows}</tbody></table>
      <p class="note">{esc(silhouette_note(c['best_silhouette']))}</p></div>
      <div class="card"><p class="note"><b>Unusual states.</b> {describe_unusual(view)}
      For very small territories, a handful of cases is enough to move a rate this much.</p></div>
    </div>
  </div>
  <div class="card note">{cluster_lists}</div>
</section>

<section>
  <h2>Explore a state</h2>
  <p>{esc(view.total_label)} {view.unit}, against the all-India rate. Choose a state from the menu.</p>
  <div class="card">{chart_html(state_explorer_chart(view), f'chart-{k}-explorer')}</div>
</section>
</div>"""


def build_page(data: dict[str, pd.DataFrame], quality: dict, clustering: dict) -> str:
    views = build_views(data, clustering)
    fact_all, fact_women = data["fact_all"], data["fact_women"]
    first, last = int(fact_all["year"].min()), int(fact_all["year"].max())
    all_cases = int(fact_all.loc[fact_all["crime_group"] == "total_ipc", "cases"].sum())
    women_cases = int(fact_women.loc[fact_women["crime_code"] == "total_against_women", "cases"].sum())
    summary = quality["summary"]
    overlap = {(r.comparison_type, r.comparison): int(r.n) for r in data["overlap"].itertuples()}
    cross_checked = overlap.get(("NCRB women tables vs NCRB crime-head tables", "match"), 0)
    kaggle_match = overlap.get(("Kaggle women file vs NCRB women tables", "match"), 0)
    kaggle_diff = overlap.get(("Kaggle women file vs NCRB women tables", "different"), 0)
    built = datetime.now(timezone.utc).strftime("%d %B %Y")
    tabs = "".join(
        f'<button type="button" role="tab" id="tab-{v.key}" aria-controls="view-{v.key}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{esc(v.tab)}</button>'
        for i, v in enumerate(views)
    )
    sections = "\n".join(view_section(v) for v in views)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crime in India, {first}–{last}</title>
<meta name="description" content="State-level crime in India, {first}-{last}, from official NCRB tables: all IPC/BNS crimes and crimes against women, as rates, with states grouped by clustering.">
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
.switch {{ display: inline-flex; gap: 4px; margin-top: 28px; padding: 4px; background: var(--card); border: 1px solid var(--line); border-radius: 999px; }}
.switch button {{ font: 600 14px/1 {FONT}; color: var(--ink-2); background: none; border: 0; border-radius: 999px; padding: 10px 16px; cursor: pointer; }}
.switch button[aria-selected="true"] {{ background: var(--ink); color: #fff; }}
.view[hidden] {{ display: none; }}
footer {{ margin-top: 64px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 14px; color: var(--ink-2); }}
</style>
</head>
<body>
<main>
<header>
  <div class="eyebrow">Data engineering project</div>
  <h1>Crime in India, {first}–{last}</h1>
  <p class="lead">Official NCRB figures for every state and union territory: checked, mapped across changes in
  the law, and turned into rates using the population NCRB itself used. States are then grouped by how similar
  their crime patterns are. The whole site is rebuilt from the raw files by an automated pipeline.</p>
  <div class="tiles">
    <div class="tile"><div class="value">{all_cases:,}</div><div class="label">IPC/BNS crimes recorded, {first}–{last}</div></div>
    <div class="tile"><div class="value">{women_cases:,}</div><div class="label">crimes against women recorded</div></div>
    <div class="tile"><div class="value">{len(data['states'])}</div><div class="label">states and union territories</div></div>
    <div class="tile"><div class="value">{summary['checks_passed']} / {summary['checks_run']}</div><div class="label">data quality checks passed; the rest are reported problems in the sources</div></div>
  </div>
  <div class="switch" role="tablist" aria-label="Choose a view">{tabs}</div>
</header>

{sections}

<section>
  <h2>Data quality</h2>
  <p>Every run checks the raw files before anything is built. Critical checks stop the pipeline;
  warnings describe problems in the published sources, and each one is handled by a reviewed rule rather than a manual edit.</p>
  <div class="card">
    <ul class="note">
      <li><b>Official tables only.</b> Every figure comes from NCRB's <i>Crime in India</i> tables, downloaded from
      one fixed version with a checksum for each file, and every state adds up to NCRB's all-India row.</li>
      <li><b>Two NCRB tables agree.</b> The crimes-against-women tables and the all-crimes tables report
      {cross_checked:,} of the same figures, and all of them match.</li>
      <li><b>Rates match NCRB's.</b> Rates use the population NCRB used, and reproduce NCRB's printed rates.
      NCRB did not publish the female population before 2012, so for 2001–2011 it is estimated from
      NCRB's total population and each state's female share in the 2011 Census.</li>
      <li><b>The Kaggle copy is a backup only.</b> Compared with NCRB's tables, {kaggle_match:,} of its figures match
      and {kaggle_diff:,} differ, so it is not used for the charts.</li>
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
    <div class="step"><b>Raw files</b><span>NCRB tables, downloaded and verified by checksum</span></div>
    <div class="step"><b>Bronze</b><span>Loaded into DuckDB as text, one row per table cell</span></div>
    <div class="step"><b>Checks</b><span>{summary['checks_run']} data quality checks in Python and SQL</span></div>
    <div class="step"><b>Silver &amp; gold</b><span>dbt models, reviewed mappings and dbt tests</span></div>
    <div class="step"><b>Clustering</b><span>K-Means and DBSCAN with scikit-learn</span></div>
    <div class="step"><b>This site</b><span>Plotly charts published to GitHub Pages</span></div>
  </div>
  <p class="note">Download the data:
    <a href="data/crimes.csv">crimes.csv</a> (all crime groups, one row per state, year and group),
    <a href="data/crimes_against_women.csv">crimes_against_women.csv</a>,
    <a href="data/state_clusters.csv">state_clusters.csv</a>,
    <a href="data/national_trend.csv">national_trend.csv</a>.
    Code: <a href="{REPO_URL}">{REPO_URL.removeprefix('https://')}</a>.</p>
</section>

<footer>
  Source: National Crime Records Bureau (NCRB), <i>Crime in India</i> 2001–2024, as extracted by the
  <a href="https://github.com/reclaimchennai/NCRB">reclaimchennai/NCRB</a> project, and NCRB's district-wise
  files on data.gov.in. Population: NCRB (Registrar General of India projections); Census of India 2011
  for the female share before 2012. Built {built}.
</footer>
</main>
<script>
// The view switch: show one view, and size its charts to their boxes.
const tabs = document.querySelectorAll(".switch button");
tabs.forEach((tab) => tab.addEventListener("click", () => {{
  tabs.forEach((t) => {{
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }});
  document.querySelectorAll("#" + tab.getAttribute("aria-controls") + " .plotly-graph-div")
    .forEach((div) => Plotly.Plots.resize(div));
}}));
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


def describe_unusual(view: View) -> str:
    """One sentence per unusual state: which rate sets it apart from a typical state."""
    clusters, features = view.clusters, view.features
    typical = clusters[features].median()
    sentences = []
    for row in clusters[clusters["is_unusual"]].sort_values("analysis_unit").itertuples():
        ratio = pd.Series({c: getattr(row, c) for c in features}) / typical
        high, low = ratio.idxmax(), ratio.idxmin()
        sentences.append(
            f"<b>{esc(row.analysis_unit)}</b>: {esc(CRIME_LABELS[high].lower())} is {ratio[high]:.1f} times "
            f"the typical state's rate, while {esc(CRIME_LABELS[low].lower())} is "
            + ("almost zero." if ratio[low] < 0.1 else f"{ratio[low]:.1f} times.")
        )
    return " ".join(sentences) or "None in the latest run."


def export_data(data: dict[str, pd.DataFrame], out: Path) -> None:
    (out / "data").mkdir(parents=True, exist_ok=True)
    data["fact_all"].to_csv(out / "data" / "crimes.csv", index=False)
    data["fact_women"].to_csv(out / "data" / "crimes_against_women.csv", index=False)
    data["clusters"].to_csv(out / "data" / "state_clusters.csv", index=False)
    pd.concat([
        data["national_all"].assign(view="all_crimes"),
        data["national_women"].assign(view="women"),
    ]).to_csv(out / "data" / "national_trend.csv", index=False)


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
