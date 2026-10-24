"""Safe SQLite connection and identifier helpers for Agent 2."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import quote


class DatabaseInspectionError(RuntimeError):
    """Base error for database configuration and inspection failures."""


class DatabaseConfigurationError(DatabaseInspectionError):
    """The database configuration is missing or unsupported."""


class DatabaseReadError(DatabaseInspectionError):
    """The database could not be opened or read."""


def normalize_database_path(database_config: str | Path | Mapping[str, object]) -> Path:
    """Validate and normalize a supported SQLite database configuration."""

    if isinstance(database_config, Mapping):
        database_type = database_config.get("type", "sqlite")
        if database_type != "sqlite":
            raise DatabaseConfigurationError(
                "only SQLite database configurations are supported"
            )
        raw_path = database_config.get("path")
    else:
        raw_path = database_config

    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise DatabaseConfigurationError("a SQLite database path is required")

    database_path = Path(raw_path).expanduser().resolve()
    if not database_path.exists():
        raise DatabaseConfigurationError("the SQLite database path does not exist")
    if not database_path.is_file():
        raise DatabaseConfigurationError("the SQLite database path is not a file")
    return database_path


def quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier without interpolating it as executable SQL."""

    return '"' + identifier.replace('"', '""') + '"'


@contextmanager
def read_only_connection(
    database_config: str | Path | Mapping[str, object],
) -> Iterator[sqlite3.Connection]:
    """Yield a read-only SQLite connection and surface database errors."""

    database_path = normalize_database_path(database_config)
    encoded_path = quote(database_path.as_posix(), safe="/:\\")
    uri = f"file:{encoded_path}?mode=ro"

    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DatabaseReadError("could not open the SQLite database") from exc

    try:
        connection.row_factory = sqlite3.Row
        yield connection
    except sqlite3.Error as exc:
        raise DatabaseReadError("could not read the SQLite database") from exc
    finally:
        connection.close()
