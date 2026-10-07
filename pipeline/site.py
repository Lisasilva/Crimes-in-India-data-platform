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
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version

from pipeline.clustering import VIEWS
from pipeline.sources import DEFAULT_DB_PATH, PROJECT_ROOT, REPORTS_DIR

SITE_DIR = PROJECT_ROOT / "site"
REPO_URL = "https://github.com/Lisasilva/Crimes-in-India-data-platform"

# Two themes, one per view.
# Midnight Vigil (all crimes): a nation under watch.
NAVY, AMBER, SLATE, IVORY = "#0B132B", "#F4A261", "#495057", "#F8F9FA"
# Broken Pedestal (crimes against women): dignity, resilience, truth and hope.
PURPLE, TEAL, GOLD, CHARCOAL = "#5B3A9A", "#2A9D8F", "#C8A24D", "#2B2D42"
INK_3, GRID = "#868e96", "#e3e5e8"
FONT = "'IBM Plex Sans', system-ui, -apple-system, Segoe UI, sans-serif"
SERIF = "'Source Serif 4', Georgia, serif"


@dataclass(frozen=True)
class Theme:
    lower: str   # K-Means "Lower crime rates" group
    higher: str  # K-Means "Higher crime rates" group
    line: str    # trend lines
    ink: str
    ink_2: str
    lower_word: str   # colour names used in the heatmap explanation
    higher_word: str
    higher_text: str  # the higher colour, dark enough for text on white


THEMES = {
    "all_crimes": Theme(lower=NAVY, higher=AMBER, line=NAVY, ink=NAVY, ink_2=SLATE,
                        lower_word="navy", higher_word="amber", higher_text="#b4581a"),
    "women": Theme(lower=TEAL, higher=PURPLE, line=PURPLE, ink=CHARCOAL, ink_2="#5c5f73",
                   lower_word="teal", higher_word="purple", higher_text=PURPLE),
}

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

    @property
    def theme(self) -> Theme:
        return THEMES[self.key]

    def cluster_colour(self, cluster_name: str) -> str:
        return self.theme.higher if cluster_name.startswith("Higher") else self.theme.lower


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


def base_layout(fig: go.Figure, height: int, theme: Theme) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=24, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=theme.ink_2),
        hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(family=FONT, color=theme.ink)),
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
                line=dict(color=view.theme.line, width=2), marker=dict(size=5, color=view.theme.line), connectgaps=False,
                hovertemplate="%{x}: %{y:.1f} " + view.unit + "<extra></extra>",
            )
        )
        base_layout(fig, 220, view.theme)
        fig.update_layout(margin=dict(l=8, r=8, t=8, b=8))
        fig.update_yaxes(rangemode="tozero")
        fig.update_xaxes(dtick=5, range=[first - 0.5, last + 0.5])
        charts.append((CRIME_LABELS[crime], fig))
    return charts


def state_ranking_chart(view: View) -> go.Figure:
    clusters = view.clusters
    d = clusters.assign(total=clusters[view.features].sum(axis=1)).sort_values("total")
    colour = d["cluster_name"].map(view.cluster_colour)
    label = d["analysis_unit"] + d["is_unusual"].map({True: "  ◆", False: ""})
    fig = go.Figure(
        go.Bar(
            x=d["total"], y=label, orientation="h", marker=dict(color=colour, line=dict(width=0)),
            customdata=d[["cluster_name", "analysis_unit"]],
            hovertemplate="<b>%{customdata[1]}</b><br>%{x:.1f} cases " + view.unit + " a year"
                          "<br>%{customdata[0]}<extra></extra>",
        )
    )
    base_layout(fig, 820, view.theme)
    fig.update_layout(bargap=0.25, margin=dict(l=8, r=16, t=8, b=8))
    fig.update_xaxes(title=dict(text=f"Average cases {view.unit} a year", font=dict(size=12)),
                     showgrid=True, gridcolor=GRID)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=view.theme.ink_2))
    # Every state is already visible, so zooming adds nothing.
    fig.update_layout(dragmode=False)
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    return fig


def cluster_map_chart(view: View) -> go.Figure:
    clusters = view.clusters
    fig = go.Figure()
    for name in ("Lower crime rates", "Higher crime rates"):
        d = clusters[clusters["cluster_name"] == name]
        fig.add_trace(
            go.Scatter(
                x=d["pca_x"], y=d["pca_y"], mode="markers", name=f"K-Means: {name.lower()}",
                marker=dict(size=11, color=view.cluster_colour(name), line=dict(color="white", width=2),
                            symbol=d["is_unusual"].map({True: "diamond", False: "circle"})),
                text=d["analysis_unit"],
                customdata=d["is_unusual"].map({True: "<br>DBSCAN: unusual", False: ""}),
                hovertemplate="<b>%{text}</b><br>K-Means: " + name.lower() + "%{customdata}<extra></extra>",
            )
        )
    # Legend entry only: the diamond shape marks the states DBSCAN flagged.
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="DBSCAN: unusual (diamond)",
                             marker=dict(size=11, color=INK_3, symbol="diamond"), hoverinfo="skip"))
    for row in clusters[clusters["is_unusual"]].itertuples():
        fig.add_annotation(x=row.pca_x, y=row.pca_y, text=row.analysis_unit, showarrow=False,
                           yshift=16, font=dict(size=12, color=view.theme.ink))
    base_layout(fig, 440, view.theme)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.12, x=0, font=dict(color=view.theme.ink_2)),
                      margin=dict(l=8, r=8, t=8, b=8))
    pad = (clusters["pca_y"].max() - clusters["pca_y"].min()) * 0.12
    fig.update_yaxes(range=[clusters["pca_y"].min() - pad, clusters["pca_y"].max() + pad])
    fig.update_xaxes(title=dict(text="Map dimension 1", font=dict(size=12), standoff=4), showticklabels=False)
    fig.update_yaxes(title=dict(text="Map dimension 2", font=dict(size=12)), showticklabels=False, showgrid=False)
    return fig


