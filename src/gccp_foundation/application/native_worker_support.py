"""Reusable core exports for the scoped native CLI worker, with no project constants."""
from gccp_foundation.storage import Ledger
from gccp_foundation.execution.session import SessionManager
from gccp_foundation.adapters.native_windows.desktop import WindowsDesktop
from gccp_foundation.execution.profiles import RuntimeProfile, file_sha256
from gccp_foundation.storage.locks import execution_lock
from gccp_foundation.domain.json_values import digest
from gccp_foundation.domain.models import SnapshotRef


class Cancellation:
    def is_cancelled(self):
        return False


def snap(model):
    value = digest(dict(headers=model["headers"], rows=model["rows"]))
    return SnapshotRef("budget." + value[:24], value)


def table(capture, label):
    candidates = [t for t in capture['tables'] if len(t['headers']) > 4 and t['headers'][4] == label]
    if len(candidates) != 1 or not candidates[0]['complete']:
        raise ValueError('Complete unique business model required: ' + label)
    return candidates[0]


def rows(model):
    return {r['row_path_session_only']: r for r in model['rows']}


def project_window_prefix(path):
    from pathlib import Path
    path = Path(path).resolve()
    return f"{path.stem} - {path.parent.as_posix()} - "
