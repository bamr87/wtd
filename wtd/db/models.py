"""
WTD Database Models - SQLAlchemy models for persistence
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


class SessionModel(Base):
    """WTD Session model."""
    
    __tablename__ = "sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    root_path = Column(String(500))
    context = Column(String(50))
    is_active = Column(Boolean, default=True)
    
    # Relationships
    todos = relationship("TodoModel", back_populates="session", cascade="all, delete-orphan")


class TodoModel(Base):
    """TODO item model."""
    
    __tablename__ = "todos"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    parent_id = Column(String(36), ForeignKey("todos.id"), nullable=True)
    
    # Core fields
    title = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(String(20), default="pending")
    context = Column(String(20), default="unknown")
    priority = Column(String(20), default="medium")
    
    # Recursion tracking
    depth = Column(Integer, default=0)
    fitness_score = Column(Float, default=1.0)
    
    # Source information
    source_file = Column(String(500))
    source_line = Column(Integer)
    source_type = Column(String(50))
    raw_text = Column(Text)
    
    # Execution
    result = Column(Text)
    error = Column(Text)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    
    # Relationships
    session = relationship("SessionModel", back_populates="todos")
    children = relationship(
        "TodoModel",
        backref="parent",
        remote_side=[id],
        cascade="all, delete-orphan",
        single_parent=True,
    )


def get_engine(db_path: str = "sqlite:///~/.wtd/wtd.db"):
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


def get_session(engine=None):
    """Create database session."""
    if engine is None:
        engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()

