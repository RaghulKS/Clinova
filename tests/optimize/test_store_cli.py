"""Offline persistence and CLI integration checks using contract fixtures and a fake DB."""

import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from clinova.common.fixtures import load_fixture
from clinova.contracts import CriteriaRule, Fix, Setting, TrialRecord
from clinova.optimize import __main__ as cli
from clinova.optimize import store
from clinova.optimize.scoring import StubScorer
from clinova.optimize.search import OptimizationResult


class FakeDatabase:
    """Model SELECTs and transactional fixes UPSERTs without opening a connection."""

    def __init__(self, trial=None, rules=()):
        self.trial = trial
        self.rules = list(rules)
        self.calls = []
        self.rows = {}
        self.transactions = 0
        self.rollbacks = 0
        self.fail_fix_id = None

    @contextmanager
    def connect(self):
        self.transactions += 1
        before = deepcopy(self.rows)
        try:
            yield self
        except Exception:
            self.rows = before
            self.rollbacks += 1
            raise

    def cursor(self, **kwargs):
        return self

    def execute(self, sql, params):
        self.calls.append((sql, params))
        normalized = " ".join(sql.lower().split())
        if normalized.startswith("insert into fixes "):
            fix_id, trial_id, label, edits, metrics = params
            assert "on conflict (fix_id) do update" in normalized
            assert isinstance(edits, Jsonb) and isinstance(metrics, Jsonb)
            if fix_id == self.fail_fix_id:
                raise RuntimeError("synthetic write failure")
            self.rows[fix_id] = (trial_id, label, deepcopy(edits.obj), deepcopy(metrics.obj))
        else:
            assert normalized.startswith("select ")
            assert "from trials " in normalized or "from criteria_rules " in normalized
        return self

    def fetchone(self):
        return deepcopy(self.trial)

    def fetchall(self):
        return deepcopy(self.rules)


def unexpected_call(*args, **kwargs):
    pytest.fail("offline test reached an unexpected external dependency")


@pytest.fixture
def fixes():
    return load_fixture("fixes.json", list[Fix])


@pytest.fixture
def trial_row():
    trial = load_fixture("trial_record.json", TrialRecord)
    row = trial.model_dump(mode="json")
    row["updated_at"] = "2026-10-10T00:00:00Z"
    row["first_version"]["descriptionModule"] = {"briefSummary": "Original registered summary"}
    row["first_version"]["armsInterventionsModule"] = {
        "armGroups": [{"label": "Original docetaxel intravenous arm"}, {}],
    }
    row["current"]["descriptionModule"] = {"briefSummary": "Amended summary must not leak"}
    row["current"]["armsInterventionsModule"] = {"armGroups": [{"label": "Current oral tablet arm"}]}
    return row


def test_save_fixes_only_writes_owned_table_with_idempotent_upsert_payload(monkeypatch, fixes):
    database = FakeDatabase()
    monkeypatch.setattr(store, "get_conn", database.connect)
    before = [fix.model_dump(mode="json") for fix in fixes]
    assert store.save_fixes(fixes) == 3
    assert store.save_fixes(fixes) == 3
    assert database.transactions == 2 and len(database.rows) == 3
    assert len(database.calls) == 6
    for (sql, params), expected in zip(database.calls, before * 2, strict=True):
        assert "INSERT INTO fixes" in sql and "ON CONFLICT (fix_id) DO UPDATE" in sql
        assert "edits = EXCLUDED.edits" in sql and "metrics = EXCLUDED.metrics" in sql
        assert "label = EXCLUDED.label" in sql
        assert params[:3] == (expected["fix_id"], expected["trial_id"], expected["label"])
        assert params[3].obj == expected["edits"]
        assert params[4].obj == {
            key: value for key, value in expected.items()
            if key not in {"fix_id", "trial_id", "label", "edits"}
        }
    revised = fixes[0].model_copy(update={"summary": "Updated expected-value explanation"})
    assert store.save_fixes([revised]) == 1
    assert len(database.rows) == 3
    assert database.rows[revised.fix_id][3]["summary"] == revised.summary
    assert [fix.model_dump(mode="json") for fix in fixes] == before


def test_save_fixes_uses_one_transaction_and_propagates_write_failures(monkeypatch, fixes):
    database = FakeDatabase()
    database.fail_fix_id = fixes[1].fix_id
    monkeypatch.setattr(store, "get_conn", database.connect)
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        store.save_fixes(fixes)
    assert database.transactions == 1 and database.rollbacks == 1
    assert not database.rows


