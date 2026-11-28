from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from gccp_foundation.domain.errors import ErrorCategory, ErrorDetail, FoundationError
from gccp_foundation.domain.json_values import loads


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    launcher: str
    process_executable: str
    library: str
    edition: str
    trade: str
    fingerprints: tuple[tuple[str, str], ...]

    @classmethod
    def load(cls, path):
        data = loads(Path(path).read_text(encoding="utf-8-sig"))
        required = {"profile_id", "launcher", "process_executable", "library", "edition", "trade", "fingerprints"}
        if set(data) != required or not data["fingerprints"]:
            raise ValueError("Incomplete or unknown profile fields")
        if data["profile_id"] == "gccp7-bj2025-local-session" and os.environ.get("GCCP7_INSTALL_ROOT"):
            original = Path(r"C:\Program Files\Glodon\GCCP\7.0-X64")
            root = Path(os.environ["GCCP7_INSTALL_ROOT"])
            if not root.is_absolute():
                raise ValueError("GCCP7_INSTALL_ROOT must be absolute")
            def relocated(value):
                return str((root / Path(value).relative_to(original)).resolve())

            data["launcher"] = relocated(data["launcher"])
            data["process_executable"] = relocated(data["process_executable"])
            data["fingerprints"] = {relocated(value): sha for value, sha in data["fingerprints"].items()}
        return cls(*(data[k] for k in ("profile_id", "launcher", "process_executable", "library", "edition", "trade")),
                   tuple((str(Path(p).resolve()), sha.lower()) for p, sha in data["fingerprints"].items()))

    def verify(self):
        matches = []
        for path, expected in self.fingerprints:
            if not Path(path).is_file():
                raise FoundationError(ErrorDetail("PROFILE_MISMATCH", ErrorCategory.VERSION,
                    f"Missing GCCP component: {path}. Set GCCP7_INSTALL_ROOT for a non-default installation."))
            actual = file_sha256(path)
            if actual != expected:
                raise FoundationError(ErrorDetail("PROFILE_MISMATCH", ErrorCategory.VERSION, f"Component mismatch: {Path(path).name}"))
            matches.append(dict(path=path, sha256=actual))
        declared = {str(Path(p).resolve()).casefold() for p, _ in self.fingerprints}
        if any(str(Path(p).resolve()).casefold() not in declared for p in (self.launcher, self.process_executable)):
            raise FoundationError(ErrorDetail("PROFILE_MISMATCH", ErrorCategory.VERSION, "Launcher and worker must both be pinned"))
        return matches


def environment(desktop, profile):
    components = profile.verify()
    processes = desktop.processes()
    monitors = [p for p in processes if p["name"].casefold() == "procmon64.exe"]
    return dict(profile_id=profile.profile_id, fingerprints=components,
                interactive_desktop=desktop.interactive_state(), license_state="unknown",
                monitor_state="process_only" if monitors else "not_running", monitor_processes=monitors,
                windows=desktop.windows(), production_probe_default=False,
                runtime_write_available=False)
