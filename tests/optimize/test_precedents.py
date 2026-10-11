"""Offline precedent evidence checks; every trial and threshold here is synthetic."""

from datetime import date, datetime
from statistics import median

import pytest

from clinova.contracts import CriteriaRule, Edit, ParsedProtocol
from clinova.optimize import precedents
from clinova.optimize.precedents import find_precedents, widening_report

QUERY = "NCT99990000"
A, B, C, D, E, F, G = (f"NCT9999000{i}" for i in range(1, 8))


def rule(value=60, *, field="crcl_ml_min", operator="ge", kind="inclusion", rule_id="I1", **kwargs):
    return CriteriaRule(rule_id=rule_id, field=field, operator=operator, value=value, kind=kind,
                        raw_text=kwargs.pop("raw_text", f"Synthetic {field} {operator} {value}"), **kwargs)


def protocol(*rules):
    return ParsedProtocol(trial_id=QUERY, rules=list(rules))


def trial(nct_id, **kwargs):
    return {"nct_id": nct_id, "title": f"Synthetic NSCLC trial {nct_id}", "status": "COMPLETED",
            "conditions": ["Non-Small Cell Lung Cancer"], "start_date": date(2020, 1, 1),
            "planned_enrollment": 100, "actual_enrollment": 98, "history_source": "versions", **kwargs}


def stored_rule(nct_id, parsed, version=0):
    return {"nct_id": nct_id, "version_no": version, **parsed.model_dump(mode="json")}


def event(nct_id, old, new, *, version=1, when=date(2020, 7, 1), **kwargs):
    return {"nct_id": nct_id, "version_no": version, "version_date": when, "field": "eligibility",
            "change_class": "widened", "old_value": old.raw_text, "new_value": new.raw_text, **kwargs}


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class EvidenceConnection:
    """A strict read-only fake for the actual SQL surface, supporting both row styles."""

    def __init__(self, *, trials=(), rules=(), events=(), neighbors=(), mappings=True):
        self.trials, self.rules, self.events, self.neighbors = trials, rules, events, neighbors
        self.mappings = mappings
        self.queries = []
        self.closed = False

    def _rows(self, rows, columns):
        return Cursor([dict(row) if self.mappings else tuple(row[column] for column in columns) for row in rows])

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "FROM trial_embeddings e" in sql:
            rows = [{"nct_id": nct_id, "distance": distance} for nct_id, distance in self.neighbors]
            return self._rows(rows, ("nct_id", "distance"))
        ids = params[0]
        if "FROM trials WHERE" in sql:
            return self._rows([row for row in self.trials if row["nct_id"] in ids], precedents._TRIAL_COLUMNS)
        if "FROM criteria_rules WHERE" in sql:
            rows = [row for row in self.rules if row["nct_id"] in ids
                    and ("AND version_no = 0" not in sql or row["version_no"] == 0)]
            return self._rows(rows, precedents._RULE_COLUMNS)
        if "FROM trial_versions WHERE" in sql:
            return self._rows([row for row in self.events if row["nct_id"] in ids], precedents._EVENT_COLUMNS)
        pytest.fail(f"unexpected SQL: {sql}")


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def unexpected_database():
        pytest.fail("offline precedent test opened a database connection")
    monkeypatch.setattr(precedents, "get_conn", unexpected_database)


def neighbors(*pairs):
    def nearest(trial_id, k, *, exclude):
        assert trial_id == QUERY and exclude == (QUERY,) and k > 0
        return pairs
    return nearest


@pytest.mark.parametrize("mappings", [False, True])
def test_default_pgvector_query_and_completed_first_version_evidence(mappings):
    original = rule()
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, rule(45))],
                              neighbors=[(A, 0.12)], mappings=mappings)
    result = find_precedents(protocol(original), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                             conn=conn)
    assert len(result) == 1
    assert result[0].nct_id == A and result[0].distance == 0.12
    assert result[0].title == trial(A)["title"]
    assert "First registered version required CrCl >= 45" in result[0].detail
    assert "COMPLETED with 98% of planned enrollment (98/100)" in result[0].detail
    sql, params = conn.queries[0]
    assert "embedding <=> q.embedding" in sql
    assert "JOIN trials" in sql and "~*" in sql and sql.index("~*") < sql.index("LIMIT")
    assert "ORDER BY e.embedding <=> q.embedding, e.nct_id" in sql
    assert "t.status = 'COMPLETED'" in sql and sql.index("t.status") < sql.index("LIMIT")
    assert params == (QUERY, QUERY, 100)
    assert "AND version_no = 0" in conn.queries[-1][0]
    assert not conn.closed


