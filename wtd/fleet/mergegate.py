"""The merge gate: decide, deterministically, whether a pull request may merge.

The fleet's house rule is that agents propose and humans dispose. This
module is the narrow, auditable exception: a pull request may be merged
only when a *deterministic* gate agrees with the reviewer agent's verdict.
The agent supplies judgement (``merge_pr``); this module supplies the
facts — CI is green on the exact head the reviewer read, the branch is
mergeable, nobody has requested changes, the repository opted in.

Nothing here does I/O and nothing here has a clock: the caller fetches the
GitHub payloads (``GitHubClient.merge_inputs``) and passes them in, so the
whole decision is unit-testable and every refusal carries its reason.

Blockers are *collected*, never short-circuited: an operator asking "why
didn't this merge?" should get the whole answer in one line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: Check-run conclusions that are not failures. ``skipped``/``neutral`` are
#: how conditional jobs and advisory checks report "nothing to say".
PASSING_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})

#: Commit-status states that count as passing.
PASSING_STATUS_STATES = frozenset({"success"})

#: ``mergeable_state`` values GitHub reports for a branch that cannot merge
#: as-is, mapped to the reason we show.
BLOCKING_MERGE_STATES = {
    "dirty": "the branch has merge conflicts with its base",
    "blocked": "branch protection is not satisfied (required reviews or checks)",
    "behind": "the head branch is behind its base and must be updated",
}

DEFAULT_BLOCKED_LABELS = ("do-not-merge", "wip", "hold", "blocked")

#: Merge methods the GitHub API accepts.
MERGE_METHODS = ("squash", "merge", "rebase")

#: Work-item evidence keys carrying a standing review approval: the head
#: commit the reviewer approved, and why. Written by the dispatcher when an
#: agent requests a merge, read by the daily merge sweep afterwards — that
#: hand-off is what lets "CI went green overnight" merge without a second
#: model call, while still refusing once the branch moves.
APPROVAL_SHA_KEY = "merge_approved_sha"
APPROVAL_REASON_KEY = "merge_rationale"


@dataclass(frozen=True)
class MergePolicy:
    """What a repository permits the fleet to merge, and how.

    Every field defaults to the conservative reading. ``enabled`` is off,
    so a fleet that never configures merging can never perform one, and
    ``allow_fleet_authored`` is off, so the fleet does not merge its own
    work unless a human writes that down for that repository.
    """

    enabled: bool = False
    method: str = "squash"
    #: Require at least one completed check/status, all of them passing.
    require_checks: bool = True
    #: Require a standing review approval from the fleet's reviewer.
    require_review_approval: bool = True
    #: Permit merging pull requests the fleet itself opened.
    allow_fleet_authored: bool = False
    blocked_labels: tuple[str, ...] = DEFAULT_BLOCKED_LABELS
    #: Cap on merges per sweep, so a bad day cannot land a hundred PRs.
    max_per_cycle: int = 2

    def normalized_method(self) -> str:
        return self.method if self.method in MERGE_METHODS else "squash"


@dataclass(frozen=True)
class CiSummary:
    """The CI verdict on one commit, from check runs plus commit statuses."""

    total: int = 0
    passed: int = 0
    failing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()

    @property
    def green(self) -> bool:
        return self.total > 0 and not self.failing and not self.pending

    def describe(self) -> str:
        if self.total == 0:
            return "no checks reported"
        if self.failing:
            return f"{len(self.failing)} failing ({', '.join(self.failing[:3])})"
        if self.pending:
            return f"{len(self.pending)} pending ({', '.join(self.pending[:3])})"
        return f"{self.passed}/{self.total} passing"


@dataclass
class MergeDecision:
    """The gate's answer for one pull request."""

    repo: str
    number: int
    allowed: bool
    blockers: list[str] = field(default_factory=list)
    ci: CiSummary = field(default_factory=CiSummary)
    head_sha: str = ""
    method: str = "squash"
    url: str | None = None
    title: str = ""

    @property
    def reason(self) -> str:
        return "; ".join(self.blockers) if self.blockers else "all merge conditions met"


