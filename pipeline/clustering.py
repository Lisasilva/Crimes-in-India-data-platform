"""Group states by their crime profile (K-Means) and flag unusual ones (DBSCAN).

Input is gold.mart_state_crime_profile: each state's average yearly rate per
100,000 women for each crime type over the profile window. Results are written
back to the warehouse as gold.state_clusters and gold.cluster_scores, and to a
report.

Choices, kept deliberately simple:
- Rates, not case counts, so large states are not grouped together just for
  being large.
- log(1 + rate), then z-scores, so no single crime type dominates the distance.
- "Importation of girls" is left out: it is zero or near zero almost everywhere
  and would only add noise.
- The number of clusters is the one with the best silhouette score, which
  measures how well separated the groups are (from -1 to 1, higher is better).
- DBSCAN marks a state as unusual when it has too few similar states nearby.
  Its distance threshold (eps) is the "elbow" of the sorted distances to each
  state's k-th nearest neighbour, the standard way to choose it.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

FEATURES = [
    "rape",
    "kidnapping_abduction",
    "dowry_deaths",
    "assault_on_women",
    "insult_to_modesty",
    "cruelty_by_husband",
]
K_RANGE = range(2, 7)
DBSCAN_MIN_SAMPLES = 3
RANDOM_STATE = 42


@dataclass
class ClusteringResult:
    states: pd.DataFrame
    scores: pd.DataFrame
    best_k: int
    best_silhouette: float
    dbscan_eps: float
    unusual_states: list[str]


def prepare_features(profile: pd.DataFrame) -> np.ndarray:
    return StandardScaler().fit_transform(np.log1p(profile[FEATURES].to_numpy(dtype=float)))


def choose_k(x: np.ndarray) -> tuple[pd.DataFrame, int]:
    rows = []
    for k in K_RANGE:
        labels = KMeans(n_clusters=k, n_init=20, random_state=RANDOM_STATE).fit_predict(x)
        rows.append({"k": k, "silhouette": round(float(silhouette_score(x, labels)), 4)})
    scores = pd.DataFrame(rows)
    best_k = int(scores.loc[scores["silhouette"].idxmax(), "k"])
    return scores, best_k


def elbow_eps(x: np.ndarray, min_samples: int = DBSCAN_MIN_SAMPLES) -> float:
    """Distance at the elbow of the sorted k-nearest-neighbour distance curve.

    The elbow is the point furthest from the straight line joining the first
    and last points of the curve.
    """
    # Each point is its own nearest neighbour, so ask for min_samples neighbours.
    distances, _ = NearestNeighbors(n_neighbors=min_samples).fit(x).kneighbors(x)
    curve = np.sort(distances[:, -1])
    n = len(curve)
    line_start, line_end = np.array([0, curve[0]]), np.array([n - 1, curve[-1]])
    direction = (line_end - line_start) / np.linalg.norm(line_end - line_start)
    points = np.column_stack([np.arange(n), curve]) - line_start
    distance_to_line = np.abs(points[:, 0] * direction[1] - points[:, 1] * direction[0])
    return float(curve[int(np.argmax(distance_to_line))])


def name_clusters(profile: pd.DataFrame, labels: np.ndarray) -> dict[int, str]:
    """Name clusters by their average total rate, from lowest to highest."""
    total_rate = profile[FEATURES].sum(axis=1)
    order = total_rate.groupby(labels).mean().sort_values().index
    names = ["Lower", "Middle", "Higher"] if len(order) == 3 else [f"Group {i + 1}" for i in range(len(order))]
    if len(order) == 2:
        names = ["Lower", "Higher"]
    return {int(cluster): f"{names[rank]} crime rates" for rank, cluster in enumerate(order)}


def cluster_states(profile: pd.DataFrame) -> ClusteringResult:
    profile = profile.dropna(subset=FEATURES).reset_index(drop=True)
    x = prepare_features(profile)

    scores, best_k = choose_k(x)
    kmeans_labels = KMeans(n_clusters=best_k, n_init=20, random_state=RANDOM_STATE).fit_predict(x)
    cluster_names = name_clusters(profile, kmeans_labels)

    eps = elbow_eps(x)
    dbscan_labels = DBSCAN(eps=eps, min_samples=DBSCAN_MIN_SAMPLES).fit_predict(x)

    coords = PCA(n_components=2, random_state=RANDOM_STATE).fit_transform(x)
    states = profile[["analysis_unit", *FEATURES]].copy()
    states["cluster"] = kmeans_labels
    states["cluster_name"] = [cluster_names[c] for c in kmeans_labels]
    states["is_unusual"] = dbscan_labels == -1
    states["pca_x"], states["pca_y"] = coords[:, 0].round(4), coords[:, 1].round(4)

    return ClusteringResult(
        states=states,
        scores=scores,
        best_k=best_k,
        best_silhouette=float(scores["silhouette"].max()),
        dbscan_eps=round(eps, 4),
        unusual_states=sorted(states.loc[states["is_unusual"], "analysis_unit"]),
    )


def run(con: duckdb.DuckDBPyConnection, out_dir: Path) -> ClusteringResult:
    profile = con.sql("SELECT * FROM gold.mart_state_crime_profile ORDER BY analysis_unit").df()
    result = cluster_states(profile)

    con.register("states_df", result.states)
    con.execute("CREATE OR REPLACE TABLE gold.state_clusters AS SELECT * FROM states_df")
    con.unregister("states_df")
    con.register("scores_df", result.scores)
    con.execute("CREATE OR REPLACE TABLE gold.cluster_scores AS SELECT * FROM scores_df")
    con.unregister("scores_df")

    write_report(result, out_dir)
    return result


def write_report(result: ClusteringResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in asdict(result).items() if k not in ("states", "scores")}
    (out_dir / "clustering_report.json").write_text(
        json.dumps(
            {
                **summary,
                "scores": result.scores.to_dict(orient="records"),
                "states": result.states.to_dict(orient="records"),
            },
            indent=2,
            default=str,
        )
    )

    lines = [
        "# State clustering",
        "",
        f"K-Means chose **{result.best_k} clusters** (silhouette {result.best_silhouette:.3f}). "
        f"DBSCAN (eps {result.dbscan_eps}, min_samples {DBSCAN_MIN_SAMPLES}) marked "
        f"{len(result.unusual_states)} states as unusual: {', '.join(result.unusual_states) or 'none'}.",
        "",
        "| k | Silhouette |",
        "| --- | --- |",
        *[f"| {r.k} | {r.silhouette:.3f} |" for r in result.scores.itertuples()],
        "",
        "| Cluster | States |",
        "| --- | --- |",
    ]
    for name, group in result.states.groupby("cluster_name"):
        lines.append(f"| {name} | {', '.join(sorted(group['analysis_unit']))} |")
    path = out_dir / "clustering_report.md"
    path.write_text("\n".join(lines) + "\n")
    return path