def test_status_nsclc_first_version_and_ranking_filters_do_not_invent_precedents():
    original = rule()
    conn = EvidenceConnection(
        trials=[trial(A, status="TERMINATED"), trial(B, title="Synthetic non-small cell bladder trial",
                                                    conditions=["Non-Small Cell Bladder Cancer"]),
                trial(C), trial(D), trial(E), trial(F, planned_enrollment=0), trial(G)],
        rules=[stored_rule(A, rule(45)), stored_rule(B, rule(45)), stored_rule(C, rule(60)),
               stored_rule(C, rule(45), 1), stored_rule(D, rule(50)), stored_rule(E, rule(30)),
               stored_rule(F, rule(45)), stored_rule(G, rule(45))],
    )
    result = find_precedents(protocol(original), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                             conn=conn, k=2, nearest_fn=neighbors((E, .4), (C, .1), (B, .02), (D, .2),
                                                               (F, .3), (A, .01), (G, .5)))
    assert [p.nct_id for p in result] == [F, E]
    assert "planned enrollment" not in result[0].detail


def test_looser_single_rule_is_insufficient_when_another_same_field_rule_tightens_it():
    conn = EvidenceConnection(
        trials=[trial(A), trial(B), trial(C)],
        rules=[stored_rule(A, rule(45)), stored_rule(A, rule(50, operator="le", rule_id="I2")),
               stored_rule(B, rule(45)), stored_rule(C, rule(45)),
               stored_rule(C, rule(field="other", operator="eq", value=None, rule_id="I2",
                                   parse_status="unparseable"))],
    )
    result = find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                             conn=conn, nearest_fn=neighbors((A, .1), (C, .2), (B, .3)))
    assert [p.nct_id for p in result] == [B]


@pytest.mark.parametrize("original, observed, edit", [
    (rule(60, operator="gt"), rule(60), Edit(kind="relax_rule", target_id="I1", new_operator="ge", new_value=60)),
    (rule(1, field="ecog", operator="le"), rule(2, field="ecog", operator="le"),
     Edit(kind="relax_rule", target_id="I1", new_value=2)),
    (rule("none", field="brain_mets", operator="ne", kind="exclusion"),
     rule("untreated", field="brain_mets", operator="eq", kind="exclusion"),
     Edit(kind="relax_rule", target_id="I1", new_operator="eq", new_value="untreated")),
    (rule(["adenocarcinoma"], field="histology", operator="in"),
     rule(["squamous", "adenocarcinoma"], field="histology", operator="in"),
     Edit(kind="relax_rule", target_id="I1", new_value=["adenocarcinoma", "squamous"])),
    (rule(None, field="autoimmune_disease", operator="present", kind="exclusion"),
     rule([], field="autoimmune_disease", operator="in", kind="exclusion"),
     Edit(kind="relax_rule", target_id="I1", new_operator="in", new_value=[])),
])
def test_numeric_boundaries_ordinals_membership_and_exclusion_semantics(original, observed, edit):
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, observed)])
    result = find_precedents(protocol(original), [edit], conn=conn, nearest_fn=neighbors((A, .1)))
    assert [p.nct_id for p in result] == [A]


@pytest.mark.parametrize("observed", [rule(4), rule(251), rule(45, unit="mL/sec"),
                                      rule(True), rule(float("nan")), rule(45, parse_status="unparseable")])
def test_invalid_stored_thresholds_never_support_a_precedent(observed):
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, observed)])
    assert find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                           conn=conn, nearest_fn=neighbors((A, .1))) == []


def test_corrupt_rule_row_cannot_disappear_and_make_a_trial_look_unconstrained():
    corrupt = stored_rule(A, rule(45, rule_id="I2")) | {"operator": "invalid"}
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, rule(45)), corrupt])
    assert find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                           conn=conn, nearest_fn=neighbors((A, .1))) == []


def test_unknown_protocol_fragments_prevent_claiming_complete_field_containment():
    unknown = rule(None, field="other", operator="eq", rule_id="I2", parse_status="unparseable")
    assert find_precedents(protocol(rule(), unknown),
                           [Edit(kind="relax_rule", target_id="I1", new_value=45)]) == []


@pytest.mark.parametrize("edits", [[], [Edit(kind="drop_rule", target_id="I1")],
                                     [Edit(kind="make_remote", target_id="1")],
                                     [Edit(kind="relax_rule", target_id="missing", new_value=45)],
                                     [Edit(kind="relax_rule", target_id="I1", new_value=75)],
                                     [Edit(kind="relax_rule", target_id="I1", new_value=4)]])
def test_unsupported_or_nonrelaxing_edits_do_not_query_the_database(edits):
    assert find_precedents(protocol(rule()), edits) == []


