"""Bundled source assets and configurable local x64 build tools."""
import os
from pathlib import Path
import re
import shutil
import subprocess

from gccp_foundation.application.native_cli import Rejected


RESOURCES = Path(__file__).resolve().parents[1] / 'resources'
TEMPLATES = RESOURCES / 'native'
PROFILE_ID = 'gccp7-bj2025-local-session'


def profile_path(profile_id=PROFILE_ID):
    if profile_id != PROFILE_ID:
        raise Rejected('PROFILE_UNSUPPORTED', 'This release supports only the bundled reviewed GCCP7 profile')
    return RESOURCES / 'profiles' / (profile_id + '.json')


def toolchain():
    paths = {}
    for name in ('GCCP7_MSVC_ROOT', 'GCCP7_WINDOWS_SDK_ROOT', 'GCCP7_QT_ROOT'):
        p = Path(os.environ.get(name, ''))
        if not p.is_absolute() or not p.is_dir():
            raise Rejected('BUILD_ENVIRONMENT_MISSING', f'Set {name} to an absolute existing directory')
        paths[name] = p
    vc, sdk, qt = paths.values()
    version = os.environ.get('GCCP7_WINDOWS_SDK_VERSION', '')
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise Rejected('BUILD_ENVIRONMENT_MISSING', 'Set GCCP7_WINDOWS_SDK_VERSION')
    required = [vc/'bin/Hostx64/x64'/name for name in ('cl.exe', 'dumpbin.exe')]
    required += [sdk/'Include'/version/name for name in ('ucrt', 'shared', 'um')]
    required += [qt/'lib'/f'Qt5{lib}.lib' for lib in ('Core','Gui','Widgets')]
    if any(not p.exists() for p in required):
        raise Rejected('BUILD_ENVIRONMENT_MISSING', 'Configured MSVC, Windows SDK or Qt SDK is incomplete')
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell or os.name != 'nt':
        raise Rejected('BUILD_ENVIRONMENT_MISSING', 'Native execution requires Windows PowerShell or pwsh')
    return shell


def compile_bridge(directory):
    shell = toolchain()
    built = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(directory/'build.ps1')],
                           cwd=directory, capture_output=True, timeout=120, creationflags=0x08000000)
    (directory/'build.log').write_bytes(built.stdout + built.stderr)
    if built.returncode:
        raise Rejected('RUNTIME_BUILD_FAILED', 'No application action dispatched. Inspect ' + str(directory/'build.log'))