def test_load_protocol_reads_version_zero_and_preserves_original_metadata(monkeypatch, trial_row, parsed_protocol):
    database = FakeDatabase(trial_row, [rule.model_dump(mode="json") for rule in parsed_protocol.rules])
    monkeypatch.setattr(store, "get_conn", database.connect)
    monkeypatch.setattr("clinova.common.llm.generate_json", unexpected_call)
    protocol = store.load_protocol(trial_row["nct_id"])
    assert protocol.trial_id == trial_row["nct_id"] and protocol.version_no == 0
    assert protocol.phase == trial_row["phase"] and protocol.title == trial_row["title"]
    assert protocol.summary == "Original registered summary"
    assert protocol.arms == ["Original docetaxel intravenous arm", "Unspecified arm"]
    assert protocol.randomized is True and protocol.setting == Setting.second_line_plus
    assert protocol.rules == parsed_protocol.rules
    assert protocol.unparseable == parsed_protocol.unparseable
    assert all(rule.locked for rule in protocol.rules if rule.field in {"pregnancy", "consent_capacity"})
    assert protocol.visits and all(visit.estimated for visit in protocol.visits)
    assert any("infusion" in visit.procedures for visit in protocol.visits)
    assert all("oral_drug_dispense" not in visit.procedures for visit in protocol.visits)
    assert database.transactions == 1 and len(database.calls) == 2
    for sql, params in database.calls:
        assert sql.lstrip().startswith("SELECT")
        assert params == (trial_row["nct_id"],)
    rules_sql = database.calls[1][0]
    assert "version_no = 0" in rules_sql and "ORDER BY CASE kind" in rules_sql


@pytest.mark.parametrize("row, rules, message", [
    (None, [], "has not been ingested"),
    ("trial", [], "no cached first-version rules"),
])
def test_load_protocol_reports_missing_ingestion_or_first_version_rules(monkeypatch, trial_row, row, rules, message):
    database = FakeDatabase(trial_row if row else None, rules)
    monkeypatch.setattr(store, "get_conn", database.connect)
    with pytest.raises(LookupError, match=message):
        store.load_protocol(trial_row["nct_id"])


@pytest.mark.parametrize("field, operator, value, kind, parse_status, expected", [
    ("prior_lines", "eq", 0, "inclusion", "ok", Setting.first_line),
    ("prior_lines", "le", 0, "inclusion", "ok", Setting.first_line),
    ("prior_lines", "ge", 1, "inclusion", "ok", Setting.second_line_plus),
    ("prior_lines", "gt", 0, "inclusion", "ok", Setting.second_line_plus),
    ("prior_platinum", "present", None, "inclusion", "ok", Setting.second_line_plus),
    ("prior_immunotherapy", "eq", True, "inclusion", "ok", Setting.second_line_plus),
    ("prior_lines", "eq", 0, "exclusion", "ok", Setting.any),
    ("prior_lines", "eq", 0, "inclusion", "unparseable", Setting.any),
])
def test_load_protocol_setting_uses_parseable_inclusion_rules_only(
    monkeypatch, trial_row, field, operator, value, kind, parse_status, expected,
):
    rule = CriteriaRule(rule_id="I1", field=field, operator=operator, value=value,
                        kind=kind, parse_status=parse_status, raw_text="Synthetic setting observation")
    database = FakeDatabase(trial_row, [rule.model_dump(mode="json")])
    monkeypatch.setattr(store, "get_conn", database.connect)
    assert store.load_protocol(trial_row["nct_id"]).setting == expected


def result_with(fixes, elapsed=0.25):
    return OptimizationResult(fixes, [], 7, elapsed, StubScorer.source, (42, 43, 44, 45, 46), 0,
                              ["Synthetic mocked optimizer output"])


