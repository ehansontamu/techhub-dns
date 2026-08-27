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
from sqlalchemy.dialects import mysql

from app.database import Base


class CompatibilityEditorState(Base):
    """Singleton revision and WebDAV publication state."""

    __tablename__ = "compatibility_editor_state"

    id = Column(String(32), primary_key=True, default="primary")
    revision = Column(Integer, nullable=False, default=0)
    review_revision = Column(Integer, nullable=False, default=0)
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


class CompatibilityChangeRequest(Base):
    """A proposed compatibility edit that is isolated from approved data."""

    __tablename__ = "compatibility_change_requests"
    __table_args__ = (
        Index(
            "ix_compatibility_change_requests_status_target",
            "status",
            "target",
        ),
    )

    id = Column(String(36), primary_key=True)
    target = Column(String(255), nullable=False)
    mutation_type = Column(String(50), nullable=False)
    base_version = Column(Integer, nullable=False, default=0)
    proposal_version = Column(Integer, nullable=False, default=1)
    proposed_data = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    ready_for_review = Column(Boolean, nullable=False, default=True)
    submitted_by = Column(String(255), nullable=False)
    updated_by = Column(String(255), nullable=False)
    reviewed_by = Column(String(255), nullable=True)
    review_note = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    reviewed_at = Column(DateTime, nullable=True)


class CompatibilityPublicationSnapshot(Base):
    """Immutable JSON body authorized by an explicit admin publish action."""

    __tablename__ = "compatibility_publication_snapshots"

    id = Column(String(36), primary_key=True)
    revision = Column(Integer, nullable=False, index=True)
    content = Column(Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False)
    sha256 = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    requested_by = Column(String(255), nullable=False)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_attempt_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
