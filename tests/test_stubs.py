"""Every frozen public entry point exists and every CLI parses --help (no network, no DB)."""

import importlib

import pytest

ENTRY_POINTS = {
    "clinova.ingest.pipeline": ["run"],
    "clinova.parse.parser": ["parse_protocol"],
    "clinova.parse.visits": ["extract_visits", "estimate_visits"],
    "clinova.parse.diff": ["diff_rules"],
    "clinova.parse.embed": ["embed_trials", "nearest"],
    "clinova.cohort.generate": ["generate_cohort", "cohort_frame", "generate_cohort_df"],
    "clinova.simulate.screen": ["screen", "screen_results", "apply_edits"],
    "clinova.simulate.diversity": ["diversity_index"],
    "clinova.simulate.agents": ["trial_offer", "arun_agents", "run_agents"],
    "clinova.simulate.runner": ["simulate_trial"],
    "clinova.simulate.surrogate": ["Surrogate"],
    "clinova.model.features": ["build_features"],
    "clinova.model.train": ["train", "load_bundle"],
    "clinova.model.predict": ["score", "flag_criteria"],
    "clinova.optimize.search": ["optimize"],
    "clinova.loop.ledger": ["record_prediction", "record_feedback"],
    "clinova.loop.checker": ["check_outcomes"],
    "clinova.loop.replay": ["run_replay"],
}

CLIS = ["ingest", "parse", "cohort", "simulate", "model", "optimize", "loop", "api"]


@pytest.mark.parametrize("module", sorted(ENTRY_POINTS))
def test_entry_points_exist(module):
    mod = importlib.import_module(module)
    for name in ENTRY_POINTS[module]:
        assert callable(getattr(mod, name)), f"{module}.{name}"


@pytest.mark.parametrize("pkg", CLIS)
def test_cli_help(pkg):
    main = importlib.import_module(f"clinova.{pkg}.__main__").main
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
