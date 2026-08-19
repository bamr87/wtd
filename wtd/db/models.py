"""
WTD Database Models - SQLAlchemy models for persistence

Uses SQLAlchemy 2.0 typed declarative mappings (``Mapped`` +
``mapped_column``) so attribute access carries real Python types
(``str``, ``datetime | None``, ...) instead of ``Column[...]``.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


class SessionModel(Base):
    """WTD Session model."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
    root_path: Mapped[str | None] = mapped_column(String(500))
    context: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    todos: Mapped[list["TodoModel"]] = relationship(
        "TodoModel", back_populates="session", cascade="all, delete-orphan"
    )


class TodoModel(Base):
    """TODO item model."""

    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("todos.id"), nullable=True
    )

    # Core fields
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    context: Mapped[str] = mapped_column(String(20), default="unknown")
    priority: Mapped[str] = mapped_column(String(20), default="medium")

    # Recursion tracking
    depth: Mapped[int] = mapped_column(Integer, default=0)
    fitness_score: Mapped[float] = mapped_column(Float, default=1.0)

    # Source information
    source_file: Mapped[str | None] = mapped_column(String(500))
    source_line: Mapped[int | None] = mapped_column(Integer)
    source_type: Mapped[str | None] = mapped_column(String(50))
    raw_text: Mapped[str | None] = mapped_column(Text)

    # Execution
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Relationships
    session: Mapped["SessionModel"] = relationship(
        "SessionModel", back_populates="todos"
    )
    children: Mapped[list["TodoModel"]] = relationship(
        "TodoModel",
        backref="parent",
        remote_side=[id],
        cascade="all, delete-orphan",
        single_parent=True,
    )


def get_engine(db_path: str = "sqlite:///~/.wtd/wtd.db") -> Engine:
    """Create database engine."""
    import os

    # Expand user path
    if db_path.startswith("sqlite:///~"):
        db_path = db_path.replace("~", os.path.expanduser("~"))

    # Ensure directory exists
    if db_path.startswith("sqlite:///"):
        path = db_path.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(path), exist_ok=True)

    engine = create_engine(db_path, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine: Engine | None = None) -> Session:
    """Create database session."""
    if engine is None:
        engine = get_engine()
    session_factory = sessionmaker(bind=engine)
    return session_factory()
