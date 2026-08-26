"""Persistent state for the collaborative compatibility editor."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)

from app.database import Base


class CompatibilityEditorState(Base):
    """Singleton revision and WebDAV publication state."""

    __tablename__ = "compatibility_editor_state"

    id = Column(String(32), primary_key=True, default="primary")
    revision = Column(Integer, nullable=False, default=0)
    published_revision = Column(Integer, nullable=False, default=0)
    pending_since = Column(DateTime, nullable=True)
    last_published_at = Column(DateTime, nullable=True)
    last_publish_attempt_at = Column(DateTime, nullable=True)
    last_publish_error = Column(Text, nullable=True)
    published_sha256 = Column(String(64), nullable=True)
    source_sha256 = Column(String(64), nullable=True)
    document_extra_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class CompatibilityComputer(Base):
    __tablename__ = "compatibility_computers"

    sku = Column(String(100), primary_key=True)
    name = Column(String(500), nullable=False)
    url = Column(Text, nullable=True)
    hidden = Column(Boolean, nullable=False, default=False)
    student_edited = Column(Boolean, nullable=True)
    extra_data = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class CompatibilityDock(Base):
    __tablename__ = "compatibility_docks"

    sku = Column(String(100), primary_key=True)
    name = Column(String(500), nullable=False)
    url = Column(Text, nullable=True)
    hidden = Column(Boolean, nullable=False, default=False)
    student_edited = Column(Boolean, nullable=True)
    extra_data = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class CompatibilityCell(Base):
    __tablename__ = "compatibility_cells"
    __table_args__ = (
        Index("ix_compatibility_cells_dock_sku", "dock_sku"),
    )

    computer_sku = Column(
        String(100),
        ForeignKey("compatibility_computers.sku", ondelete="CASCADE"),
        primary_key=True,
    )
    dock_sku = Column(
        String(100),
        ForeignKey("compatibility_docks.sku", ondelete="CASCADE"),
        primary_key=True,
    )
    compatibility_status = Column(String(50), nullable=True)
    display = Column(String(50), nullable=True)
    charging = Column(String(50), nullable=True)
    usb_detection = Column(String(50), nullable=True)
    ethernet = Column(String(50), nullable=True)
    audio = Column(String(50), nullable=True)
    sd_card = Column(String(50), nullable=True)
    reboot_needed = Column(Boolean, nullable=True)
    notes = Column(Text, nullable=True)
    student_edited = Column(Boolean, nullable=True)
    extra_data = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class CompatibilityEditorOperation(Base):
    """Idempotency record for mutation retries from browsers."""

    __tablename__ = "compatibility_editor_operations"

    operation_id = Column(String(64), primary_key=True)
    revision = Column(Integer, nullable=False)
    mutation_type = Column(String(50), nullable=False)
    target = Column(String(255), nullable=False)
    actor = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
