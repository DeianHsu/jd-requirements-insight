"""该模块定义SQLAlchemy ORM持久化模型及其数据库字段。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """返回带时区的UTC时间，统一数据库记录的时间基准。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """作为所有ORM模型的共同基类，为SQLAlchemy收集表结构元数据。"""


class JobDescription(Base):
    """保存一份JD的来源信息、招聘元数据、完整正文和去重标识。"""

    __tablename__ = "job_descriptions"

    # 该字段组负责记录数据身份和原始来源，确保后续结论可以回溯。
    id: Mapped[int] = mapped_column(primary_key=True)
    source_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_file: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), default="unknown")
    source_image: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 该字段组保存招聘岗位的基础属性，便于后续筛选和分组统计。
    collected_at: Mapped[date] = mapped_column(Date)
    company: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    title_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    salary: Mapped[str | None] = mapped_column(String(100), nullable=True)
    experience: Mapped[str | None] = mapped_column(String(100), nullable=True)
    education: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 该字段组描述公司画像和人工标签，为目标公司类型分析提供维度。
    company_type: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    company_size: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    financing_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # 始终保留完整原文和审计时间，避免结构化过程丢失原始证据。
    raw_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    # 一个原始JD可以保留多个版本的抽取结果，便于比较抽取器迭代效果。
    extractions: Mapped[list[JobExtraction]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobExtraction(Base):
    """保存某个抽取器版本针对一份JD生成的结构化结果及运行元数据。"""

    __tablename__ = "job_extractions"
    __table_args__ = (
        UniqueConstraint("job_id", "extractor_version", name="uq_job_extractor_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True
    )
    extractor_version: Mapped[str] = mapped_column(String(255))
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    role_family: Mapped[str] = mapped_column(String(50), index=True)
    seniority: Mapped[str] = mapped_column(String(50), index=True)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    job: Mapped[JobDescription] = relationship(back_populates="extractions")
    requirements: Mapped[list[JobRequirement]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan"
    )


class JobRequirement(Base):
    """保存一项原子岗位要求的分类、逻辑组、年限范围和原文证据。"""

    __tablename__ = "job_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        ForeignKey("job_extractions.id", ondelete="CASCADE"), index=True
    )
    raw_name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(50), index=True)
    importance: Mapped[str] = mapped_column(String(50), index=True)
    proficiency: Mapped[str] = mapped_column(String(50))
    group_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    group_logic: Mapped[str] = mapped_column(String(20), default="standalone")
    min_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    years_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)

    extraction: Mapped[JobExtraction] = relationship(back_populates="requirements")


class JobConsolidation(Base):
    """保存一次跨JD归并的批次主记录，携带范围键与归并器版本。"""

    __tablename__ = "job_consolidations"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "consolidator_version",
            "input_fingerprint",
            name="uq_scope_consolidator_input",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(255), index=True)
    consolidator_version: Mapped[str] = mapped_column(String(255))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(255))
    selected_job_ids: Mapped[list[int]] = mapped_column(JSON)
    extraction_ids: Mapped[list[int]] = mapped_column(JSON)
    model_name: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(50))
    schema_version: Mapped[str] = mapped_column(String(50))
    occurrence_count: Mapped[int] = mapped_column(default=0)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    canonical_requirements: Mapped[list[CanonicalRequirementRecord]] = relationship(
        back_populates="consolidation", cascade="all, delete-orphan"
    )
    mappings: Mapped[list[RequirementMappingRecord]] = relationship(
        back_populates="consolidation", cascade="all, delete-orphan"
    )


class CanonicalRequirementRecord(Base):
    """保存归并产生的跨JD标准要求项及其来源理由。"""

    __tablename__ = "canonical_requirements"
    __table_args__ = (
        UniqueConstraint(
            "consolidation_id",
            "canonical_requirement_id",
            name="uq_consolidation_canonical_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consolidation_id: Mapped[int] = mapped_column(
        ForeignKey("job_consolidations.id", ondelete="CASCADE"), index=True
    )
    canonical_requirement_id: Mapped[str] = mapped_column(String(100))
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)

    consolidation: Mapped[JobConsolidation] = relationship(
        back_populates="canonical_requirements"
    )


class RequirementMappingRecord(Base):
    """保存要求实例到标准要求项的映射，requirement_id可回溯到原始要求。"""

    __tablename__ = "requirement_mappings"
    __table_args__ = (
        UniqueConstraint(
            "consolidation_id", "requirement_id", name="uq_consolidation_requirement"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consolidation_id: Mapped[int] = mapped_column(
        ForeignKey("job_consolidations.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="CASCADE"), index=True
    )
    canonical_requirement_id: Mapped[str] = mapped_column(String(100))
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)

    consolidation: Mapped[JobConsolidation] = relationship(
        back_populates="mappings"
    )
    requirement: Mapped[JobRequirement] = relationship()
