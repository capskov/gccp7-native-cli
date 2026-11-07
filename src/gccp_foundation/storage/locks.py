from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from gccp_foundation.domain.errors import ErrorCategory, ErrorDetail, FoundationError


@contextmanager
def execution_lock(state_root: Path, project_file_id: str):
    """One configured application state root, never a per-job run directory."""
    directory = Path(state_root) / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for name in ("desktop", hashlib.sha256(project_file_id.encode()).hexdigest()):
            stream = (directory / (name + ".lock")).open("a+b")
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                raise FoundationError(ErrorDetail("EXECUTION_BUSY", ErrorCategory.ENVIRONMENT,
                                                  "Another local execution owns the desktop/project")) from exc
            handles.append(stream)
        yield
    finally:
        for stream in reversed(handles):
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
