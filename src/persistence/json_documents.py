"""Versioned JSON documents with atomic crash-safe replacement."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any


CURRENT_DOCUMENT_VERSION = 1


class JsonDocumentError(ValueError):
    """Base error for invalid or unsupported persisted JSON documents."""


class JsonDocumentSchemaError(JsonDocumentError):
    """The document does not match its declared schema."""


class UnsupportedJsonDocumentVersion(JsonDocumentError):
    """The document version cannot be read by this application."""


@dataclass(frozen=True, slots=True)
class CollectionDocumentSpec:
    schema: str
    collection_key: str
    legacy_kind: str
    current_version: int = CURRENT_DOCUMENT_VERSION

    def __post_init__(self) -> None:
        if self.legacy_kind not in {"list", "mapping"}:
            raise ValueError("legacy_kind must be 'list' or 'mapping'")


@dataclass(frozen=True, slots=True)
class LoadedCollectionDocument:
    collection: list[Any]
    requires_migration: bool


def load_collection_document(
    path: Path,
    spec: CollectionDocumentSpec,
) -> LoadedCollectionDocument:
    """Read one collection without mutating its source document."""
    raw_document = _read_json(path)
    legacy_collection = _legacy_collection(raw_document, spec)
    if legacy_collection is not None:
        return LoadedCollectionDocument(
            collection=legacy_collection,
            requires_migration=True,
        )

    if not isinstance(raw_document, dict):
        raise JsonDocumentSchemaError(f"{path.name} must contain a versioned JSON object")
    schema = raw_document.get("schema")
    if schema != spec.schema:
        raise JsonDocumentSchemaError(
            f"{path.name} declares schema {schema!r}; expected {spec.schema!r}"
        )
    version = raw_document.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise JsonDocumentSchemaError(f"{path.name} schema_version must be an integer")
    if version != spec.current_version:
        raise UnsupportedJsonDocumentVersion(
            f"{path.name} schema version {version} is unsupported; expected {spec.current_version}"
        )
    collection = raw_document.get(spec.collection_key)
    if not isinstance(collection, list):
        raise JsonDocumentSchemaError(
            f"{path.name} field {spec.collection_key!r} must be a JSON array"
        )
    return LoadedCollectionDocument(
        collection=collection,
        requires_migration=False,
    )


def migrate_collection_document(
    path: Path,
    spec: CollectionDocumentSpec,
    normalized_collection: list[Any],
) -> None:
    """Back up and replace a legacy document after domain validation succeeds."""
    _write_legacy_backup(path)
    write_collection_document(path, spec, normalized_collection)


def write_collection_document(
    path: Path,
    spec: CollectionDocumentSpec,
    collection: list[Any],
) -> None:
    if not isinstance(collection, list):
        raise TypeError("collection must be a list")
    write_json_atomic(
        path,
        {
            "schema": spec.schema,
            "schema_version": spec.current_version,
            spec.collection_key: collection,
        },
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write JSON through a same-directory temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            temporary_file = os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            )
        except Exception:
            with suppress(OSError):
                os.close(descriptor)
            raise
        with temporary_file as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        _sync_directory(path.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json_document(path: Path) -> Any:
    """Read a JSON value and translate parser failures to document errors."""
    return _read_json(path)


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as exc:
        raise JsonDocumentSchemaError(
            f"{path.name} is not valid JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _legacy_collection(
    raw_document: Any,
    spec: CollectionDocumentSpec,
) -> list[Any] | None:
    if spec.legacy_kind == "list":
        return raw_document if isinstance(raw_document, list) else None
    if (
        isinstance(raw_document, dict)
        and "schema" not in raw_document
        and set(raw_document) == {spec.collection_key}
    ):
        collection = raw_document[spec.collection_key]
        if not isinstance(collection, list):
            raise JsonDocumentSchemaError(
                f"legacy field {spec.collection_key!r} must be a JSON array"
            )
        return collection
    return None


def _write_legacy_backup(path: Path) -> None:
    backup_path = path.with_name(f"{path.name}.v0.bak")
    if backup_path.exists():
        return
    original = path.read_bytes()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{backup_path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(original)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, backup_path)
        _sync_directory(path.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