def pattern_heatmap_chart(view: View) -> go.Figure:
    """States x crimes, each cell the state's rate compared with the typical (median) state's.

    Rows are grouped by K-Means group, so the chart shows which crimes put a state in its group.
    """
    clusters, features = view.clusters, view.features
    typical = clusters[features].median().replace(0, np.nan)
    ratio = clusters[features] / typical
    higher = clusters["cluster_name"].str.startswith("Higher")
    order = clusters.assign(higher=higher, total=clusters[features].sum(axis=1)) \
        .sort_values(["higher", "total"], ascending=[False, False]).index
    # log2 of the ratio: 0 is typical, +1 twice the typical rate, -1 half. Capped at 4x either way.
    z = np.log2(ratio.loc[order].clip(lower=1 / 16, upper=16)).clip(-2, 2)
    rows = clusters.loc[order]
    labels = [
        f"<span style='color:{view.theme.higher_text if r.cluster_name.startswith('Higher') else view.theme.lower}'>"
        f"{html.escape(r.analysis_unit)}"
        f"{'  ◆' if r.is_unusual else ''}</span>"
        for r in rows.itertuples()
    ]
    names = [CRIME_LABELS[f] for f in features]
    hover = [
        [f"{r.analysis_unit}<br>{CRIME_LABELS[f]}: {getattr(r, f):.1f} {view.unit}"
         f"<br>{ratio.at[i, f]:.1f}× the typical state" for f in features]
        for i, r in zip(order, rows.itertuples())
    ]
    fig = go.Figure(go.Heatmap(
        z=z.to_numpy(), x=names, y=labels, zmin=-2, zmax=2, xgap=2, ygap=2,
        colorscale=[[0, view.theme.lower], [0.5, "#ffffff"], [1, view.theme.higher]],
        text=hover, hovertemplate="%{text}<extra></extra>",
        colorbar=dict(tickvals=[-2, -1, 0, 1, 2], ticktext=["¼× or less", "½×", "typical", "2×", "4× or more"],
                      thickness=10, len=0.5, outlinewidth=0, tickfont=dict(size=11)),
    ))
    base_layout(fig, 24 * len(rows) + 140, view.theme)
    fig.update_layout(margin=dict(l=8, r=8, t=8, b=8))
    fig.update_xaxes(side="top", tickangle=-35, showline=False, ticks="", tickfont=dict(size=12))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(size=12))
    # A line between the two groups.
    n_higher = int(higher.sum())
    if 0 < n_higher < len(rows):
        fig.add_shape(type="line", xref="paper", x0=0, x1=1, y0=n_higher - 0.5, y1=n_higher - 0.5,
                      line=dict(color=view.theme.ink, width=2))
    return fig


