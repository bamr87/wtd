"""
WTD Repository - Data access layer
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from wtd.core.models import TodoContext, TodoNode, TodoPriority, TodoSource, TodoStatus
from wtd.db.models import SessionModel, TodoModel, get_engine, get_session


class TodoRepository:
    """Repository for TODO persistence operations."""

    def __init__(self, db_path: str = "sqlite:///~/.wtd/wtd.db"):
        self.engine = get_engine(db_path)

    def create_session(self, root_path: str, context: str = "unknown") -> str:
        """Create a new WTD session."""
        session = get_session(self.engine)
        try:
            db_session = SessionModel(
                root_path=root_path,
                context=context,
            )
            session.add(db_session)
            session.commit()
            return db_session.id
        finally:
            session.close()

    def save_todo(self, todo: TodoNode, session_id: str) -> str:
        """Save a TODO to the database."""
        session = get_session(self.engine)
        try:
            db_todo = TodoModel(
                id=str(todo.id),
                session_id=session_id,
                parent_id=str(todo.parent_id) if todo.parent_id else None,
                title=todo.title,
                description=todo.description,
                status=todo.status.value,
                context=todo.context.value,
                priority=todo.priority.value,
                depth=todo.depth,
                fitness_score=todo.fitness_score,
                source_file=str(todo.source.file_path) if todo.source and todo.source.file_path else None,
                source_line=todo.source.line_number if todo.source else None,
                source_type=todo.source.source_type if todo.source else None,
                raw_text=todo.source.raw_text if todo.source else None,
                result=todo.result,
                error=todo.error,
                started_at=todo.started_at,
                completed_at=todo.completed_at,
            )
            session.merge(db_todo)
            session.commit()
            return db_todo.id
        finally:
            session.close()

    def get_todo(self, todo_id: str) -> Optional[TodoNode]:
        """Get a TODO by ID."""
        session = get_session(self.engine)
        try:
            db_todo = session.query(TodoModel).filter_by(id=todo_id).first()
            if not db_todo:
                return None
            return self._to_node(db_todo)
        finally:
            session.close()

    def get_session_todos(self, session_id: str) -> list[TodoNode]:
        """Get all TODOs for a session."""
        session = get_session(self.engine)
        try:
            db_todos = session.query(TodoModel).filter_by(session_id=session_id).all()
            return [self._to_node(t) for t in db_todos]
        finally:
            session.close()

    def update_status(self, todo_id: str, status: TodoStatus) -> bool:
        """Update TODO status."""
        session = get_session(self.engine)
        try:
            db_todo = session.query(TodoModel).filter_by(id=todo_id).first()
            if not db_todo:
                return False
            
            db_todo.status = status.value
            
            if status == TodoStatus.IN_PROGRESS:
                db_todo.started_at = datetime.now()
            elif status in (TodoStatus.COMPLETED, TodoStatus.CANCELLED, TodoStatus.COLLAPSED):
                db_todo.completed_at = datetime.now()
            
            session.commit()
            return True
        finally:
            session.close()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its TODOs."""
        session = get_session(self.engine)
        try:
            db_session = session.query(SessionModel).filter_by(id=session_id).first()
            if not db_session:
                return False
            session.delete(db_session)
            session.commit()
            return True
        finally:
            session.close()

    def _to_node(self, db_todo: TodoModel) -> TodoNode:
        """Convert database model to TodoNode."""
        source = None
        if db_todo.source_file or db_todo.raw_text:
            from pathlib import Path
            source = TodoSource(
                file_path=Path(db_todo.source_file) if db_todo.source_file else None,
                line_number=db_todo.source_line,
                source_type=db_todo.source_type or "unknown",
                raw_text=db_todo.raw_text or "",
            )
        
        return TodoNode(
            id=UUID(db_todo.id),
            parent_id=UUID(db_todo.parent_id) if db_todo.parent_id else None,
            title=db_todo.title,
            description=db_todo.description or "",
            status=TodoStatus(db_todo.status),
            context=TodoContext(db_todo.context),
            priority=TodoPriority(db_todo.priority),
            source=source,
            depth=db_todo.depth,
            fitness_score=db_todo.fitness_score,
            result=db_todo.result,
            error=db_todo.error,
            created_at=db_todo.created_at,
            started_at=db_todo.started_at,
            completed_at=db_todo.completed_at,
        )