def test_distance_limit_self_exclusion_deduplication_and_missing_embedding():
    conn = EvidenceConnection(trials=[trial(A), trial(B), trial(C)],
                              rules=[stored_rule(nct, rule(45)) for nct in (A, B, C)])
    result = find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                             conn=conn, comparable_k=2, max_distance=.2,
                             nearest_fn=neighbors((QUERY, 0), (A, .2), (A, .1), (B, .15), (C, .3),
                                                  (D, -1), (E, float("nan")), (F, None)))
    assert [(p.nct_id, p.distance) for p in result] == [(A, .1), (B, .15)]
    empty = EvidenceConnection()
    assert find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)],
                           conn=empty) == []
    assert len(empty.queries) == 1


def test_widening_denominator_exact_original_and_earliest_field_expansion_months():
    old, loose, looser = rule(), rule(45), rule(30)
    ecog1, ecog2 = rule(1, field="ecog", operator="le", rule_id="I2"), rule(2, field="ecog", operator="le", rule_id="I2")
    conn = EvidenceConnection(
        trials=[trial(A), trial(B, status="TERMINATED"), trial(C), trial(D), trial(E), trial(F)],
        rules=[stored_rule(A, old), stored_rule(A, loose, 1), stored_rule(A, looser, 2),
               stored_rule(B, old), stored_rule(B, loose, 1),
               stored_rule(C, old), stored_rule(C, ecog1), stored_rule(C, old, 1), stored_rule(C, ecog2, 1),
               stored_rule(D, rule(50)), stored_rule(E, rule(60, operator="gt")),
               stored_rule(F, rule(60, kind="exclusion"))],
        events=[event(A, loose, looser, version=2, when="2021-01-01"),
                event(A, old, loose, when=date(2020, 7, 1)),
                event(B, old, loose, when=datetime(2021, 1, 1)),
                event(C, old, old, change_class="mixed", old_value=old.raw_text + ecog1.raw_text,
                      new_value=old.raw_text + ecog2.raw_text)],
    )
    report = widening_report(protocol(old), old, conn=conn,
                             nearest_fn=neighbors((A, .1), (B, .2), (C, .3), (D, .4), (E, .5), (F, .6)))
    assert report.comparable_n == 6 and report.original_threshold_n == 3
    assert report.denominator_nct_ids == [A, B, C]
    assert report.widened_n == 2 and report.widened_share == 2 / 3
    assert report.widened_nct_ids == [A, B]
    assert report.median_months_after_start == pytest.approx(median([182, 366]) / (365.25 / 12))
    assert report.unresolved_history_n == 0
    assert report.model_dump(mode="json")["operator"] == "ge"
    assert any("FROM trial_versions" in sql and "change_class IN ('widened', 'mixed')" in sql
               for sql, _ in conn.queries)


