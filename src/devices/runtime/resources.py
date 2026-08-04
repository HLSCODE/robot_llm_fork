from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

from .models import ResourceBusyError


@dataclass(slots=True)
class ResourceLease:
    lease_id: str
    owner_id: str
    resources: tuple[str, ...]
    _arbiter: "ResourceArbiter"
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._arbiter.release(self)
        self._released = True

    def __enter__(self) -> "ResourceLease":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class ResourceArbiter:
    """Grant exclusive, non-blocking leases for hardware resources."""

    def __init__(self) -> None:
        self._owners: dict[str, tuple[str, str]] = {}
        self._lock = RLock()

    def acquire(
        self,
        owner_id: str,
        resources: tuple[str, ...] | list[str] | set[str],
    ) -> ResourceLease:
        normalized = tuple(sorted(set(resources)))
        if not owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if not normalized:
            raise ValueError("at least one resource is required")

        with self._lock:
            for resource_id in normalized:
                current = self._owners.get(resource_id)
                if current is not None:
                    current_owner, _lease_id = current
                    raise ResourceBusyError(resource_id, current_owner)

            lease_id = uuid4().hex
            for resource_id in normalized:
                self._owners[resource_id] = (owner_id, lease_id)

        return ResourceLease(
            lease_id=lease_id,
            owner_id=owner_id,
            resources=normalized,
            _arbiter=self,
        )

    def release(self, lease: ResourceLease) -> None:
        with self._lock:
            for resource_id in lease.resources:
                current = self._owners.get(resource_id)
                if current == (lease.owner_id, lease.lease_id):
                    del self._owners[resource_id]

    def owner_of(self, resource_id: str) -> str | None:
        with self._lock:
            current = self._owners.get(resource_id)
            return current[0] if current else None

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {
                resource_id: owner_id
                for resource_id, (owner_id, _lease_id) in self._owners.items()
            }
