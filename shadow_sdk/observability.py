from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


class OperationContextError(ValueError):
    """Raised when cross-service correlation identifiers are malformed."""


def _identifier(label: str, value: str | None, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise OperationContextError(f"{label} is required")
        return None
    if _SAFE_ID.fullmatch(value) is None:
        raise OperationContextError(f"{label} must be a 1-160 character safe identifier")
    return value


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Identifiers shared by logs, evidence, receipts, and transport boundaries.

    ``correlation_id`` follows one logical workflow across retries. ``request_id``
    identifies one transport attempt, ``trace_id`` identifies one trace, and
    ``causation_id`` points to the upstream request or event. ``run_id`` identifies
    the bounded build, probe, or restore-verification run.
    """

    run_id: str
    correlation_id: str
    trace_id: str
    request_id: str
    causation_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        for label in ("run_id", "correlation_id", "trace_id", "request_id"):
            _identifier(label, getattr(self, label))
        _identifier("causation_id", self.causation_id, required=False)
        _identifier("idempotency_key", self.idempotency_key, required=False)

    @classmethod
    def create(
        cls,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        request_id: str | None = None,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OperationContext:
        request = request_id or f"req-{uuid.uuid4()}"
        return cls(
            run_id=run_id or f"run-{uuid.uuid4()}",
            correlation_id=correlation_id or request,
            trace_id=uuid.uuid4().hex,
            request_id=request,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, str | None]) -> OperationContext:
        return cls(
            run_id=str(value.get("run_id") or ""),
            correlation_id=str(value.get("correlation_id") or ""),
            trace_id=str(value.get("trace_id") or ""),
            request_id=str(value.get("request_id") or ""),
            causation_id=value.get("causation_id"),
            idempotency_key=value.get("idempotency_key"),
        )

    def as_dict(self) -> dict[str, str]:
        result = {
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
        }
        if self.causation_id is not None:
            result["causation_id"] = self.causation_id
        if self.idempotency_key is not None:
            result["idempotency_key"] = self.idempotency_key
        return result

    def as_headers(self) -> dict[str, str]:
        headers = {
            "X-Shadow-Run-Id": self.run_id,
            "X-Correlation-Id": self.correlation_id,
            "X-Shadow-Trace-Id": self.trace_id,
            "X-Request-Id": self.request_id,
        }
        if self.causation_id is not None:
            headers["X-Causation-Id"] = self.causation_id
        if self.idempotency_key is not None:
            headers["Idempotency-Key"] = self.idempotency_key
        return headers
