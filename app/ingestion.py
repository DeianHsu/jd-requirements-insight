"""该模块负责解析Markdown JD、校验字段、计算哈希、去重并写入数据库。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import JobDescription
from app.schemas import JobDocument


class JobFileError(ValueError):
    """表示单个JD文件在Markdown解析或字段校验阶段出现的可读错误。"""


@dataclass(frozen=True)
class ImportErrorDetail:
    """记录某个来源文件的导入错误，便于批处理结束后统一展示。"""

    source_file: str
    message: str


@dataclass
class ImportSummary:
    """汇总一次目录导入的发现、成功、跳过和失败数量。"""

    discovered: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[ImportErrorDetail] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """返回本次导入失败的文件数量。"""
        return len(self.errors)


def normalize_for_hash(text: str) -> str:
    """统一Unicode和空白格式，使排版差异不会干扰重复内容判断。"""
    # NFKC把全角字符等兼容形式归一，降低网页复制格式造成的伪差异。
    unicode_normalized = unicodedata.normalize("NFKC", text)
    # 压缩每行空白并移除空行，只改变哈希输入而不修改数据库中的原始正文。
    lines = [re.sub(r"\s+", " ", line).strip() for line in unicode_normalized.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def content_hash(text: str) -> str:
    """为规范化后的JD正文生成稳定SHA-256哈希，作为内容级去重标识。"""
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_job_file(path: Path) -> JobDocument:
    """读取单个Markdown的Front Matter和正文，并转换为通过校验的JobDocument。"""
    # 解析失败统一包装成领域错误，避免CLI直接暴露第三方库的复杂异常。
    try:
        post = frontmatter.load(path)
    except Exception as exc:
        raise JobFileError(f"无法解析 Markdown/YAML：{exc}") from exc

    # 将结构化元数据、完整正文和来源文件名合并为一次Pydantic校验输入。
    payload = dict(post.metadata)
    payload["raw_text"] = post.content.strip()
    payload["source_file"] = path.name

    # 将Pydantic的结构化错误压缩为用户能直接定位字段的一行中文提示。
    try:
        return JobDocument.model_validate(payload)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            field_name = ".".join(str(part) for part in error["loc"])
            messages.append(f"{field_name}: {error['msg']}")
        raise JobFileError("；".join(messages)) from exc


def to_model(document: JobDocument, digest: str) -> JobDescription:
    """把通过校验的输入Schema映射为可由SQLAlchemy持久化的ORM对象。"""
    return JobDescription(
        source_hash=digest,
        source_file=document.source_file,
        source_url=document.source_url,
        source_type=document.source_type,
        source_image=document.source_image,
        collected_at=document.collected_at,
        company=document.company,
        title=document.title,
        title_truncated=document.title_truncated,
        city=document.city,
        salary=document.salary,
        experience=document.experience,
        education=document.education,
        company_type=document.company_type,
        company_size=document.company_size,
        industry=document.industry,
        financing_status=document.financing_status,
        tags=document.tags,
        extra_metadata=document.unknown_metadata(),
        raw_text=document.raw_text,
    )


def import_directory(
    directory: Path, session_factory: sessionmaker[Session]
) -> ImportSummary:
    """批量导入目录中的Markdown JD，并按文件隔离错误和数据库事务。"""
    # 在扫描前校验路径，尽早给出明确错误而不是静默导入零条数据。
    if not directory.exists():
        raise FileNotFoundError(f"JD目录不存在：{directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"JD路径不是目录：{directory}")

    # 固定文件顺序可以让导入结果、ID分配和测试行为保持可复现。
    files = sorted(directory.glob("*.md"))
    summary = ImportSummary(discovered=len(files))

    for path in files:
        try:
            document = parse_job_file(path)
            digest = content_hash(document.raw_text)

            # 每个文件使用独立Session，单个文件失败不会回滚此前成功记录。
            with session_factory() as session:
                # 写入前按唯一内容哈希查询，使重复执行导入命令保持幂等。
                existing_id = session.scalar(
                    select(JobDescription.id).where(JobDescription.source_hash == digest)
                )
                if existing_id is not None:
                    summary.skipped += 1
                    continue

                # 校验和去重均通过后才提交，保证数据库中只保存完整有效记录。
                session.add(to_model(document, digest))
                session.commit()
                summary.imported += 1
        # 可预期的单文件错误只进入汇总，批次中的其他JD继续处理。
        except (JobFileError, OSError) as exc:
            summary.errors.append(ImportErrorDetail(path.name, str(exc)))

    return summary


def list_jobs(session_factory: sessionmaker[Session]) -> list[JobDescription]:
    """按采集日期和ID的稳定顺序返回全部已导入JD。"""
    with session_factory() as session:
        # 稳定排序使CLI输出和后续批量分析拥有可预测的记录顺序。
        statement = select(JobDescription).order_by(
            JobDescription.collected_at.desc(), JobDescription.id.asc()
        )
        return list(session.scalars(statement))
