"""
WTD Routine Manager - Handles recurring/routine TODOs

Routines are TODOs that repeat on a schedule:
- Daily, weekly, monthly, quarterly
- Custom intervals (every N days)
- Conditional triggers (on-release, on-pr, on-deploy)
- Day-specific (monday, friday, workday, weekend)
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from wtd.core.models import TodoNode, TodoPriority, TodoSource, TodoStatus, TodoContext


class RoutineFrequency(str, Enum):
    """Frequency types for routines."""
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"  # Every N days
    CONDITIONAL = "conditional"  # Event-triggered


class RoutineDay(str, Enum):
    """Specific days for routines."""
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"
    WORKDAY = "workday"  # Monday-Friday
    WEEKEND = "weekend"  # Saturday-Sunday


class RoutineTrigger(str, Enum):
    """Conditional triggers for routines."""
    ON_RELEASE = "on-release"
    ON_PR = "on-pr"
    ON_DEPLOY = "on-deploy"
    ON_ERROR_SPIKE = "on-error-spike"
    ON_COMMIT = "on-commit"
    ON_MERGE = "on-merge"
    ON_STARTUP = "on-startup"


class RoutineStatus(str, Enum):
    """Status of a routine."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"  # For this cycle
    OVERDUE = "overdue"
    SNOOZED = "snoozed"
    ARCHIVED = "archived"


@dataclass
class RoutineSchedule:
    """Schedule configuration for a routine."""
    frequency: RoutineFrequency
    day: RoutineDay | None = None
    day_of_month: int | None = None  # 1-31 for monthly
    custom_days: int | None = None  # For every-N-days
    trigger: RoutineTrigger | None = None  # For conditional
    time_of_day: str | None = None  # HH:MM format
    
    def next_due_date(self, from_date: datetime | None = None) -> datetime | None:
        """Calculate the next due date based on schedule."""
        now = from_date or datetime.now()
        
        if self.frequency == RoutineFrequency.CONDITIONAL:
            return None  # Triggered by events, not time
        
        if self.frequency == RoutineFrequency.DAILY:
            next_date = now + timedelta(days=1)
            if self.day:
                next_date = self._next_matching_day(now, self.day)
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if self.frequency == RoutineFrequency.WEEKLY:
            next_date = now + timedelta(days=7)
            if self.day:
                next_date = self._next_matching_day(now, self.day)
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if self.frequency == RoutineFrequency.BIWEEKLY:
            next_date = now + timedelta(days=14)
            if self.day:
                # Find next matching day, then add a week if needed
                next_date = self._next_matching_day(now, self.day)
                if (next_date - now).days < 7:
                    next_date += timedelta(days=7)
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if self.frequency == RoutineFrequency.MONTHLY:
            # Move to next month
            if now.month == 12:
                next_date = now.replace(year=now.year + 1, month=1)
            else:
                next_date = now.replace(month=now.month + 1)
            
            # Set day of month
            if self.day_of_month:
                day = min(self.day_of_month, 28)  # Safe day
                next_date = next_date.replace(day=day)
            
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if self.frequency == RoutineFrequency.QUARTERLY:
            # Move to next quarter
            current_quarter = (now.month - 1) // 3
            next_quarter_month = ((current_quarter + 1) % 4) * 3 + 1
            next_year = now.year if next_quarter_month > now.month else now.year + 1
            next_date = now.replace(year=next_year, month=next_quarter_month, day=1)
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        if self.frequency == RoutineFrequency.CUSTOM and self.custom_days:
            next_date = now + timedelta(days=self.custom_days)
            return next_date.replace(hour=9, minute=0, second=0, microsecond=0)
        
        return None
    
    def _next_matching_day(self, from_date: datetime, day: RoutineDay) -> datetime:
        """Find the next occurrence of a specific day."""
        day_mapping = {
            RoutineDay.MONDAY: 0,
            RoutineDay.TUESDAY: 1,
            RoutineDay.WEDNESDAY: 2,
            RoutineDay.THURSDAY: 3,
            RoutineDay.FRIDAY: 4,
            RoutineDay.SATURDAY: 5,
            RoutineDay.SUNDAY: 6,
        }
        
        if day == RoutineDay.WORKDAY:
            # Next weekday
            next_date = from_date + timedelta(days=1)
            while next_date.weekday() >= 5:  # Saturday or Sunday
                next_date += timedelta(days=1)
            return next_date
        
        if day == RoutineDay.WEEKEND:
            # Next weekend day
            next_date = from_date + timedelta(days=1)
            while next_date.weekday() < 5:  # Not Saturday or Sunday
                next_date += timedelta(days=1)
            return next_date
        
        target_weekday = day_mapping.get(day, 0)
        days_ahead = target_weekday - from_date.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return from_date + timedelta(days=days_ahead)


