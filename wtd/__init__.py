"""
WTD – What To Do: an autonomous AI agent fleet platform.

Every TODO — a code comment, a GitHub issue, a failing workflow, a missing
README — becomes queued work for a fleet of Claude-powered agents that
discover it, act on it, and discover more. Claude Code OAuth is the default
lane; the Anthropic API is the fallback.
"""

__version__ = "0.2.0"
__author__ = "WTD Team"

from wtd.core.agent import WTDAgent
from wtd.core.scanner import TodoScanner
from wtd.core.tree import TodoNode, TodoTree

__all__ = ["TodoTree", "TodoNode", "TodoScanner", "WTDAgent", "__version__"]

