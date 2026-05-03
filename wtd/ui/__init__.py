"""
WTD UI Module - Terminal dashboard and rich output
"""

from wtd.ui.dashboard import Dashboard
from wtd.ui.output import Console, print_scan_result, print_todo_tree
from wtd.ui.routines_output import (
    print_all_routines,
    print_due_routines,
    print_routine_summary,
    print_routines_needing_review,
)

__all__ = [
    "Dashboard",
    "Console",
    "print_todo_tree",
    "print_scan_result",
    "print_routine_summary",
    "print_due_routines",
    "print_routines_needing_review",
    "print_all_routines",
]