@dataclass
class Routine:
    """A routine/recurring TODO."""
    id: UUID = field(default_factory=uuid4)
    title: str = ""
    description: str = ""
    schedule: RoutineSchedule = field(default_factory=lambda: RoutineSchedule(RoutineFrequency.WEEKLY))
    status: RoutineStatus = RoutineStatus.ACTIVE
    priority: TodoPriority = TodoPriority.MEDIUM
    context: TodoContext = TodoContext.UNKNOWN
    
    # Tracking
    last_completed: datetime | None = None
    next_due: datetime | None = None
    times_completed: int = 0
    times_skipped: int = 0
    times_snoozed: int = 0
    
    # History
    completion_history: list[datetime] = field(default_factory=list)
    
    # Metadata
    source: TodoSource | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    # Evaluation
    effectiveness_score: float = 1.0  # Decays if frequently skipped
    notes: str = ""
    
    def __post_init__(self):
        if self.next_due is None:
            self.next_due = self.schedule.next_due_date()
    
    def is_due(self, as_of: datetime | None = None) -> bool:
        """Check if this routine is due."""
        now = as_of or datetime.now()
        
        if self.status != RoutineStatus.ACTIVE:
            return False
        
        if self.schedule.frequency == RoutineFrequency.CONDITIONAL:
            return False  # Must be triggered explicitly
        
        if self.next_due is None:
            return True
        
        return now >= self.next_due
    
    def is_overdue(self, as_of: datetime | None = None) -> bool:
        """Check if this routine is overdue."""
        now = as_of or datetime.now()
        
        if not self.is_due(now):
            return False
        
        if self.next_due is None:
            return False
        
        # Overdue if more than 1 day past due
        return (now - self.next_due).days >= 1
    
    def complete(self, notes: str = "") -> None:
        """Mark this routine as completed for this cycle."""
        now = datetime.now()
        self.last_completed = now
        self.times_completed += 1
        self.completion_history.append(now)
        self.next_due = self.schedule.next_due_date(now)
        self.status = RoutineStatus.ACTIVE
        self.updated_at = now
        
        # Improve effectiveness if completed on time
        self.effectiveness_score = min(1.0, self.effectiveness_score + 0.05)
        
        if notes:
            self.notes = notes
    
    def skip(self, reason: str = "") -> None:
        """Skip this routine for this cycle."""
        now = datetime.now()
        self.times_skipped += 1
        self.next_due = self.schedule.next_due_date(now)
        self.status = RoutineStatus.ACTIVE
        self.updated_at = now
        
        # Decay effectiveness if skipped
        self.effectiveness_score = max(0.1, self.effectiveness_score - 0.1)
        
        if reason:
            self.notes = f"Skipped: {reason}"
    
    def snooze(self, hours: int = 24) -> None:
        """Snooze this routine for a specified time."""
        now = datetime.now()
        self.times_snoozed += 1
        self.next_due = now + timedelta(hours=hours)
        self.status = RoutineStatus.SNOOZED
        self.updated_at = now
        
        # Small decay for snoozing
        self.effectiveness_score = max(0.1, self.effectiveness_score - 0.02)
    
    def pause(self) -> None:
        """Pause this routine indefinitely."""
        self.status = RoutineStatus.PAUSED
        self.updated_at = datetime.now()
    
    def resume(self) -> None:
        """Resume a paused routine."""
        now = datetime.now()
        self.status = RoutineStatus.ACTIVE
        self.next_due = self.schedule.next_due_date(now)
        self.updated_at = now
    
    def archive(self) -> None:
        """Archive this routine (soft delete)."""
        self.status = RoutineStatus.ARCHIVED
        self.updated_at = datetime.now()
    
    def should_reevaluate(self) -> bool:
        """Check if this routine should be re-evaluated."""
        # Suggest re-evaluation if:
        # - Effectiveness is low
        # - Skipped more than completed
        # - Not completed in a long time
        
        if self.effectiveness_score < 0.5:
            return True
        
        if self.times_skipped > self.times_completed and self.times_skipped > 3:
            return True
        
        if self.last_completed:
            days_since = (datetime.now() - self.last_completed).days
            if self.schedule.frequency == RoutineFrequency.DAILY and days_since > 7:
                return True
            if self.schedule.frequency == RoutineFrequency.WEEKLY and days_since > 30:
                return True
        
        return False
    
    def get_reevaluation_reason(self) -> str:
        """Get the reason for re-evaluation suggestion."""
        reasons = []
        
        if self.effectiveness_score < 0.5:
            reasons.append(f"Low effectiveness ({self.effectiveness_score:.0%})")
        
        if self.times_skipped > self.times_completed and self.times_skipped > 3:
            reasons.append(f"Frequently skipped ({self.times_skipped} skips vs {self.times_completed} completions)")
        
        if self.last_completed:
            days_since = (datetime.now() - self.last_completed).days
            if days_since > 14:
                reasons.append(f"Not completed in {days_since} days")
        
        return "; ".join(reasons) if reasons else "Routine is performing well"
    
    def to_todo_node(self) -> TodoNode:
        """Convert to a TodoNode for execution."""
        status = TodoStatus.PENDING
        if self.status == RoutineStatus.OVERDUE:
            status = TodoStatus.PENDING  # Still pending but marked urgent
        
        return TodoNode(
            id=self.id,
            title=f"[Routine] {self.title}",
            description=self.description,
            status=status,
            context=self.context,
            priority=TodoPriority.HIGH if self.is_overdue() else self.priority,
            source=self.source,
            tags=["routine"] + self.tags,
            metadata={
                "routine_id": str(self.id),
                "frequency": self.schedule.frequency.value,
                "times_completed": self.times_completed,
                "effectiveness": self.effectiveness_score,
            },
        )


