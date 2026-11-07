from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .json_values import decimal_text, digest, freeze, thaw


class ActionKind(StrEnum):
    ENSURE_QUOTA = "ensure_quota"
    SET_QUANTITY = "set_quantity"
    EDIT_RESOURCE = "edit_resource"
    SET_RESOURCE_PRICES = "set_resource_prices"


@dataclass(frozen=True)
class Quantity:
    value: str
    unit: str

    def __post_init__(self):
        object.__setattr__(self, "value", decimal_text(self.value))
        if not isinstance(self.unit, str) or not self.unit.strip():
            from .errors import ContractError
            raise ContractError("Quantity requires a nonblank unit")

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.value)


@dataclass(frozen=True)
class QuotaKey:
    library: str
    edition: str
    trade: str
    code: str
    name: str
    unit: str
    chapter: str | None = None
    vendor_id: str | None = None


@dataclass(frozen=True)
class ResourceIdentity:
    code: str
    name: str
    specification: str
    unit: str


@dataclass(frozen=True)
class SnapshotRef:
    id: str
    sha256: str


@dataclass(frozen=True)
class Action:
    action_id: str
    line_id: str
    kind: ActionKind
    depends_on: tuple[str, ...]
    # The discriminator-specific payload is validated before freezing.
    payload: Mapping[str, Any]

    def to_dict(self) -> dict:
        return dict(action_id=self.action_id, line_id=self.line_id, kind=str(self.kind),
                    depends_on=list(self.depends_on), payload=thaw(self.payload))


@dataclass(frozen=True)
class Completion:
    required_scopes: tuple[str, ...]
    require_positive_quantities: bool
    require_priced_resources: bool


@dataclass(frozen=True)
class JobPlan:
    project_id: str
    job_key: str
    plan_revision: int
    baseline_ref: SnapshotRef
    actions: tuple[Action, ...]
    completion: Completion
    source_refs: tuple[Mapping[str, Any], ...]
    execution_order: tuple[str, ...]
    _document: Mapping[str, Any]

    @classmethod
    def from_validated(cls, data: dict, order: tuple[str, ...]) -> "JobPlan":
        return cls(data["project_id"], data["job_key"], data["plan_revision"],
                   SnapshotRef(**data["baseline_ref"]),
                   tuple(Action(a["action_id"], a["line_id"], ActionKind(a["kind"]),
                                tuple(a["depends_on"]), freeze(a["payload"])) for a in data["actions"]),
                   Completion(tuple(data["completion"]["required_scopes"]),
                              data["completion"]["require_positive_quantities"],
                              data["completion"]["require_priced_resources"]),
                   tuple(freeze(s) for s in data["source_refs"]), order, freeze(data))

    def to_dict(self) -> dict:
        return thaw(self._document)

    @property
    def plan_digest(self) -> str:
        doc = self.to_dict()
        # Routing/idempotency keys and presentation are not execution content.
        for name in ("job_key", "plan_revision", "reasoning_ref", "presentation"):
            doc.pop(name, None)
        for action in doc["actions"]:
            action["depends_on"].sort()
            payload = action["payload"]
            if "quantity" in payload:
                payload["quantity"]["value"] = decimal_text(payload["quantity"]["value"])
            if "changes" in payload and "consumption" in payload["changes"]:
                payload["changes"]["consumption"] = decimal_text(payload["changes"]["consumption"])
            for item in payload.get("items", []):
                item["price"] = decimal_text(item["price"])
        return digest(doc)
