"""
WTD – What To Do: The Ultimate Recursive TODO Engine

Turn every TODO into a self-replicating, AI-orchestrated action factory.
"""

__version__ = "0.1.0"
__author__ = "WTD Team"

from wtd.core.tree import TodoTree, TodoNode
from wtd.core.scanner import TodoScanner
from wtd.core.agent import WTDAgent

__all__ = ["TodoTree", "TodoNode", "TodoScanner", "WTDAgent", "__version__"]