class RoutineParser:
    """Parse routine definitions from markdown files."""
    
    # Pattern: [routine:frequency:day] or [routine:every-N-days]
    ROUTINE_PATTERN = re.compile(
        r"\[routine:([a-z-]+)(?::([a-z0-9-]+))?\]",
        re.IGNORECASE
    )
    
    FREQUENCY_MAP = {
        "daily": RoutineFrequency.DAILY,
        "weekly": RoutineFrequency.WEEKLY,
        "biweekly": RoutineFrequency.BIWEEKLY,
        "monthly": RoutineFrequency.MONTHLY,
        "quarterly": RoutineFrequency.QUARTERLY,
        "yearly": RoutineFrequency.YEARLY,
    }
    
    DAY_MAP = {
        "monday": RoutineDay.MONDAY,
        "tuesday": RoutineDay.TUESDAY,
        "wednesday": RoutineDay.WEDNESDAY,
        "thursday": RoutineDay.THURSDAY,
        "friday": RoutineDay.FRIDAY,
        "saturday": RoutineDay.SATURDAY,
        "sunday": RoutineDay.SUNDAY,
        "workday": RoutineDay.WORKDAY,
        "weekend": RoutineDay.WEEKEND,
    }
    
    TRIGGER_MAP = {
        "on-release": RoutineTrigger.ON_RELEASE,
        "on-pr": RoutineTrigger.ON_PR,
        "on-deploy": RoutineTrigger.ON_DEPLOY,
        "on-error-spike": RoutineTrigger.ON_ERROR_SPIKE,
        "on-commit": RoutineTrigger.ON_COMMIT,
        "on-merge": RoutineTrigger.ON_MERGE,
        "on-startup": RoutineTrigger.ON_STARTUP,
    }
    
    @classmethod
    def parse_line(cls, line: str, file_path: Path | None = None, line_num: int = 0) -> Routine | None:
        """Parse a single line for routine definition."""
        match = cls.ROUTINE_PATTERN.search(line)
        if not match:
            return None
        
        freq_str = match.group(1).lower()
        modifier = match.group(2).lower() if match.group(2) else None
        
        # Extract title (text after the routine tag)
        title_start = match.end()
        title = line[title_start:].strip()
        
        # Remove markdown checkbox if present
        title = re.sub(r"^\s*-?\s*\[[ x]\]\s*", "", title, flags=re.IGNORECASE)
        
        if not title:
            return None
        
        # Parse schedule
        schedule = cls._parse_schedule(freq_str, modifier)
        if schedule is None:
            return None
        
        # Infer context from title
        context = cls._infer_context(title)
        
        # Create routine
        routine = Routine(
            title=title,
            schedule=schedule,
            context=context,
            source=TodoSource(
                file_path=file_path,
                line_number=line_num,
                source_type="routine",
                raw_text=line.strip(),
            ) if file_path else None,
        )
        
        return routine
    
    @classmethod
    def _parse_schedule(cls, freq_str: str, modifier: str | None) -> RoutineSchedule | None:
        """Parse frequency and modifier into a schedule."""
        # Check for custom interval (every-N-days)
        custom_match = re.match(r"every-(\d+)-days?", freq_str)
        if custom_match:
            days = int(custom_match.group(1))
            return RoutineSchedule(
                frequency=RoutineFrequency.CUSTOM,
                custom_days=days,
            )
        
        # Check for conditional triggers
        if freq_str in cls.TRIGGER_MAP:
            return RoutineSchedule(
                frequency=RoutineFrequency.CONDITIONAL,
                trigger=cls.TRIGGER_MAP[freq_str],
            )
        
        # Standard frequency
        if freq_str not in cls.FREQUENCY_MAP:
            return None
        
        frequency = cls.FREQUENCY_MAP[freq_str]
        day = None
        day_of_month = None
        
        if modifier:
            # Check if it's a day name
            if modifier in cls.DAY_MAP:
                day = cls.DAY_MAP[modifier]
            # Check if it's a day number (for monthly)
            elif modifier.isdigit():
                day_of_month = int(modifier)
        
        return RoutineSchedule(
            frequency=frequency,
            day=day,
            day_of_month=day_of_month,
        )
    
    @classmethod
    def _infer_context(cls, title: str) -> TodoContext:
        """Infer context from routine title."""
        title_lower = title.lower()
        
        keywords = {
            TodoContext.BUGFIX: ["fix", "bug", "error", "issue", "triage"],
            TodoContext.WRITE: ["document", "write", "update docs", "changelog"],
            TodoContext.PLAN: ["plan", "review", "retrospective", "roadmap"],
            TodoContext.TEST: ["test", "ci", "coverage", "integration"],
            TodoContext.DEPLOY: ["deploy", "release", "publish", "ship"],
            TodoContext.REFACTOR: ["refactor", "clean", "optimize", "debt"],
            TodoContext.BUILD: ["build", "create", "implement", "add"],
        }
        
        for context, kws in keywords.items():
            if any(kw in title_lower for kw in kws):
                return context
        
        return TodoContext.UNKNOWN


