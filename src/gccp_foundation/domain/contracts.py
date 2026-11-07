"""Packaged schemas plus cross-field rules. No adapter imports or side effects."""
from collections import defaultdict
from importlib.resources import files
import json
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ContractError
from .json_values import digest, freeze, loads, thaw
from .models import JobPlan

SCHEMA = json.loads(files("gccp_foundation").joinpath("resources/domain.schema.json").read_text(encoding="utf-8"))
CATALOG = json.loads(files("gccp_foundation").joinpath("resources/tools.json").read_text(encoding="utf-8"))
TOOLS = {item["name"]: item for item in CATALOG["tools"]}


def validate_schema(definition: str, data: Any) -> None:
    if definition not in SCHEMA["$defs"]:
        raise ContractError(f"Unknown schema: {definition}")
    value = thaw(freeze(data))
    validator = Draft202012Validator({**SCHEMA, "$ref": "#/$defs/" + definition})
    error = next(validator.iter_errors(value), None)
    if error:
        path = "/".join(str(part) for part in error.absolute_path)
        raise ContractError(f"{definition}/{path}: {error.message}")


def _semantic_plan(data: dict) -> tuple[str, ...]:
    actions = {a["action_id"]: a for a in data["actions"]}
    if len(actions) != len(data["actions"]):
        raise ContractError("Duplicate action_id")
    sources = {s["id"] for s in data["source_refs"]}
    if len(sources) != len(data["source_refs"]):
        raise ContractError("Duplicate source id")
    for action in actions.values():
        deps = set(action["depends_on"])
        if action["action_id"] in deps or not deps <= actions.keys():
            raise ContractError("Missing or self dependency")
    order: list[str] = []
    ancestors: dict[str, set[str]] = {}
    while len(order) != len(actions):
        ready = [key for key, a in actions.items() if key not in ancestors
                 and set(a["depends_on"]) <= ancestors.keys()]
        if not ready:
            raise ContractError("Cyclic action dependency")
        for key in ready:
            ancestors[key] = set(actions[key]["depends_on"])
            for dep in actions[key]["depends_on"]:
                ancestors[key].update(ancestors[dep])
            order.append(key)

    outputs = {
        "ensure_quota": {"budget_snapshot_ref", "resource_snapshot_ref"},
        "set_quantity": {"budget_snapshot_ref", "resource_snapshot_ref"},
        "edit_resource": {"budget_snapshot_ref", "resource_snapshot_ref", "resource_ref"},
        "set_resource_prices": {"budget_snapshot_ref", "resource_snapshot_ref"},
    }
    writes: dict[str, list[str]] = defaultdict(list)
    ensures: dict[str, dict] = {}
    final_quantities: dict[str, str] = {}

    def inspect(value, action, line_id):
        if isinstance(value, dict):
            if "source_refs" in value and not set(value["source_refs"]) <= sources:
                raise ContractError("Undefined source reference")
            if "from_action" in value:
                parent = value["from_action"]
                if parent not in ancestors[action["action_id"]]:
                    raise ContractError("Output reference must be covered by dependencies")
                if value["output"] not in outputs[actions[parent]["kind"]]:
                    raise ContractError("Referenced action does not produce this output type")
                if value["output"] == "resource_ref" and actions[parent]["line_id"] != line_id:
                    raise ContractError("Resource reference belongs to a different line")
            if "value" in value and "unit" in value and set(value) == {"value", "unit"}:
                if not value["unit"].strip():
                    raise ContractError("Blank quantity unit")
            for child in value.values():
                inspect(child, action, line_id)
        elif isinstance(value, list):
            for child in value:
                inspect(child, action, child.get("line_id", line_id) if isinstance(child, dict) else line_id)

    for key in order:
        action = actions[key]
        line = action["line_id"]
        payload = action["payload"]
        inspect(payload, action, line)
        if action["kind"] == "ensure_quota":
            if line in ensures:
                raise ContractError("Duplicate ensure_quota for one business line")
            ensures[line] = action
            if payload["quantity"]["unit"] != payload["quota_key"]["unit"]:
                raise ContractError("Quantity unit differs from quota unit", "UNIT_MISMATCH")
            if any(not str(value).strip() for field, value in payload["quota_key"].items()
                   if field not in ("chapter", "vendor_id")):
                raise ContractError("Blank quota identity field")
        if "quantity" in payload:
            final_quantities[line] = payload["quantity"]["value"]
        touched = {line}
        if action["kind"] == "set_resource_prices":
            touched = {item["line_id"] for item in payload["items"]}
            identities = [digest({"line_id": item["line_id"], "target": item["resource_target"]})
                          for item in payload["items"]]
            if len(identities) != len(set(identities)):
                raise ContractError("Duplicate price target in one batch")
        for target in touched:
            for prior in writes[target]:
                if prior not in ancestors[key]:
                    raise ContractError("Unordered writes to the same business line")
            writes[target].append(key)

    # Ensure creators precede all consumers, including actions listed before them.
    for line, creator in ensures.items():
        for key in writes[line]:
            if key != creator["action_id"] and creator["action_id"] not in ancestors[key]:
                raise ContractError("Business line used before its creator")
        unit = creator["payload"]["quota_key"]["unit"]
        for key in writes[line]:
            quantity = actions[key]["payload"].get("quantity")
            if quantity and quantity["unit"] != unit:
                raise ContractError("Quantity update changes quota unit", "UNIT_MISMATCH")
    if data["completion"]["require_positive_quantities"]:
        if "quantity" not in data["completion"]["required_scopes"]:
            raise ContractError("Positive-quantity completion requires quantity verification")
        from decimal import Decimal
        if any(Decimal(value) <= 0 for value in final_quantities.values()):
            raise ContractError("Final requested quantities must be positive")
    if data["completion"]["require_priced_resources"] and "unit_prices" not in data["completion"]["required_scopes"]:
        raise ContractError("Priced-resource completion requires unit price verification")
    return tuple(order)


