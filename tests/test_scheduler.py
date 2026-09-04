"""Tests for the pure cycle scheduler."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from wtd.core.models import TodoPriority
from wtd.fleet.models import WorkItem, WorkKind, make_dedup_key
from wtd.fleet.roles import builtin_roles
from wtd.fleet.scheduler import (
    permitted_roles,
    plan_cycle,
    unknown_role_names,
)

_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def item(
    repo: str,
    kind: WorkKind,
    anchor: str,
    *,
    priority: TodoPriority = TodoPriority.MEDIUM,
    minutes: int = 0,
    role_hint: str | None = None,
) -> WorkItem:
    return WorkItem(
        dedup_key=make_dedup_key(repo, kind, anchor),
        kind=kind,
        repo=repo,
        title=f"{kind.value} {anchor}",
        priority=priority,
        role_hint=role_hint,
        created_at=_T0 + timedelta(minutes=minutes),
    )


def test_priority_bands_come_first():
    roles = builtin_roles()
    items = [
        item("a/r", WorkKind.WRITE_DOCS, "1", priority=TodoPriority.LOW),
        item("a/r", WorkKind.FIX_BUG, "2", priority=TodoPriority.HIGH),
        item("a/r", WorkKind.TRIAGE_ISSUE, "3", priority=TodoPriority.MEDIUM),
    ]
    plan = plan_cycle(items, roles, max_runs=10)
    kinds = [a.item.kind for a in plan.assignments]
    assert kinds == [WorkKind.FIX_BUG, WorkKind.TRIAGE_ISSUE, WorkKind.WRITE_DOCS]


def test_repos_interleave_within_a_band():
    roles = builtin_roles()
    items = [
        item("busy/repo", WorkKind.TRIAGE_ISSUE, f"i{n}", minutes=n) for n in range(4)
    ] + [item("quiet/repo", WorkKind.TRIAGE_ISSUE, "q1", minutes=99)]
    plan = plan_cycle(items, roles, max_runs=2)
    repos = [a.item.repo for a in plan.assignments]
    # Fairness: the quiet repo gets one of the two slots despite being newest.
    assert repos == ["busy/repo", "quiet/repo"]


def test_role_hint_respected_and_fallback_resolution():
    roles = builtin_roles()
    hinted = item("a/r", WorkKind.FIX_BUG, "1", role_hint="bug-hunter")
    unhinted = item("a/r", WorkKind.REVIEW_PR, "2")
    plan = plan_cycle([hinted, unhinted], roles, max_runs=10)
    by_key = {a.item.dedup_key: a.role.name for a in plan.assignments}
    assert by_key[hinted.dedup_key] == "bug-hunter"
    assert by_key[unhinted.dedup_key] == "reviewer"


def test_invalid_hint_falls_back_to_kind_owner():
    roles = builtin_roles()
    bad_hint = item("a/r", WorkKind.REVIEW_PR, "1", role_hint="does-not-exist")
    plan = plan_cycle([bad_hint], roles, max_runs=10)
    assert plan.assignments[0].role.name == "reviewer"


def test_unroutable_items_reported():
    roles = builtin_roles()
    del roles["reviewer"]
    orphan = item("a/r", WorkKind.REVIEW_PR, "1")
    plan = plan_cycle([orphan], roles, max_runs=10)
    assert plan.assignments == []
    assert plan.unroutable == [orphan]


def test_max_runs_produces_overflow():
    roles = builtin_roles()
    items = [item("a/r", WorkKind.TRIAGE_ISSUE, f"i{n}", minutes=n) for n in range(5)]
    plan = plan_cycle(items, roles, max_runs=2)
    assert len(plan.assignments) == 2
    assert len(plan.overflow) == 3


def test_repo_and_role_filters():
    roles = builtin_roles()
    items = [
        item("a/r", WorkKind.TRIAGE_ISSUE, "1"),
        item("b/r", WorkKind.TRIAGE_ISSUE, "2"),
        item("a/r", WorkKind.REVIEW_PR, "3"),
    ]
    plan = plan_cycle(items, roles, max_runs=10, repos=["a/r"], role_names=["triage"])
    assert [a.item.repo for a in plan.assignments] == ["a/r"]
    assert [a.role.name for a in plan.assignments] == ["triage"]
    # The review_pr item in a/r is excluded by the role filter → unroutable.
    assert len(plan.unroutable) == 1


def test_older_items_first_within_same_priority_and_repo():
    roles = builtin_roles()
    newer = item("a/r", WorkKind.TRIAGE_ISSUE, "new", minutes=60)
    older = item("a/r", WorkKind.TRIAGE_ISSUE, "old", minutes=0)
    plan = plan_cycle([newer, older], roles, max_runs=10)
    assert plan.assignments[0].item.dedup_key == older.dedup_key


# ----------------------------------------------------------------------
# Per-repo role allowlists (wtd.yml `roles:` on a roster entry)
# ----------------------------------------------------------------------
def test_repo_allowlist_keeps_other_roles_out():
    # An operator who writes `roles: [triage]` means it: no reviewer, no
    # doc-writer, no cost and no blast radius from either.
    roles = builtin_roles()
    items = [
        item("a/r", WorkKind.TRIAGE_ISSUE, "1"),
        item("a/r", WorkKind.REVIEW_PR, "2"),
    ]
    plan = plan_cycle(items, roles, max_runs=10, repo_roles={"a/r": ["triage"]})
    assert [a.role.name for a in plan.assignments] == ["triage"]
    assert [i.kind for i in plan.unroutable] == [WorkKind.REVIEW_PR]


def test_empty_or_absent_allowlist_means_every_role():
    roles = builtin_roles()
    items = [
        item("a/r", WorkKind.TRIAGE_ISSUE, "1"),
        item("a/r", WorkKind.REVIEW_PR, "2"),
    ]
    for repo_roles in (None, {}, {"a/r": []}, {"other/repo": ["triage"]}):
        plan = plan_cycle(items, roles, max_runs=10, repo_roles=repo_roles)
        assert len(plan.assignments) == 2, repo_roles
        assert plan.unroutable == [], repo_roles


def test_allowlist_is_per_repo_not_global():
    roles = builtin_roles()
    items = [
        item("narrow/repo", WorkKind.REVIEW_PR, "1"),
        item("open/repo", WorkKind.REVIEW_PR, "2"),
    ]
    plan = plan_cycle(
        items, roles, max_runs=10, repo_roles={"narrow/repo": ["triage"]}
    )
    assert [a.item.repo for a in plan.assignments] == ["open/repo"]
    assert [i.repo for i in plan.unroutable] == ["narrow/repo"]


def test_role_filter_narrows_further_but_cannot_widen():
    roles = builtin_roles()
    items = [
        item("a/r", WorkKind.TRIAGE_ISSUE, "1"),
        item("a/r", WorkKind.REVIEW_PR, "2"),
    ]
    repo_roles = {"a/r": ["triage"]}
    # --role reviewer asks for work the roster forbids: nothing runs.
    plan = plan_cycle(
        items, roles, max_runs=10, role_names=["reviewer"], repo_roles=repo_roles
    )
    assert plan.assignments == []
    assert len(plan.unroutable) == 2


def test_hint_resolves_within_the_permitted_set():
    # The hint names a forbidden role, but another permitted role handles
    # the kind — the work is routed, not stranded.
    roles = builtin_roles()
    roles["deputy"] = replace(roles["bug-hunter"], name="deputy")
    hinted = item("a/r", WorkKind.FIX_BUG, "1", role_hint="bug-hunter")
    plan = plan_cycle([hinted], roles, max_runs=10, repo_roles={"a/r": ["deputy"]})
    assert [a.role.name for a in plan.assignments] == ["deputy"]


def test_unknown_role_name_strands_the_repo_rather_than_widening_it():
    # A typo must never fail open into "run everything".
    roles = builtin_roles()
    items = [item("a/r", WorkKind.TRIAGE_ISSUE, "1")]
    plan = plan_cycle(items, roles, max_runs=10, repo_roles={"a/r": ["triaje"]})
    assert plan.assignments == []
    assert plan.unroutable == items


def test_unknown_role_names_reports_typos_per_repo():
    roles = builtin_roles()
    found = unknown_role_names(
        roles,
        {
            "a/r": ["triage", "triaje"],
            "b/r": ["reviewer"],
            "c/r": ["nope", "also-nope"],
        },
    )
    assert found == {"a/r": ["triaje"], "c/r": ["also-nope", "nope"]}
    assert unknown_role_names(roles, None) == {}


def test_permitted_roles_narrows_only_when_asked():
    roles = builtin_roles()
    assert permitted_roles(roles, None) is roles
    assert permitted_roles(roles, []) is roles
    assert set(permitted_roles(roles, ["triage", "reviewer"])) == {"triage", "reviewer"}
