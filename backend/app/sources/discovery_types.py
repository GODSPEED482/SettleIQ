"""Shared discovery snapshots, profiles and API summaries (tasks 2.5 and 2.6)."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FieldSnapshot(_Strict):
    name: str
    datatype: str
    nullable: bool
    unique: bool


class ForeignKeySnapshot(_Strict):
    field: str
    references: str


class IndexSnapshot(_Strict):
    name: str
    columns: list[str]
    unique: bool = False
    primary: bool = False


class SchemaSnapshot(_Strict):
    object_type: Literal["TABLE", "VIEW", "CSV"]
    fields: list[FieldSnapshot]
    primary_key: list[str] = Field(default_factory=list)
    unique_constraints: list[list[str]] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySnapshot] = Field(default_factory=list)
    indexes: list[IndexSnapshot] = Field(default_factory=list)
    format: dict[str, Any] = Field(default_factory=dict)


class ObjectProfile(_Strict):
    row_count: int
    sampled: bool = False
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)


@dataclass(frozen=True)
class ObjectResult:
    object_name: str
    snapshot: SchemaSnapshot
    profile: ObjectProfile


class ObjectSummary(_Strict):
    object: str
    object_type: str
    fields: int


class DiscoveryResult(_Strict):
    source_id: str
    kind: Literal["postgresql", "csv"]
    objects: list[ObjectSummary]
