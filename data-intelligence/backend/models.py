"""Typed internal models for Agent 2's SQLite inspection results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
@dataclass(frozen=True)
class Observation:
    """A fact observed from the database, separate from a data-quality warning."""

    code: str
    message: str
    table: str | None = None
    column: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class WarningRecord:
    """A condition that may affect interpretation of the profiled data."""

    code: str
    severity: str
    message: str
    table: str | None = None
    column: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ForeignKeyDefinition:
    column: str
    references_table: str
    references_column: str | None
    inferred: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ForeignKeyViolation:
    table: str
    rowid: int | None
    parent_table: str
    foreign_key_id: int
    column: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str
    nullable: bool
    pk: bool
    fk_to: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        if result["fk_to"] is None:
            del result["fk_to"]
        return result


@dataclass(frozen=True)
class TableSchema:
    name: str
    kind: str
    row_count: int
    columns: list[ColumnSchema]
    foreign_keys: list[ForeignKeyDefinition]
    observations: list[Observation] = field(default_factory=list)
    warnings: list[WarningRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "row_count": self.row_count,
            "columns": [column.to_dict() for column in self.columns],
            "foreign_keys": [foreign_key.to_dict() for foreign_key in self.foreign_keys],
            "observations": [observation.to_dict() for observation in self.observations],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