def model_comparison_note(view: View) -> str:
    """The table of alternative methods, with one sentence on what it shows."""
    rows = view.clustering["model_comparison"]
    kmeans = next(r for r in rows if r["method"] == "K-Means")
    better = [r for r in rows if r["silhouette"] > kmeans["silhouette"] + 0.01 and r["smallest_group"] >= 3]
    body = "".join(
        f"<tr class='{'best' if r['method'] == 'K-Means' else ''}'><td>{esc(r['method'])}</td>"
        f"<td class='num'>{r['silhouette']:.3f}</td><td class='num'>{r['smallest_group']}</td></tr>"
        for r in rows
    )
    if better:
        verdict = (f"{esc(better[0]['method'])} scores higher here; K-Means is kept because it is the simplest "
                   "to explain and the groups are similar.")
    else:
        verdict = ("None finds clearly better groups. A higher score that comes from putting one or two states "
                   "in a group of their own is not a useful grouping.")
    return (f"<div class='card'><p class='small-head'>Other methods checked, {view.clustering['best_k']} groups</p>"
            f"<table><thead><tr><th>Method</th><th class='num'>Silhouette</th><th class='num'>Smallest group</th>"
            f"</tr></thead><tbody>{body}</tbody></table><p class='note'>{verdict}</p></div>")


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
                line=dict(color=view.theme.line, width=2), marker=dict(size=6, color=view.theme.line), connectgaps=False,
                hovertemplate=state + ", %{x}: %{y:.1f}<extra></extra>",
            )
        )
    buttons = [
        dict(label=state, method="update",
             args=[{"visible": [True] + [j == i for j in range(len(states))]}])
        for i, state in enumerate(states)
    ]
    base_layout(fig, 380, view.theme)
    fig.update_layout(
        showlegend=True, legend=dict(orientation="h", y=1.12, x=0.32, font=dict(color=view.theme.ink_2)),
        margin=dict(l=8, r=8, t=56, b=8),
        updatemenus=[dict(buttons=buttons, active=default, x=0, y=1.18, xanchor="left", yanchor="top",
                          bgcolor="white", bordercolor=GRID, font=dict(color=view.theme.ink))],
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


# Small inline icons for the two themes (24px line icons).
ICONS = {
    # Midnight Vigil: an open eye, a nation under watch.
    "all_crimes": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
                  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                  '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    # Broken Pedestal: a shield, strength rather than fragility.
    "women": '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
             '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
}

def line_icon(paths: str) -> str:
    return ('<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>')


# Side navigation: (target, label, icon). Targets inside a view use data-nav; the others are ids.
NAV = [
    ("top", "Overview", line_icon('<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>')),
    ("trends", "Trends", line_icon('<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>')),
    ("ranking", "Highest rates", line_icon('<path d="M4 6h16"/><path d="M4 12h11"/><path d="M4 18h6"/>')),
    ("groups", "Groups", line_icon('<circle cx="7" cy="8" r="3"/><circle cx="17" cy="8" r="3"/>'
                                   '<circle cx="12" cy="17" r="3"/>')),
    ("patterns", "What sets groups apart", line_icon('<rect x="3" y="3" width="7" height="7"/>'
                                                     '<rect x="14" y="3" width="7" height="7"/>'
                                                     '<rect x="3" y="14" width="7" height="7"/>'
                                                     '<rect x="14" y="14" width="7" height="7"/>')),
    ("explore", "Explore a state", line_icon('<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5"/>')),
    ("quality", "Data quality", line_icon('<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>')),
    ("built", "How it is built", line_icon('<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>')),
]
QUOTE_ICON = ('<svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true" fill="currentColor">'
              '<path d="M4 18c0-6 3-10 9-12l1 2c-3 1.5-5 4-5 7h4v9H4v-6zm14 0c0-6 3-10 9-12l1 2c-3 1.5-5 4-5 '
              '7h4v9h-9v-6z"/></svg>')
PEOPLE_ICON = line_icon('<circle cx="9" cy="8" r="3"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/>'
                        '<circle cx="17" cy="9" r="2.5"/><path d="M16 14.2c2.9.4 5 2.8 5 5.8"/>')

THEME_NOTES = {
    "all_crimes": {
        "name": "Midnight Vigil",
        "colours": ((NAVY, "Midnight navy"), (AMBER, "Burnt amber"), (SLATE, "Slate grey"), (IVORY, "Soft ivory")),
        "text": "A nation under watch: the data is here not to sensationalise crime, but to understand it and "
                "help make communities safer. Midnight navy stands for what lies beneath the surface of the "
                "numbers, burnt amber for vigilance and attention, slate grey for neutral, objective evidence, "
                "and soft ivory for clarity and transparency.",
    },
    "women": {
        "name": "Broken Pedestal",
        "colours": ((PURPLE, "Royal purple"), (TEAL, "Deep teal"), (GOLD, "Burnished gold"), (CHARCOAL, "Charcoal")),
        "text": "This theme is built around four ideas: dignity, resilience, truth, and hope. Purple reflects the "
                "strength and dignity every woman deserves. Gold represents the respect society often promises "
                "but must also practice. Teal symbolizes healing and solidarity with survivors. Charcoal reminds "
                "us that behind every statistic is a real person whose story deserves to be seen, heard, and "
                "acted upon.",
    },
}
WOMEN_QUOTE = "Respect is not measured by the names we give women, but by the safety we give them."


def silhouette_note(score: float) -> str:
    if score >= 0.25:
        return (f"A score of {score:.2f} means the groups are real but overlap: states sit on a "
                "spectrum, and the line between the groups is a rough one.")
    return (f"A score of {score:.2f} is weak: the groups overlap heavily. States sit on one continuous "
            "spectrum, so read the two groups as the lower and higher halves of it, not as separate "
            "types of state.")


def chart_card(inner: str, note: str = "", reset: bool = True) -> str:
    """A card holding one or more charts, with a button that undoes any zoom."""
    if not reset:
        return f'<div class="card">{inner}{note}</div>'
    return (f'<div class="card chart-card"><button type="button" class="reset" '
            f'title="Undo any zoom or pan on this chart">↺ Reset view</button>{inner}{note}</div>')


def purpose_callout() -> str:
    """The shared 'Behind every number' statement; its colours follow the theme."""
    return (f'<aside class="callout"><span class="icon">{PEOPLE_ICON}</span><div>'
            '<div class="label">Behind every number</div>'
            '<p class="big">Every point on this dashboard represents lives affected, not just numbers.</p>'
            '<p class="small">The goal is not to provoke fear, but to encourage understanding, accountability, '
            'and meaningful action.</p></div></aside>')


def theme_note(key: str) -> str:
    note = THEME_NOTES[key]
    chips = "".join(
        f"<span class='chip'><span class='dot' style='background:{hex_}'></span>{esc(name)}</span>"
        for hex_, name in note["colours"]
    )
    return (f"<div class='palette only-{key}'><div class='palette-head'><b>{esc(note['name'])}</b>{chips}</div>"
            f"<p>{esc(note['text'])}</p></div>")


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
        f"<p><span class='swatch' style='background:{view.cluster_colour(name)}'></span>"
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
<section data-nav="trends">
  <h2>How rates have changed across India</h2>
  <p>{esc(text['trend'])}</p>
  {chart_card(f'<div class="minis">{trend}</div>')}
  <p class="note">Reported cases depend on whether crimes are reported and recorded, so a rising line can mean
  better reporting as well as more crime. {esc(text['trend_note'])}</p>
</section>

<section data-nav="ranking">
  <h2>Which states have the highest rates</h2>
  {purpose_callout()}
  <p>Average yearly cases {view.unit}, {profile_window()}, {esc(text['ranking'])}.
  Colour shows the K-Means group each state falls into, and ◆ marks a state DBSCAN flagged as unusual.</p>
  {chart_card(chart_html(state_ranking_chart(view), f'chart-{k}-ranking'), reset=False)}
  <p class="note">The bars are sorted by this total, but K-Means groups states by their pattern across all {n}
  rates, each given equal weight. So a state can rank above one in the higher group and still fall in the lower
  group, for example when one crime type is high and the others are low.</p>
</section>

<section data-nav="groups">
  <h2>States grouped by their crime pattern</h2>
  <p>Three methods work together here. <b>K-Means</b> sorts states into groups by comparing all {n} rates at
  once; it tried 2 to 6 groups and kept the number with the best <i>silhouette score</i>, which measures how
  clearly the groups separate (from −1 to 1). <b>DBSCAN</b> flags states with too few similar neighbours.
  <b>PCA</b> only places the dots on the map.</p>
  <div class="key"><span><i class="k-dot"></i>Colour: K-Means group</span><span><i class="k-diamond"></i>Diamond: DBSCAN, unusual</span><span><i class="k-pos"></i>Position: similarity (PCA)</span></div>
  <div class="two">
    {chart_card(chart_html(cluster_map_chart(view), f'chart-{k}-map'),
                f'<p class="note">A similarity map: each dot is a state, and states with similar rate patterns sit '
                f'close together. The two axes summarise the {n} rates (principal component analysis) and have '
                f'no units, so distances are approximate.</p>')}
    <div>
      <div class="card"><table><thead><tr><th>K-Means groups</th><th class="num">Silhouette</th></tr></thead><tbody>{score_rows}</tbody></table>
      <p class="note">{esc(silhouette_note(c['best_silhouette']))}</p></div>
      <div class="card"><p class="note"><b>Unusual states (DBSCAN).</b> {describe_unusual(view)}
      For very small territories, a handful of cases is enough to move a rate this much.</p></div>
    </div>
  </div>
  {model_comparison_note(view)}
  <div class="card note"><p class="small-head">K-Means groups</p>{cluster_lists}</div>
</section>

<section data-nav="patterns">
  <h2>What sets each group apart</h2>
  <p>Each cell compares a state's rate for one crime with the typical (median) state's rate:
  {view.theme.higher_word} is higher than typical, {view.theme.lower_word} is lower, and white is about the same.
  These are the {n} rates K-Means compares. States above the line are in the higher group, and those below it
  are in the lower group, so the colours show which crimes put each state where it is.</p>
  {chart_card(chart_html(pattern_heatmap_chart(view), f'chart-{k}-heatmap'))}
</section>

<section data-nav="explore">
  <h2>Explore a state</h2>
  <p>{esc(view.total_label)} {view.unit}, against the all-India rate. Choose a state from the menu.</p>
  {chart_card(chart_html(state_explorer_chart(view), f'chart-{k}-explorer'))}
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
    tabs = "".join(
        f'<button type="button" role="tab" id="tab-{v.key}" aria-controls="view-{v.key}" data-theme="{v.key}" '
        f'aria-selected="{"true" if i == 0 else "false"}">{ICONS[v.key]}{esc(v.tab)}</button>'
        for i, v in enumerate(views)
    )
    sections = "\n".join(view_section(v) for v in views)
    nav_switch = "".join(
        f'<button type="button" data-switch="{v.key}" data-label="{esc(v.tab)}" aria-label="{esc(v.tab)}" '
        f'aria-pressed="{"true" if i == 0 else "false"}">{ICONS[v.key]}</button>'
        for i, v in enumerate(views)
    )
    nav_links = "".join(
        f'<a href="#{target}" data-target="{target}" data-label="{esc(label)}" aria-label="{esc(label)}">{icon}</a>'
        for target, label, icon in NAV
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crime in India, {first}–{last}</title>
<meta name="description" content="State-level crime in India, {first}-{last}, from official NCRB tables: all IPC/BNS crimes and crimes against women, as rates, with states grouped by clustering.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,600;8..60,700&display=swap" rel="stylesheet">
<script src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js" charset="utf-8"></script>
<style>
/* Midnight Vigil (all crimes) is the default; Broken Pedestal (crimes against women) overrides it. */
:root {{
  color-scheme: light;
  --surface: {IVORY}; --card: #ffffff; --ink: {NAVY}; --ink-2: {SLATE}; --ink-3: {INK_3}; --line: {GRID};
  --accent: {AMBER}; --accent-ink: {NAVY}; --link: #1f4e8c;
  --hero-bg: linear-gradient(160deg, {NAVY} 0%, #1c2541 100%); --hero-ink: {IVORY}; --hero-ink-2: #c5cad3;
  --hero-tile: rgba(255,255,255,.06); --hero-line: rgba(255,255,255,.14);
  --tab-on-bg: {AMBER}; --tab-on-ink: {NAVY};
  --callout-bg: linear-gradient(110deg, {NAVY} 0%, #1c2541 100%); --callout-bar: {AMBER};
  --callout-icon-bg: {AMBER}; --callout-icon: {NAVY}; --callout-label: {AMBER};
  --callout-ink: {IVORY}; --callout-ink-2: #c5cad3; --callout-shadow: rgba(11,19,43,.18);
  --closing-link: {AMBER}; --closing-step: {AMBER};
  --ok: #0ca30c; --warn: #b07a00; --bad: #d03b3b;
}}
body[data-theme="women"] {{
  --surface: #faf8fc; --ink: {CHARCOAL}; --ink-2: #5c5f73; --accent: {PURPLE}; --accent-ink: {PURPLE}; --link: {PURPLE};
  --hero-bg: radial-gradient(circle at 50% 0%, #e6dbf5 0%, #f1ebf9 45%, #faf6ec 100%);
  --hero-ink: {CHARCOAL}; --hero-ink-2: #55586b;
  --hero-tile: #ffffff; --hero-line: #dccdee;
  --tab-on-bg: {PURPLE}; --tab-on-ink: #ffffff;
  --callout-bg: linear-gradient(110deg, {PURPLE} 0%, #4a2f80 100%); --callout-bar: {GOLD};
  --callout-icon-bg: {GOLD}; --callout-icon: #ffffff; --callout-label: #f1dfae;
  --callout-ink: #ffffff; --callout-ink-2: #e3d9f3; --callout-shadow: rgba(91,58,154,.25);
  --closing-link: {PURPLE}; --closing-step: {PURPLE};
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--ink); font: 16px/1.6 {FONT}; transition: color .2s; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 0 16px; }}
.hero {{ background: var(--hero-bg); color: var(--hero-ink); border-bottom: 4px solid var(--accent); transition: background .3s; }}
body[data-theme="women"] .hero {{ border-bottom: 6px solid {GOLD}; }}
.hero-inner {{ max-width: 1040px; margin: 0 auto; padding: 56px 16px 36px; text-align: center; }}
.hero p.lead {{ font-size: 18px; color: var(--hero-ink-2); max-width: 720px; margin: 0 auto; }}
.hero .tiles, .hero .palette {{ text-align: left; }}
h1, h2 {{ font-family: {SERIF}; font-weight: 700; }}
h1 {{ font-size: clamp(32px, 5vw, 52px); line-height: 1.08; margin: 0 0 16px; letter-spacing: -0.01em; }}
body[data-theme="women"] h1 {{ color: {PURPLE}; font-size: clamp(34px, 5.6vw, 60px); }}
h1 .years {{ display: block; font-size: .5em; font-weight: 600; color: var(--accent); margin-top: 6px; }}
body[data-theme="women"] h1 .years {{ color: {GOLD}; }}
h2 {{ font-size: 26px; margin: 0 0 8px; }}
h2::before {{ content: ""; display: block; width: 36px; height: 3px; background: var(--accent); margin-bottom: 10px; border-radius: 2px; }}
section {{ margin-top: 56px; }}
section > p {{ color: var(--ink-2); max-width: 760px; }}
a {{ color: var(--link); }}
.hero a {{ color: inherit; }}
.eyebrow {{ display: flex; justify-content: center; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
body[data-theme="women"] .eyebrow {{ color: {PURPLE}; }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-top: 28px; }}
.tile {{ background: var(--hero-tile); border: 1px solid var(--hero-line); border-radius: 10px; padding: 16px 18px; }}
.tile .value {{ font-size: 28px; font-weight: 700; letter-spacing: -0.01em; }}
.tile .label {{ font-size: 14px; color: var(--hero-ink-2); }}
/* The women's total becomes the lead tile when that view is on. */
body[data-theme="women"] .tile.women-tile {{ background: {PURPLE}; border-color: {PURPLE}; color: #fff;
  box-shadow: 0 10px 24px rgba(91,58,154,.28), inset 0 -4px 0 {GOLD}; transform: translateY(-4px); }}
body[data-theme="women"] .tile.women-tile .label {{ color: #e8def7; }}
.tile {{ transition: background .3s, transform .3s, box-shadow .3s; }}
.quote-band {{ margin: 32px -16px 0; padding: 36px 24px 32px; background: linear-gradient(135deg, {PURPLE} 0%, #4a2f80 100%);
  color: #fff; text-align: center; border-radius: 14px; position: relative; }}
.quote-band svg {{ color: {GOLD}; }}
.quote-band blockquote {{ margin: 8px auto 0; max-width: 760px; font: italic 600 clamp(22px, 3vw, 30px)/1.35 {SERIF}; }}
.quote-band .rule {{ width: 64px; height: 3px; background: {GOLD}; margin: 18px auto 0; border-radius: 2px; }}
/* "Behind every number": the same words in both views, in the open view's colours. */
.callout {{ display: flex; gap: 18px; align-items: center; margin: 18px 0 20px; padding: 22px 24px 22px 28px;
  background: var(--callout-bg); border-radius: 14px; position: relative; overflow: hidden;
  box-shadow: 0 8px 22px var(--callout-shadow); }}
.callout::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; background: var(--callout-bar); }}
.callout .icon {{ flex: none; display: grid; place-items: center; width: 52px; height: 52px; border-radius: 50%;
  background: var(--callout-icon-bg); color: var(--callout-icon); }}
.callout .icon svg {{ width: 24px; height: 24px; }}
.callout .label {{ font-size: 12px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--callout-label); }}
.callout .big {{ margin: 4px 0 0; font: 600 clamp(19px, 2.2vw, 23px)/1.35 {SERIF}; color: var(--callout-ink); }}
.callout .small {{ margin: 6px 0 0; font-size: 15px; color: var(--callout-ink-2); }}
@media (max-width: 560px) {{ .callout {{ flex-direction: column; align-items: flex-start; }} }}
.palette {{ margin: 24px auto 0; padding-top: 16px; border-top: 1px solid var(--hero-line); max-width: 860px; }}
.palette-head {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; font-size: 14px; }}
.palette p {{ font-size: 14px; color: var(--hero-ink-2); margin: 8px 0 0; }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--hero-ink-2); }}
.dot {{ width: 12px; height: 12px; border-radius: 50%; border: 1px solid var(--hero-line); }}
body:not([data-theme="women"]) .only-women, body[data-theme="women"] .only-all_crimes {{ display: none; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-top: 16px; }}
.chart-card {{ position: relative; padding-top: 48px; }}
.reset {{ position: absolute; top: 10px; right: 10px; z-index: 2; font: 500 12px/1 {FONT}; color: var(--ink-2);
  background: #fff; border: 1px solid var(--line); border-radius: 999px; padding: 6px 10px; cursor: pointer; }}
.reset:hover {{ color: var(--accent-ink); border-color: var(--accent); }}
.note {{ font-size: 14px; color: var(--ink-2); }}
.small-head {{ font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: var(--ink-3); margin: 0 0 4px; }}
.key {{ display: flex; flex-wrap: wrap; gap: 8px 20px; font-size: 13px; color: var(--ink-2); margin-top: 12px; }}
.key span {{ display: inline-flex; align-items: center; gap: 6px; }}
.k-dot, .k-diamond, .k-pos {{ display: inline-block; width: 10px; height: 10px; }}
.k-dot {{ border-radius: 50%; background: linear-gradient(90deg, {NAVY} 50%, {AMBER} 50%); }}
body[data-theme="women"] .k-dot {{ background: linear-gradient(90deg, {TEAL} 50%, {PURPLE} 50%); }}
.k-diamond {{ background: {INK_3}; transform: rotate(45deg) scale(.85); }}
.k-pos {{ border: 2px dotted {INK_3}; border-radius: 50%; }}
.minis {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 8px 16px; }}
.mini {{ min-width: 0; }}
.mini h3 {{ font-size: 14px; font-weight: 600; margin: 4px 0 0 8px; }}
.two {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; align-items: start; }}
@media (max-width: 760px) {{ .two {{ grid-template-columns: 1fr; }} }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
th {{ color: var(--ink-3); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr.best td {{ font-weight: 700; color: var(--accent-ink); }}
.table-wrap {{ overflow-x: auto; }}
.badge {{ display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }}
.badge.ok {{ color: var(--ok); background: #e9f6e9; }}
.badge.warn {{ color: var(--warn); background: #fdf3dc; }}
.badge.bad {{ color: var(--bad); background: #fbe7e7; }}
.swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 8px; }}
.flow {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: stretch; margin-top: 16px; }}
.step {{ flex: 1 1 150px; background: var(--card); border: 1px solid var(--line); border-top: 3px solid var(--accent); border-radius: 10px; padding: 12px 14px; }}
.step b {{ display: block; }}
.step span {{ font-size: 13px; color: var(--ink-2); }}
details summary {{ cursor: pointer; color: var(--link); font-weight: 500; }}
.switch {{ display: inline-flex; flex-wrap: wrap; justify-content: center; gap: 4px; margin-top: 32px; padding: 4px; background: var(--hero-tile); border: 1px solid var(--hero-line); border-radius: 999px; }}
.switch button {{ display: inline-flex; align-items: center; gap: 8px; font: 600 15px/1 {FONT}; color: var(--hero-ink-2); background: none; border: 0; border-radius: 999px; padding: 12px 20px; cursor: pointer; transition: background .2s; }}
.switch button[aria-selected="true"] {{ background: var(--tab-on-bg); color: var(--tab-on-ink); }}
.view[hidden] {{ display: none; }}
/* Side navigation: one icon per section, a line that fills as you scroll, labels on hover. */
.sidenav {{ position: fixed; right: 18px; top: 50%; transform: translateY(-50%); z-index: 10;
  display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 6px;
  background: rgba(255,255,255,.92); border: 1px solid var(--line); border-radius: 999px;
  box-shadow: 0 6px 20px rgba(11,19,43,.10); backdrop-filter: blur(6px); }}
.sidenav .track {{ position: absolute; left: 50%; top: 16px; bottom: 16px; width: 2px; margin-left: -1px; background: var(--line); z-index: -1; }}
.sidenav .fill {{ position: absolute; left: 0; top: 0; width: 100%; height: 0; background: var(--accent); }}
.sidenav a, .sidenav button {{ position: relative; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%;
  color: var(--ink-2); background: #fff; border: 1px solid transparent; cursor: pointer; text-decoration: none; padding: 0; }}
.sidenav a:hover, .sidenav button:hover {{ color: var(--accent-ink); border-color: var(--accent); }}
.sidenav a.active {{ background: var(--accent); color: var(--tab-on-ink); }}
body[data-theme="women"] .sidenav a.active {{ color: #fff; }}
.sidenav .sep {{ width: 18px; height: 1px; background: var(--line); margin: 2px 0; }}
.sidenav button[aria-pressed="true"] {{ background: var(--tab-on-bg); color: var(--tab-on-ink); }}
.sidenav [data-label]::after {{ content: attr(data-label); position: absolute; right: 44px; white-space: nowrap;
  font: 500 12px/1 {FONT}; color: #fff; background: var(--ink); padding: 6px 10px; border-radius: 6px;
  opacity: 0; pointer-events: none; transform: translateX(4px); transition: opacity .15s, transform .15s; }}
.sidenav [data-label]:hover::after, .sidenav [data-label]:focus-visible::after {{ opacity: 1; transform: none; }}
@media (max-width: 1180px) {{ .sidenav {{ right: 8px; }} }}
@media (max-width: 760px) {{ .sidenav {{ display: none; }} }}
/* The closing sections (data quality onwards) carry the theme as strongly as the header. */
.closing {{ margin-top: 72px; background: var(--hero-bg); color: var(--hero-ink); border-top: 4px solid var(--accent); transition: background .3s; }}
body[data-theme="women"] .closing {{ border-top: 6px solid {GOLD}; }}
.closing-inner {{ max-width: 1040px; margin: 0 auto; padding: 8px 16px 48px; }}
.closing section > p {{ color: var(--hero-ink-2); }}
.closing h2 {{ color: var(--hero-ink); }}
body[data-theme="women"] .closing h2 {{ color: {PURPLE}; }}
.closing h2::before {{ background: var(--closing-step); }}
body[data-theme="women"] .closing h2::before {{ background: {GOLD}; }}
.closing .card, .closing .step {{ color: {NAVY}; }}
body[data-theme="women"] .closing .card, body[data-theme="women"] .closing .step {{ color: {CHARCOAL}; }}
.closing .step {{ border-top: 4px solid var(--closing-step); }}
.closing > .closing-inner > section > p a, .closing footer a {{ color: var(--closing-link); }}
.closing footer {{ border-top-color: var(--hero-line); color: var(--hero-ink-2); }}
footer {{ margin-top: 64px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 14px; color: var(--ink-2); }}
</style>
</head>
<body data-theme="all_crimes">
<nav class="sidenav" aria-label="Sections">
  <span class="track"><span class="fill"></span></span>
  {nav_switch}
  <span class="sep"></span>
  {nav_links}
</nav>
<header class="hero" id="top">
<div class="hero-inner">
  <div class="eyebrow"><span class="only-all_crimes">{ICONS['all_crimes']}</span><span class="only-women">{ICONS['women']}</span>Data engineering project · NCRB {first}–{last}</div>
  <h1><span class="only-all_crimes">Crime in India</span><span class="only-women">Crimes against women in India</span><span class="years">{first}–{last}</span></h1>
  <p class="lead">Official NCRB figures for every state and union territory: checked, mapped across changes in
  the law, and turned into rates using the population NCRB itself used. States are then grouped by how similar
  their crime patterns are. The whole site is rebuilt from the raw files by an automated pipeline.</p>
  <div class="tiles">
    <div class="tile"><div class="value">{all_cases:,}</div><div class="label">IPC/BNS crimes recorded, {first}–{last}</div></div>
    <div class="tile women-tile"><div class="value">{women_cases:,}</div><div class="label">crimes against women recorded: NCRB's category for offences specific to women, such as rape, dowry deaths and cruelty by husband</div></div>
    <div class="tile"><div class="value">{len(data['states'])}</div><div class="label">states and union territories</div></div>
    <div class="tile"><div class="value">{summary['checks_passed']} / {summary['checks_run']}</div><div class="label">data quality checks passed; the rest are reported problems in the sources</div></div>
  </div>
  <div class="switch" role="tablist" aria-label="Choose a view">{tabs}</div>
  <div class="only-women">
    <figure class="quote-band">{QUOTE_ICON}<blockquote>{esc(WOMEN_QUOTE)}</blockquote><div class="rule"></div></figure>
  </div>
  {theme_note('all_crimes')}
  {theme_note('women')}
</div>
</header>
<main>

{sections}
</main>
<div class="closing"><div class="closing-inner">
<section id="quality">
  <h2>Data quality</h2>
  <p>The raw files are checked before anything is built. Critical problems stop the pipeline, and each known
  problem in NCRB's tables is handled by a reviewed rule, never a manual edit.</p>
  <div class="card">
    <ul class="note">
      <li><b>Official tables only.</b> Every figure comes from NCRB's <i>Crime in India</i> tables, kept in the
      repository with a checksum for each file, and every state adds up to NCRB's all-India row.</li>
      <li><b>Two NCRB tables agree.</b> The crimes-against-women tables and the all-crimes tables report
      {cross_checked:,} of the same figures, and all of them match.</li>
      <li><b>Rates match NCRB's.</b> Rates use the population NCRB used, and reproduce NCRB's printed rates.
      NCRB did not publish the female population before 2012, so for 2001–2011 it is estimated from
      NCRB's total population and each state's female share in the 2011 Census.</li>
      <li><b>The Kaggle copy is a backup only.</b> Compared with NCRB's tables, {kaggle_match:,} of its figures match
      and {kaggle_diff:,} differ, so it is not used for the charts.</li>
    </ul>
    <details><summary>Show all {summary['checks_run']} checks</summary>
      <div class="table-wrap"><table>
        <thead><tr><th>Source</th><th>Check</th><th>Severity</th><th>Result</th><th class="num">Rows</th></tr></thead>
        <tbody>{quality_rows(quality)}</tbody>
      </table></div>
    </details>
    <p class="note">Full write-up: <a href="{REPO_URL}/blob/main/docs/data_quality.md">docs/data_quality.md</a>.</p>
  </div>
</section>

<section id="built">
  <h2>How it is built</h2>
  <p>GitHub Actions runs the tests and the full pipeline on every code change, then publishes this site. To add a
  new year, its NCRB table is added to the manifest and the same pipeline checks, maps and rebuilds everything.
  Only free, open-source tools are used.</p>
  <div class="flow">
    <div class="step"><b>Raw files</b><span>NCRB tables, kept in the repository and verified by checksum</span></div>
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
  Source: National Crime Records Bureau (NCRB), <i>Crime in India</i> {first}–{last}, as extracted by the
  <a href="https://github.com/reclaimchennai/NCRB">reclaimchennai/NCRB</a> project, and NCRB's district-wise
  files on data.gov.in. Population: NCRB (Registrar General of India projections); Census of India 2011
  for the female share before 2012.
</footer>
</div></div>
<script>
// The view switch: show one view, change the theme, and size the view's charts to their boxes.
const tabs = document.querySelectorAll(".switch button");
tabs.forEach((tab) => tab.addEventListener("click", () => {{
  tabs.forEach((t) => {{
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  }});
  document.body.dataset.theme = tab.dataset.theme;
  window.scrollTo({{ top: 0, behavior: "smooth" }});
  document.querySelectorAll(".sidenav [data-switch]").forEach((b) =>
    b.setAttribute("aria-pressed", b.dataset.switch === tab.dataset.theme));
  document.querySelectorAll("#" + tab.getAttribute("aria-controls") + " .plotly-graph-div")
    .forEach((div) => Plotly.Plots.resize(div));
  updateNav();
}}));
// Side navigation: the view buttons work like the switch, and links go to the section in the open view.
document.querySelectorAll(".sidenav [data-switch]").forEach((b) => b.addEventListener("click", () => {{
  document.getElementById("tab-" + b.dataset.switch).click();
}}));
function navTarget(name) {{
  return document.getElementById(name) || document.querySelector(`.view:not([hidden]) [data-nav="${{name}}"]`);
}}
const navLinks = [...document.querySelectorAll(".sidenav a")];
navLinks.forEach((a) => a.addEventListener("click", (event) => {{
  event.preventDefault();
  navTarget(a.dataset.target).scrollIntoView({{ behavior: "smooth", block: "start" }});
}}));
function updateNav() {{
  const line = window.innerHeight * 0.35;
  let current = navLinks[0];
  for (const a of navLinks) {{
    const target = navTarget(a.dataset.target);
    if (target && target.getBoundingClientRect().top <= line) current = a;
  }}
  navLinks.forEach((a) => a.classList.toggle("active", a === current));
  const max = document.documentElement.scrollHeight - window.innerHeight;
  document.querySelector(".sidenav .fill").style.height = (max > 0 ? 100 * window.scrollY / max : 0) + "%";
}}
window.addEventListener("scroll", updateNav, {{ passive: true }});
updateNav();
// Remember each chart's starting axes, so "Reset view" can undo zooming and panning.
const startAxes = new Map();
function rememberAxes(div) {{
  const axes = {{}};
  for (const name of ["xaxis", "yaxis"]) {{
    const axis = div.layout[name] || {{}};
    axes[name] = axis.autorange !== true && axis.range ? axis.range.slice() : null;
  }}
  startAxes.set(div, axes);
}}
function resetAxes(div) {{
  const update = {{}};
  for (const [name, range] of Object.entries(startAxes.get(div) || {{}})) {{
    if (range) update[name + ".range"] = range.slice();
    else update[name + ".autorange"] = true;
  }}
  Plotly.relayout(div, update);
}}
document.querySelectorAll(".chart-card .reset").forEach((button) => button.addEventListener("click", () => {{
  button.closest(".chart-card").querySelectorAll(".plotly-graph-div").forEach(resetAxes);
}}));
// Charts are drawn while the page is still loading; size them to their final boxes.
window.addEventListener("load", () => {{
  document.querySelectorAll(".plotly-graph-div").forEach((div) => {{
    rememberAxes(div);
    Plotly.Plots.resize(div);
  }});
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