def load_job_plan(data: dict | str) -> JobPlan:
    doc = loads(data) if isinstance(data, str) else thaw(freeze(data))
    validate_schema("JobPlan", doc)
    order = _semantic_plan(doc)
    return JobPlan.from_validated(doc, order)


def validate_input(tool_name: str, data: Any) -> JobPlan | dict:
    if tool_name not in TOOLS:
        raise ContractError(f"Unknown tool: {tool_name}", "UNSUPPORTED_CAPABILITY")
    definition = TOOLS[tool_name]["input_schema_ref"].split("#/$defs/")[1]
    if definition == "JobPlan":
        return load_job_plan(data)
    validate_schema(definition, data)
    return thaw(freeze(data))


def validate_response(tool_name: str, response: dict, *, plan: JobPlan | None = None) -> None:
    if tool_name not in TOOLS:
        raise ContractError(f"Unknown tool: {tool_name}", "UNSUPPORTED_CAPABILITY")
    validate_schema("Envelope", response)
    status, data, error = response["status"], response["data"], response["error"]
    if status in ("accepted", "observed", "completed"):
        if error is not None:
            raise ContractError("Successful envelope cannot contain an error")
        definition = TOOLS[tool_name]["data_schema_ref"].split("#/$defs/")[1]
        validate_schema(definition, data)
    elif data is not None:
        # Existing task state may accompany an unsuccessful job response.
        if tool_name.startswith("gccp_job_"):
            validate_schema("JobData", data)
        else:
            raise ContractError("Non-job error envelope data must be null")
    if data and tool_name == "gccp_quota_search" and status == "observed":
        if data["returned_count"] != len(data["candidates"]):
            raise ContractError("Candidate count does not match returned candidates")
        if data["complete"] and (data["returned_count"] != data["total_count"] or data["readiness"] == "incomplete"):
            raise ContractError("Incomplete candidate list cannot be claimed complete")
    if data and tool_name == "gccp_environment":
        for capability in data["capabilities"]:
            if capability["available"] and (capability["state"] != "live_verified" or not capability["evidence"]):
                raise ContractError("Available capability requires live verification evidence")
    if data and tool_name.startswith("gccp_job_") and (status == "completed" or data["state"] == "completed"):
        if plan is None:
            raise ContractError("Completed job needs its request plan for verification")
        if data["state"] != "completed" or data["remaining_scope"] or data["questions"]:
            raise ContractError("Completed job still has unfinished work")
        if data["project_id"] != plan.project_id or data["plan_digest"] != plan.plan_digest:
            raise ContractError("Completed job does not match request plan")
        summaries = data["actions"]
        if len({a["action_id"] for a in summaries}) != len(summaries) or {a["action_id"] for a in summaries} != {a.action_id for a in plan.actions}:
            raise ContractError("Completed job action coverage is incomplete")
        required_state = "persistence_verified" if "persistence" in plan.completion.required_scopes else "readback_verified"
        for summary in summaries:
            action = next(a for a in plan.actions if a.action_id == summary["action_id"])
            if summary["line_id"] != action.line_id or summary["dispatch_state"] == "outcome_unknown" or not summary["receipt_refs"]:
                raise ContractError("Completed action lacks a bound receipt or has unknown effects")
            if summary["state"] not in ({"persistence_verified"} if required_state == "persistence_verified" else {"readback_verified", "persistence_verified"}):
                raise ContractError("Completed action has not reached required verification state")
        verify_completion(data["verification"], plan.completion.required_scopes)


def verify_completion(verification: dict, required_scopes: tuple[str, ...]) -> None:
    validate_schema("Verification", verification)
    for scope in required_scopes:
        item = verification[scope]
        if item["status"] != "passed" or not item["evidence"]:
            raise ContractError(f"Required verification has no passing evidence: {scope}")
