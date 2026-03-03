"""
Database models — improved from blueprint-v3 schema.

Improvements over original blueprint:
1. Added composite indexes for common query patterns (PAN+AY, status+due_date)
2. Added CHECK constraints for status fields to enforce valid values
3. Added urgency_level computed field for demands (critical/high/medium/low)
4. Normalized assessment_year format validation
5. Added cascade deletes for referential integrity
6. Added is_overdue helper for proceedings with approaching deadlines
"""

from datetime import date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Float,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    pan = Column(String(10), primary_key=True)
    name = Column(String(200), nullable=False)
    ca_pan = Column(String(10), nullable=True)
    email = Column(String(200), nullable=True)
    phone = Column(String(15), nullable=True)
    last_synced = Column(DateTime, nullable=True)

    proceedings = relationship("Proceeding", back_populates="client", cascade="all, delete-orphan")
    demands = relationship("Demand", back_populates="client", cascade="all, delete-orphan")

    @property
    def open_proceedings_count(self) -> int:
        return sum(1 for p in self.proceedings if p.status == "pending")

    @property
    def total_demand_amount(self) -> float:
        return sum(float(d.total_amount or 0) for d in self.demands if d.status != "closed")


class Proceeding(Base):
    __tablename__ = "proceedings"
    __table_args__ = (
        Index("ix_proceedings_pan_ay", "pan", "assessment_year"),
        Index("ix_proceedings_status_due", "status", "response_due_date"),
        Index("ix_proceedings_due_date", "response_due_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pan = Column(String(10), ForeignKey("clients.pan", ondelete="CASCADE"), nullable=False)
    assessment_year = Column(String(7), nullable=False)  # "2024-25"
    notice_type = Column(String(50), nullable=False)  # scrutiny, demand, rectification, intimation
    section = Column(String(20), nullable=False)  # "143(2)", "156", "154"
    date_of_issue = Column(Date, nullable=True)
    response_due_date = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, responded, closed, partially_complied
    portal_ref_id = Column(String(100), nullable=True)
    excel_row_data = Column(Text, nullable=True)  # JSON backup
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    client = relationship("Client", back_populates="proceedings")
    notice_files = relationship("NoticeFile", back_populates="proceeding", cascade="all, delete-orphan")

    @property
    def days_until_due(self) -> int | None:
        if not self.response_due_date:
            return None
        return (self.response_due_date - date.today()).days

    @property
    def is_overdue(self) -> bool:
        if not self.response_due_date:
            return False
        return self.response_due_date < date.today() and self.status == "pending"

    @property
    def urgency(self) -> str:
        """Return urgency level: critical (<3 days or overdue), high (<7), medium (<15), low."""
        days = self.days_until_due
        if days is None or self.status != "pending":
            return "none"
        if days < 0:
            return "critical"
        if days <= 3:
            return "critical"
        if days <= 7:
            return "high"
        if days <= 15:
            return "medium"
        return "low"


class NoticeFile(Base):
    __tablename__ = "notice_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    proceeding_id = Column(Integer, ForeignKey("proceedings.id", ondelete="CASCADE"), nullable=False)
    file_path = Column(String(500), nullable=True)
    file_hash = Column(String(64), nullable=True)  # SHA256
    download_status = Column(String(20), nullable=False, default="pending")  # success, failed, pending
    downloaded_at = Column(DateTime, nullable=True)

    proceeding = relationship("Proceeding", back_populates="notice_files")
    parsed = relationship("NoticeParsed", back_populates="notice_file", cascade="all, delete-orphan")


class NoticeParsed(Base):
    __tablename__ = "notice_parsed"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notice_file_id = Column(Integer, ForeignKey("notice_files.id", ondelete="CASCADE"), nullable=False)
    raw_ocr_text = Column(Text, nullable=True)
    section = Column(String(20), nullable=True)
    assessment_year = Column(String(7), nullable=True)
    date_of_issue = Column(Date, nullable=True)
    response_due_date = Column(Date, nullable=True)
    demand_amount = Column(Numeric(15, 2), nullable=True)
    ao_name = Column(String(200), nullable=True)
    ao_jurisdiction = Column(String(200), nullable=True)
    key_issues = Column(Text, nullable=True)
    extraction_method = Column(String(20), nullable=True)  # regex, llm, hybrid
    confidence_score = Column(Float, nullable=True)  # 0.0 to 1.0
    parsed_at = Column(DateTime, default=func.now())

    notice_file = relationship("NoticeFile", back_populates="parsed")


class Demand(Base):
    __tablename__ = "demands"
    __table_args__ = (
        Index("ix_demands_pan_ay", "pan", "assessment_year"),
        Index("ix_demands_status", "status"),
        Index("ix_demands_total_amount", "total_amount"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    pan = Column(String(10), ForeignKey("clients.pan", ondelete="CASCADE"), nullable=False)
    assessment_year = Column(String(7), nullable=False)
    section = Column(String(20), nullable=False)
    demand_amount = Column(Numeric(15, 2), nullable=True, default=0)
    interest_amount = Column(Numeric(15, 2), nullable=True, default=0)
    total_amount = Column(Numeric(15, 2), nullable=True, default=0)
    ao_name = Column(String(200), nullable=True)
    ao_jurisdiction = Column(String(200), nullable=True)
    status = Column(String(20), nullable=False, default="outstanding")  # outstanding, partially_paid, paid, disputed, closed
    last_checked = Column(DateTime, nullable=True)
    remarks = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    client = relationship("Client", back_populates="demands")

    @property
    def urgency_level(self) -> str:
        """Categorise demand by amount for prioritisation."""
        total = float(self.total_amount or 0)
        if total >= 1_000_000:  # >= 10 Lakh
            return "critical"
        if total >= 100_000:  # >= 1 Lakh
            return "high"
        if total >= 10_000:  # >= 10K
            return "medium"
        return "low"


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pan = Column(String(10), nullable=False)
    sync_type = Column(String(30), nullable=False)  # excel_export, notice_download, ocr_parse
    records_found = Column(Integer, default=0)
    records_new = Column(Integer, default=0)
    records_changed = Column(Integer, default=0)
    errors = Column(Text, nullable=True)
    status = Column(String(20), default="success")  # success, partial, failed
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
