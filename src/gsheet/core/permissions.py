"""Explicit connection-local request grants; OAuth scopes are not these grants."""

from dataclasses import dataclass, field
from enum import StrEnum

from ..config import ConfigurationError
from .exceptions import APIClientError, APIErrorCode


class OperationKind(StrEnum):
    READ = "read"
    WRITE = "write"
    STRUCTURAL = "structural"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Trusted application supplies grants; empty defaults deny all requests.

    Mutations need WRITE; structural batches also need STRUCTURAL, and destructive
    requests additionally need DESTRUCTIVE. allowed_batch_requests names the
    supported Google batch request types permitted in this connection.
    This does not enforce business identity, tab/cell ACLs, or external editors.
    """

    allowed_spreadsheet_ids: frozenset[str] = field(default_factory=frozenset, repr=False)
    allowed_operations: frozenset[OperationKind] = frozenset({OperationKind.READ})
    allowed_batch_requests: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("allowed_spreadsheet_ids", "allowed_operations", "allowed_batch_requests"):
            value = getattr(self, name)
            if not isinstance(value, (set, frozenset)):
                raise ConfigurationError(name, "supply a set of explicit grants")
            if name == "allowed_operations":
                valid = all(isinstance(item, OperationKind) for item in value)
            else:
                valid = all(isinstance(item, str) and item.strip() for item in value)
            if not valid:
                raise ConfigurationError(name, "invalid grant type")
            object.__setattr__(self, name, frozenset(value))

    def authorize(self, spreadsheet_id: str, operations: frozenset[OperationKind],
                  batch_requests: frozenset[str] = frozenset()) -> None:
        if (spreadsheet_id not in self.allowed_spreadsheet_ids
                or not operations <= self.allowed_operations
                or not batch_requests <= self.allowed_batch_requests):
            raise APIClientError(APIErrorCode.PERMISSION_DENIED, outcome="validation_rejection")