def test_cli_fixture_runs_real_optimizer_without_any_database_or_fitted_models(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(DEMO_NCT_ID=None))
    monkeypatch.setattr(cli, "load_protocol", unexpected_call)
    monkeypatch.setattr(cli, "save_fixes", unexpected_call)
    monkeypatch.setattr(store, "get_conn", unexpected_call)
    monkeypatch.setattr("clinova.optimize.edits.load_observed_rules", unexpected_call)
    monkeypatch.setattr("clinova.optimize.search.ModelScorer", unexpected_call)
    monkeypatch.setattr("clinova.optimize.precedents.find_precedents", unexpected_call)
    output_path = tmp_path / "nested" / "fixture-report.json"
    status = cli.main(["--fixture", "--seed", "17", "--n-trials", "4", "--cohort-size", "64",
                       "--output", str(output_path)])
    report = json.loads(capsys.readouterr().out)
    assert status == 0 and report == json.loads(output_path.read_text())
    assert report["trial_id"].startswith("FIXTURE-") and report["persisted_n"] == 0
    assert report["scorer_source"] == StubScorer.source
    assert report["cohort_seeds"] == [17, 18, 19, 20, 21] and report["bootstrap_models_n"] == 0
    assert {fix["label"] for fix in report["fixes"]} == {"fastest", "lowest_dropout", "balanced"}
    assert all(not fix["precedents"] for fix in report["fixes"])
    assert any("Synthetic fixture" in note for note in report["notes"])


@pytest.mark.parametrize("explicit_trial, no_persist", [(False, False), (True, False), (True, True)])
def test_cli_uses_selected_demo_or_explicit_trial_and_honors_no_persist(
    monkeypatch, capsys, parsed_protocol, fixes, explicit_trial, no_persist,
):
    loaded, optimized, saved = [], [], []
    protocol = parsed_protocol.model_copy(update={"trial_id": "NCT00000042"})
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(DEMO_NCT_ID="NCT00000001"))
    monkeypatch.setattr(cli, "load_protocol", lambda trial_id: loaded.append(trial_id) or protocol)
    def optimize(protocol, **kwargs):
        optimized.append((protocol, kwargs))
        return result_with(fixes)
    monkeypatch.setattr(cli, "optimize_with_report", optimize)
    monkeypatch.setattr(cli, "save_fixes", lambda candidates: saved.append(candidates) or len(candidates))
    args = ["--seed", "11", "--n-trials", "5", "--cohort-size", "75"]
    if explicit_trial:
        args += ["--trial", "NCT00000042"]
    if no_persist:
        args += ["--no-persist"]
    assert cli.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert loaded == ["NCT00000042" if explicit_trial else "NCT00000001"]
    assert optimized == [(protocol, {"seed": 11, "n_trials": 5, "cohort_size": 75})]
    assert saved == ([] if no_persist else [fixes])
    assert report["persisted_n"] == (0 if no_persist else 3)


@pytest.mark.parametrize("elapsed, fix_count, benchmark, expected", [
    (9.999, 3, True, 0), (10.0, 3, True, 1), (10.001, 3, True, 1),
    (0.1, 2, True, 1), (0.1, 0, True, 1), (12.0, 3, False, 0),
])
def test_cli_benchmark_exit_status_requires_three_fixes_and_under_ten_seconds(
    monkeypatch, capsys, fixes, elapsed, fix_count, benchmark, expected,
):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(DEMO_NCT_ID=None))
    monkeypatch.setattr(cli, "load_protocol", unexpected_call)
    monkeypatch.setattr(cli, "save_fixes", unexpected_call)
    monkeypatch.setattr(cli, "optimize_with_report", lambda *args, **kwargs: result_with(fixes[:fix_count], elapsed))
    args = ["--fixture", "--no-persist"] + (["--benchmark"] if benchmark else [])
    assert cli.main(args) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["elapsed_s"] == elapsed and len(report["fixes"]) == fix_count


def test_cli_benchmark_includes_load_and_persistence_time(monkeypatch, capsys, fixes):
    times = iter((0, 10))
    monkeypatch.setattr(cli.time, "perf_counter", lambda: next(times))
    monkeypatch.setattr(cli, "optimize_with_report", lambda *_, **__: result_with(fixes, .1))
    assert cli.main(["--fixture", "--benchmark"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["elapsed_s"] == .1 and report["run_elapsed_s"] == 10


@pytest.mark.parametrize("args", [["--fixture", "--trial", "NCT00000042"], []])
def test_cli_rejects_conflicting_source_flags_or_missing_demo_before_external_calls(monkeypatch, args):
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(DEMO_NCT_ID=None))
    monkeypatch.setattr(cli, "load_protocol", unexpected_call)
    monkeypatch.setattr(cli, "optimize_with_report", unexpected_call)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