def test_widening_membership_threshold_matches_ignore_set_order():
    original = rule(["adenocarcinoma", "squamous"], field="histology", operator="in")
    reordered = rule(["squamous", "adenocarcinoma"], field="histology", operator="in")
    wider = rule(["squamous", "adenocarcinoma", "large_cell"], field="histology", operator="in")
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, reordered), stored_rule(A, wider, 1)],
                              events=[event(A, reordered, wider)])
    report = widening_report(protocol(original), original, conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.original_threshold_n == report.widened_n == 1


def test_widening_default_pgvector_pool_includes_all_outcomes_and_supports_tuple_rows():
    old, new = rule(), rule(45)
    conn = EvidenceConnection(trials=[trial(A, status="TERMINATED")],
                              rules=[stored_rule(A, old), stored_rule(A, new, 1)],
                              events=[event(A, old, new)], neighbors=[(A, .1)], mappings=False)
    report = widening_report(protocol(old), old, conn=conn)
    assert report.original_threshold_n == report.widened_n == 1
    assert "t.status = 'COMPLETED'" not in conn.queries[0][0]


@pytest.mark.parametrize("first, before, after", [(60, 30, 45), (60, 75, 60), (60, 60, 75)])
def test_widening_must_strictly_expand_both_first_and_previous_field_constraints(first, before, after):
    original, previous, current = rule(first), rule(before), rule(after)
    conn = EvidenceConnection(trials=[trial(A)],
                              rules=[stored_rule(A, original), stored_rule(A, previous, 1),
                                     stored_rule(A, current, 2)],
                              events=[event(A, previous, current, version=2, change_class="mixed")])
    report = widening_report(protocol(original), original, conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.original_threshold_n == 1 and report.widened_n == 0


@pytest.mark.parametrize("event_kwargs", [
    {"when": date(2020, 1, 1)}, {"when": date(2019, 12, 1)}, {"version": 0},
    {"field": "enrollment_count"}, {"change_class": "administrative"}, {"change_class": "narrowed"},
])
def test_events_at_or_before_start_or_on_other_fields_do_not_count(event_kwargs):
    old, new = rule(), rule(45)
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, old), stored_rule(A, new, 1)],
                              events=[event(A, old, new, **event_kwargs)])
    report = widening_report(protocol(old), old, conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.widened_n == 0 and report.widened_share == 0
    assert report.median_months_after_start is None


@pytest.mark.parametrize("failure", ["missing_after", "missing_before", "snapshot_mismatch", "missing_snapshot",
                                    "removed_field", "unparseable", "missing_event_date", "missing_start_date"])
def test_incomplete_or_misaligned_history_is_explicit_and_never_credited(failure):
    old, new = rule(), rule(45)
    rows = [stored_rule(A, old), stored_rule(A, new, 1)]
    events = [event(A, old, new)]
    trial_row = trial(A)
    if failure == "missing_after":
        rows.pop()
    elif failure == "missing_before":
        rows[-1]["version_no"] = 2
        events[0]["version_no"] = 2
    elif failure == "snapshot_mismatch":
        events[0]["new_value"] = old.raw_text
    elif failure == "missing_snapshot":
        events[0]["new_value"] = None
    elif failure == "removed_field":
        # A current snapshot stored at v1 removed CrCl, but actual history v1
        # only widened ECOG. Empty field fragments must not match vacuously.
        ecog = rule(2, field="ecog", operator="le")
        rows[-1] = stored_rule(A, ecog, 1)
        events[0]["new_value"] = old.raw_text + ecog.raw_text
    elif failure == "unparseable":
        rows.append(stored_rule(A, rule(None, field="other", operator="eq", rule_id="I2",
                                        parse_status="unparseable"), 1))
    elif failure == "missing_event_date":
        events[0]["version_date"] = "invalid-date"
    else:
        trial_row["start_date"] = None
    conn = EvidenceConnection(trials=[trial_row], rules=rows, events=events)
    report = widening_report(protocol(old), old, conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.widened_n == 0 and report.widened_share == 0
    assert report.unresolved_history_n == 1
    assert report.median_months_after_start is None


def test_incomplete_history_without_events_does_not_imply_no_amendment():
    original = rule()
    conn = EvidenceConnection(trials=[trial(A, history_source="earliest_vs_current"), trial(B)],
                              rules=[stored_rule(A, original), stored_rule(B, original)])
    report = widening_report(protocol(original), original, conn=conn, nearest_fn=neighbors((A, .1), (B, .2)))
    assert report.original_threshold_n == 2 and report.widened_share == 0
    assert report.unresolved_history_n == 1
    assert "missing history is not evidence of no amendment" in " ".join(report.notes)


def test_partial_history_with_verified_widening_is_not_an_unresolved_nonwidened_trial():
    original, wider = rule(), rule(45)
    conn = EvidenceConnection(trials=[trial(A, history_source="earliest_vs_current")],
                              rules=[stored_rule(A, original), stored_rule(A, wider, 1)],
                              events=[event(A, original, wider)])
    report = widening_report(protocol(original), original, conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.widened_n == 1 and report.unresolved_history_n == 0


def test_no_exact_threshold_produces_unavailable_share_and_median():
    conn = EvidenceConnection(trials=[trial(A)], rules=[stored_rule(A, rule(50))])
    report = widening_report(protocol(rule()), rule(), conn=conn, nearest_fn=neighbors((A, .1)))
    assert report.comparable_n == 1 and report.original_threshold_n == 0
    assert report.widened_share is report.median_months_after_start is None
    assert report.denominator_nct_ids == report.widened_nct_ids == []


@pytest.mark.parametrize("kwargs", [{"comparable_k": 0}, {"max_distance": -1},
                                    {"max_distance": float("nan")}, {"max_distance": float("inf")}])
def test_invalid_neighbor_options_are_rejected_before_loading_evidence(kwargs):
    with pytest.raises(ValueError):
        find_precedents(protocol(rule()), [], **kwargs)
    with pytest.raises(ValueError):
        widening_report(protocol(rule()), rule(), **kwargs)


def test_invalid_rule_and_result_limit_are_rejected_without_database_access():
    with pytest.raises(ValueError, match="valid parseable rule"):
        widening_report(protocol(rule()), rule(4))
    with pytest.raises(ValueError, match="k must be nonnegative"):
        find_precedents(protocol(rule()), [], k=-1)
    assert find_precedents(protocol(rule()), [Edit(kind="relax_rule", target_id="I1", new_value=45)], k=0) == []