def summarize_ci(
    check_runs: Iterable[dict[str, Any]] | None,
    combined_status: dict[str, Any] | None = None,
) -> CiSummary:
    """Fold check runs and commit statuses into one verdict.

    A check run that has not completed is *pending*, not passing — "green"
    means every signal on the commit has finished and none of them failed.
    A commit with no signal at all is not green either; ``require_checks``
    decides whether that blocks.
    """
    failing: list[str] = []
    pending: list[str] = []
    passed = 0
    total = 0

    for run in check_runs or []:
        name = str(run.get("name") or "check")
        total += 1
        if str(run.get("status")) != "completed":
            pending.append(name)
            continue
        if str(run.get("conclusion")) in PASSING_CONCLUSIONS:
            passed += 1
        else:
            failing.append(f"{name}: {run.get('conclusion')}")

    for status in (combined_status or {}).get("statuses", []) or []:
        context = str(status.get("context") or "status")
        state = str(status.get("state"))
        total += 1
        if state in PASSING_STATUS_STATES:
            passed += 1
        elif state == "pending":
            pending.append(context)
        else:
            failing.append(f"{context}: {state}")

    return CiSummary(
        total=total, passed=passed, failing=tuple(failing), pending=tuple(pending)
    )


def latest_review_states(reviews: Sequence[dict[str, Any]] | None) -> dict[str, str]:
    """The most recent decisive review state per reviewer login.

    ``COMMENTED`` reviews are not decisive — a reviewer who comments after
    approving has not withdrawn the approval.
    """
    decisive = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    states: dict[str, str] = {}
    for review in reviews or []:
        state = str(review.get("state", "")).upper()
        if state not in decisive:
            continue
        login = str((review.get("user") or {}).get("login", "")) or "unknown"
        states[login] = state
    return states


def evaluate_merge(
    pull: dict[str, Any],
    *,
    policy: MergePolicy,
    ci: CiSummary,
    reviews: Sequence[dict[str, Any]] | None = None,
    approved_sha: str | None = None,
    fleet_authored: bool = False,
    repo: str = "",
) -> MergeDecision:
    """Decide whether ``pull`` may be merged, collecting every blocker.

    ``approved_sha`` is the head commit the fleet's reviewer actually read.
    Pinning the approval to a commit is what keeps "reviewed and green"
    honest: a push after the review invalidates it rather than riding it.
    """
    head_sha = str((pull.get("head") or {}).get("sha", ""))
    number = int(pull.get("number") or 0)
    decision = MergeDecision(
        repo=repo or str(((pull.get("base") or {}).get("repo") or {}).get("full_name", "")),
        number=number,
        allowed=False,
        ci=ci,
        head_sha=head_sha,
        method=policy.normalized_method(),
        url=pull.get("html_url"),
        title=str(pull.get("title", "")),
    )
    blockers = decision.blockers

    if not policy.enabled:
        blockers.append("merging is not enabled for this repository")

    state = str(pull.get("state", "open"))
    if state != "open":
        blockers.append(f"pull request is {state}")
    if pull.get("merged"):
        blockers.append("pull request is already merged")
    if pull.get("draft"):
        blockers.append("pull request is a draft — mark it ready for review first")

    if fleet_authored and not policy.allow_fleet_authored:
        blockers.append(
            "the fleet opened this pull request and allow_fleet_authored is off"
        )

    labels = {str(lbl.get("name", "")).lower() for lbl in pull.get("labels") or []}
    blocked = sorted(labels & {label.lower() for label in policy.blocked_labels})
    if blocked:
        blockers.append(f"blocked by label(s): {', '.join(blocked)}")

    mergeable = pull.get("mergeable")
    if mergeable is False:
        blockers.append("GitHub reports the branch is not mergeable (conflicts)")
    elif mergeable is None:
        blockers.append("GitHub has not computed mergeability yet — retry next sweep")

    merge_state = str(pull.get("mergeable_state", "")).lower()
    if merge_state in BLOCKING_MERGE_STATES:
        blockers.append(BLOCKING_MERGE_STATES[merge_state])

    if policy.require_checks and not ci.green:
        blockers.append(f"CI is not green: {ci.describe()}")

    for login, review_state in sorted(latest_review_states(reviews).items()):
        if review_state == "CHANGES_REQUESTED":
            blockers.append(f"changes requested by {login}")

    if policy.require_review_approval:
        if not approved_sha:
            blockers.append("no standing fleet review approval for this pull request")
        elif head_sha and approved_sha != head_sha:
            blockers.append(
                f"head moved since the review (approved {approved_sha[:8]}, "
                f"head {head_sha[:8]})"
            )

    decision.allowed = not blockers
    return decision
