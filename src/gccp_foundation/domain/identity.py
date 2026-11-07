from dataclasses import asdict

from .json_values import digest
from .models import QuotaKey, ResourceIdentity


def quota_identity(key: QuotaKey) -> str:
    """Composite business identity, never a vendor database identifier."""
    return digest(asdict(key))


def line_identity(project_id: str, line_id: str) -> str:
    return digest({"project_id": project_id, "line_id": line_id})


def action_identity(project_id: str, job_key: str, action_id: str) -> str:
    return digest({"project_id": project_id, "job_key": job_key, "action_id": action_id})


def resource_identity(project_id: str, line_id: str, role: str,
                      identity: ResourceIdentity) -> str:
    return digest({"project_id": project_id, "line_id": line_id,
                   "role": role, "resource": asdict(identity)})
