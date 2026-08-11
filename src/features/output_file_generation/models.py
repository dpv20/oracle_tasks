"""Value objects shared by the output-file generation feature."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class DataSourceChoice(str, Enum):
    ACTIVE = "active"
    ARCHIVE = "archive"


@dataclass(frozen=True)
class OracleTarget:
    country: str
    database_key: str
    credential_key: str
    tns: str
    label: str = ""


@dataclass(frozen=True)
class GenerationRequest:
    target: OracleTarget
    process_ref_no: str
    overwrite: bool = False
    interface_code: str | None = None
    input_file_date: str | None = None


@dataclass(frozen=True)
class GenerationResult:
    output_path: Path
    input_code: str
    output_code: str
    process_ref_no: str
    file_date: str
    source: DataSourceChoice
    body_count: int
    status_counts: dict[str, int]
    lines: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class ProcessCandidate:
    source: DataSourceChoice
    input_code: str
    record_count: int
    evidence_kinds: frozenset[str] = frozenset()
    upload_record_count: int = 0
