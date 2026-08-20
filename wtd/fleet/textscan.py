"""Read what a workflow *does*, not what it talks about.

Naive pattern matching over a workflow file cannot tell these apart::

    run: gh pr merge --squash --auto          # the lane merges
    prompt: "**Never merge a pull request.**  # the lane is forbidden to
             Not with `gh pr merge`, not …"   #   merge — the opposite
    run: grep -nE 'gh pr merge' …             # the lane *detects* merges

All three contain the string ``gh pr merge``. Treating them alike produced
three false "this agent merges its own PRs" findings out of four on the
first run of this audit — including one against a repository whose own
documentation says, correctly, that no tier ever merges.

This module narrows the haystack to lines that plausibly *execute*
something, dropping comments, prose prohibitions, and detector patterns.
The technique is borrowed from gitorio's Fleet Ops engine, which reads
command lines through an equivalent comment-stripper for the same reason.

The result is deliberately conservative: it prefers missing a real
behaviour to inventing one, because a false accusation costs a maintainer
more than a missed nit.
"""

from __future__ import annotations

import re

#: Prose that negates a capability rather than exercising it.
_NEGATION = re.compile(
    r"\b(never|do not|don'?t|must not|cannot|can'?t|no tier|forbidden|refuse|"
    r"without)\b",
    re.I,
)

#: The line is searching for a pattern, not running it.
_DETECTOR = re.compile(r"\b(grep|rg|ripgrep|egrep|awk|sed)\b|--allowedTools\s+['\"]?\(")

#: Markdown emphasis/bullets are a strong hint the line is prompt prose.
_PROSE_HINT = re.compile(r"^\s*(?:[-*+]\s+)?\*\*|^\s*\d+\.\s+\[|^\s*>")


def strip_comments(text: str) -> str:
    """Drop YAML comment lines and trailing ``#`` comments.

    Only strips a trailing ``#`` when it is preceded by whitespace and is
    not inside an obvious quoted string, so shell constructs such as
    ``${{ }}`` and ``$#`` survive.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if line.count('"') % 2 == 0 and line.count("'") % 2 == 0:
            line = re.sub(r"\s+#(?!\{).*$", "", line)
        out.append(line)
    return "\n".join(out)


def command_lines(text: str) -> list[str]:
    """Lines that plausibly execute or grant a capability.

    Excludes comments, negating prose, and detector patterns. Tool grants
    (``--allowedTools "Bash(gh pr merge:*)"``) are kept: granting a
    capability to an agent is a real conferral of that capability.
    """
    kept: list[str] = []
    for line in strip_comments(text).splitlines():
        if not line.strip():
            continue
        if _NEGATION.search(line):
            continue
        if _DETECTOR.search(line) and "allowedTools" not in line:
            continue
        if _PROSE_HINT.search(line) and "allowedTools" not in line:
            continue
        kept.append(line)
    return kept


def executes(text: str, pattern: re.Pattern[str]) -> bool:
    """True when ``pattern`` matches a line that actually does something."""
    return any(pattern.search(line) for line in command_lines(text))


def find_evidence(text: str, pattern: re.Pattern[str], limit: int = 3) -> list[str]:
    """The matching command lines, trimmed — so a finding can cite itself."""
    hits = [line.strip()[:160] for line in command_lines(text) if pattern.search(line)]
    return hits[:limit]
