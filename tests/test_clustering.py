import numpy as np
import pandas as pd

from pipeline.clustering import FEATURES, cluster_states, elbow_eps


def make_profile(groups: dict[str, tuple[float, int]], seed: int = 0) -> pd.DataFrame:
    """States with rates scattered around a level, one level per group."""
    rng = np.random.default_rng(seed)
    rows = []
    for prefix, (level, count) in groups.items():
        for i in range(count):
            rates = level * rng.uniform(0.9, 1.1, size=len(FEATURES))
            rows.append({"analysis_unit": f"{prefix}{i}", **dict(zip(FEATURES, rates))})
    return pd.DataFrame(rows)


def test_finds_well_separated_groups_and_names_them_by_rate():
    profile = make_profile({"low": (1, 8), "high": (50, 8)})
    result = cluster_states(profile)

    assert result.best_k == 2
    assert result.best_silhouette > 0.7
    names = result.states.set_index("analysis_unit")["cluster_name"]
    assert set(names[names.index.str.startswith("low")]) == {"Lower crime rates"}
    assert set(names[names.index.str.startswith("high")]) == {"Higher crime rates"}


def test_dbscan_flags_a_state_unlike_any_other():
    profile = make_profile({"a": (5, 10), "b": (40, 10)})
    odd = {"analysis_unit": "odd", **{f: 5.0 for f in FEATURES}, "dowry_deaths": 400.0}
    result = cluster_states(pd.concat([profile, pd.DataFrame([odd])], ignore_index=True))

    assert "odd" in result.unusual_states


def test_results_are_repeatable():
    profile = make_profile({"a": (2, 6), "b": (10, 6), "c": (60, 6)})
    first, second = cluster_states(profile), cluster_states(profile)
    assert first.states.equals(second.states)


def test_elbow_eps_sits_between_the_dense_and_sparse_distances():
    x = np.vstack([np.zeros((10, 2)) + np.linspace(0, 0.1, 10)[:, None], [[5, 5], [9, 9]]])
    eps = elbow_eps(x, min_samples=3)
    assert 0 < eps < 5