class RoutineManager:
    """Manages routine TODOs."""
    
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path.home() / ".wtd" / "routines.json"
        self.routines: dict[UUID, Routine] = {}
        self._load()
    
    def _load(self) -> None:
        """Load routines from storage."""
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text())
                for item in data.get("routines", []):
                    routine = self._deserialize_routine(item)
                    self.routines[routine.id] = routine
            except (json.JSONDecodeError, KeyError):
                pass
    
    def _save(self) -> None:
        """Save routines to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "routines": [self._serialize_routine(r) for r in self.routines.values()],
            "updated_at": datetime.now().isoformat(),
        }
        self.storage_path.write_text(json.dumps(data, indent=2, default=str))
    
    def _serialize_routine(self, routine: Routine) -> dict:
        """Serialize a routine to dict."""
        return {
            "id": str(routine.id),
            "title": routine.title,
            "description": routine.description,
            "schedule": {
                "frequency": routine.schedule.frequency.value,
                "day": routine.schedule.day.value if routine.schedule.day else None,
                "day_of_month": routine.schedule.day_of_month,
                "custom_days": routine.schedule.custom_days,
                "trigger": routine.schedule.trigger.value if routine.schedule.trigger else None,
            },
            "status": routine.status.value,
            "priority": routine.priority.value,
            "context": routine.context.value,
            "last_completed": routine.last_completed.isoformat() if routine.last_completed else None,
            "next_due": routine.next_due.isoformat() if routine.next_due else None,
            "times_completed": routine.times_completed,
            "times_skipped": routine.times_skipped,
            "times_snoozed": routine.times_snoozed,
            "effectiveness_score": routine.effectiveness_score,
            "tags": routine.tags,
            "created_at": routine.created_at.isoformat(),
            "updated_at": routine.updated_at.isoformat(),
        }
    
    def _deserialize_routine(self, data: dict) -> Routine:
        """Deserialize a routine from dict."""
        schedule_data = data.get("schedule", {})
        schedule = RoutineSchedule(
            frequency=RoutineFrequency(schedule_data.get("frequency", "weekly")),
            day=RoutineDay(schedule_data["day"]) if schedule_data.get("day") else None,
            day_of_month=schedule_data.get("day_of_month"),
            custom_days=schedule_data.get("custom_days"),
            trigger=RoutineTrigger(schedule_data["trigger"]) if schedule_data.get("trigger") else None,
        )
        
        return Routine(
            id=UUID(data["id"]),
            title=data["title"],
            description=data.get("description", ""),
            schedule=schedule,
            status=RoutineStatus(data.get("status", "active")),
            priority=TodoPriority(data.get("priority", "medium")),
            context=TodoContext(data.get("context", "unknown")),
            last_completed=datetime.fromisoformat(data["last_completed"]) if data.get("last_completed") else None,
            next_due=datetime.fromisoformat(data["next_due"]) if data.get("next_due") else None,
            times_completed=data.get("times_completed", 0),
            times_skipped=data.get("times_skipped", 0),
            times_snoozed=data.get("times_snoozed", 0),
            effectiveness_score=data.get("effectiveness_score", 1.0),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
        )
    
    def add(self, routine: Routine) -> None:
        """Add a routine."""
        self.routines[routine.id] = routine
        self._save()
    
    def remove(self, routine_id: UUID) -> bool:
        """Remove a routine."""
        if routine_id in self.routines:
            del self.routines[routine_id]
            self._save()
            return True
        return False
    
    def get(self, routine_id: UUID) -> Routine | None:
        """Get a routine by ID."""
        return self.routines.get(routine_id)
    
    def get_due_routines(self) -> list[Routine]:
        """Get all routines that are currently due."""
        return [r for r in self.routines.values() if r.is_due()]
    
    def get_overdue_routines(self) -> list[Routine]:
        """Get all overdue routines."""
        return [r for r in self.routines.values() if r.is_overdue()]
    
    def get_routines_needing_reevaluation(self) -> list[Routine]:
        """Get routines that should be re-evaluated."""
        return [r for r in self.routines.values() if r.should_reevaluate()]

    def get_routines_needing_review(self) -> list[Routine]:
        """Alias used by the CLI review command."""
        return self.get_routines_needing_reevaluation()
    
    def complete_routine(self, routine_id: UUID, notes: str = "") -> bool:
        """Mark a routine as completed."""
        routine = self.routines.get(routine_id)
        if routine:
            routine.complete(notes)
            self._save()
            return True
        return False
    
    def skip_routine(self, routine_id: UUID, reason: str = "") -> bool:
        """Skip a routine."""
        routine = self.routines.get(routine_id)
        if routine:
            routine.skip(reason)
            self._save()
            return True
        return False
    
    def snooze_routine(self, routine_id: UUID, hours: int = 24) -> bool:
        """Snooze a routine."""
        routine = self.routines.get(routine_id)
        if routine:
            routine.snooze(hours)
            self._save()
            return True
        return False
    
    def trigger_conditional(self, trigger: RoutineTrigger) -> list[Routine]:
        """Trigger all routines with a specific conditional trigger."""
        triggered = []
        for routine in self.routines.values():
            if (routine.schedule.frequency == RoutineFrequency.CONDITIONAL and
                routine.schedule.trigger == trigger and
                routine.status == RoutineStatus.ACTIVE):
                triggered.append(routine)
        return triggered
    
    def sync_from_files(self, root_path: Path) -> dict[str, int]:
        """Sync routines from markdown files."""
        stats = {"added": 0, "updated": 0, "unchanged": 0}
        
        # Find routine files
        routine_files = list(root_path.rglob("**/routines.md")) + \
                       list(root_path.rglob("**/ROUTINES.md"))
        
        existing_titles = {r.title: r.id for r in self.routines.values()}
        
        for file_path in routine_files:
            try:
                content = file_path.read_text()
                lines = content.split("\n")
                
                for line_num, line in enumerate(lines, 1):
                    routine = RoutineParser.parse_line(line, file_path, line_num)
                    if routine:
                        if routine.title in existing_titles:
                            # Update source but keep tracking data
                            existing_id = existing_titles[routine.title]
                            existing = self.routines[existing_id]
                            existing.source = routine.source
                            existing.updated_at = datetime.now()
                            stats["updated"] += 1
                        else:
                            self.add(routine)
                            existing_titles[routine.title] = routine.id
                            stats["added"] += 1
            except (IOError, UnicodeDecodeError):
                continue
        
        stats["unchanged"] = len(self.routines) - stats["added"] - stats["updated"]
        self._save()
        return stats
    
    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all routines."""
        active = [r for r in self.routines.values() if r.status == RoutineStatus.ACTIVE]
        due = self.get_due_routines()
        overdue = self.get_overdue_routines()
        need_eval = self.get_routines_needing_reevaluation()
        
        return {
            "total": len(self.routines),
            "active": len(active),
            "due": len(due),
            "overdue": len(overdue),
            "need_reevaluation": len(need_eval),
            "by_frequency": self._count_by_frequency(),
            "average_effectiveness": sum(r.effectiveness_score for r in active) / len(active) if active else 0,
        }
    
    def _count_by_frequency(self) -> dict[str, int]:
        """Count routines by frequency."""
        counts: dict[str, int] = {}
        for routine in self.routines.values():
            freq = routine.schedule.frequency.value
            counts[freq] = counts.get(freq, 0) + 1
        return counts

