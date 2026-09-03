"""Docs drift: is the documentation still describing the code?"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wtd.core.models import TodoPriority
from wtd.fleet.docsdrift import (
    DocsPolicy,
    DocsSignals,
    assess_docs,
    daily_anchor,
    utc_day,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def signals(**overrides) -> DocsSignals:
    base = {
        "readme_chars": 5000,
        "last_code_commit": NOW,
        "last_docs_commit": NOW - timedelta(days=1),
        "commits_since_docs": 1,
        "doc_paths_present": ("README.md", "docs"),
    }
    base.update(overrides)
    return DocsSignals(**base)


class TestAssessDocs:
    def test_fresh_docs_need_nothing(self):
        assessment = assess_docs("o/r", signals())
        assert not assessment.needs_update
        assert assessment.summary == "documentation looks current"

    def test_missing_readme_is_high_priority(self):
        assessment = assess_docs("o/r", signals(readme_chars=None))
        assert assessment.needs_update
        assert assessment.priority == TodoPriority.HIGH
        assert "no README" in assessment.summary

    def test_thin_readme_still_fires(self):
        assessment = assess_docs("o/r", signals(readme_chars=120))
        assert assessment.needs_update
        assert "120 characters" in assessment.summary

    def test_stale_docs_need_both_time_and_commits(self):
        old = signals(
            last_docs_commit=NOW - timedelta(days=40), commits_since_docs=30
        )
        assert assess_docs("o/r", old).needs_update

        # 40 quiet days with two commits is not drift — it is a calm repo.
        quiet = signals(last_docs_commit=NOW - timedelta(days=40), commits_since_docs=2)
        assert not assess_docs("o/r", quiet).needs_update

        # 30 commits inside a week is busy, but the docs kept up.
        busy = signals(last_docs_commit=NOW - timedelta(days=3), commits_since_docs=30)
        assert not assess_docs("o/r", busy).needs_update

    def test_thresholds_are_configurable(self):
        drifted = signals(last_docs_commit=NOW - timedelta(days=5), commits_since_docs=6)
        assert not assess_docs("o/r", drifted).needs_update
        strict = DocsPolicy(stale_after_days=3, min_commits_since_docs=2)
        assert assess_docs("o/r", drifted, strict).needs_update

    def test_no_documentation_commit_at_all_is_drift(self):
        assessment = assess_docs("o/r", signals(last_docs_commit=None))
        assert assessment.needs_update
        assert "no commit" in assessment.summary

    def test_reasons_accumulate(self):
        assessment = assess_docs(
            "o/r",
            signals(
                readme_chars=50,
                last_docs_commit=NOW - timedelta(days=90),
                commits_since_docs=50,
            ),
        )
        assert len(assessment.reasons) == 2
        assert assessment.drift_days == 90.0

    def test_drift_is_never_negative(self):
        # Docs newer than the newest code commit is normal (a docs-only push).
        assessment = assess_docs(
            "o/r", signals(last_docs_commit=NOW + timedelta(days=2))
        )
        assert assessment.drift_days == 0.0
        assert not assessment.needs_update


class TestDailyAnchor:
    def test_anchor_changes_with_the_day_and_repo(self):
        assert daily_anchor("o/r", "2026-09-03") != daily_anchor("o/r", "2026-09-04")
        assert daily_anchor("o/r", "2026-09-03") != daily_anchor("o/s", "2026-09-03")

    def test_same_day_same_anchor(self):
        assert daily_anchor("o/r", "2026-09-03") == daily_anchor("o/r", "2026-09-03")

    def test_utc_day_formats_the_clock_it_is_given(self):
        assert utc_day(NOW) == "2026-09-03"
