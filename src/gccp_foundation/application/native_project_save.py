"""Dispatch the normal Save action once and verify the open project and disk."""
from contextlib import closing
import json
from pathlib import Path
import re
import time
import uuid

from .native_build import TEMPLATES, compile_bridge, profile_path
from .native_cli import Rejected, digest, encoded, initialize_commands, save_timeout_seconds
from .native_delete import load_bridge, save_result
from .native_runtime import render_transport
from .native_worker_support import (
    Ledger, RuntimeProfile, SessionManager, WindowsDesktop, execution_lock, file_sha256,
    project_window_prefix, table, wait_for_saved_workcopy,
)
from .ports import SessionContext
from gccp_foundation.storage.native_save_attempts import NativeSaveAttempts


def save_project(state, runtime_root, project_id, command_id, timeout, *, check_only=False):
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', command_id):
        raise Rejected('IDENTIFIER_INVALID', 'Provide a bounded command ID')
    timeout = save_timeout_seconds(timeout)
    signature = digest(dict(operation='project-save', project_id=project_id, command_id=command_id))
    with closing(Ledger(state)) as store:
        initialize_commands(store)
        project = store.db.execute('SELECT * FROM projects WHERE project_id=?', (project_id,)).fetchone()
        if project is None:
            raise Rejected('PROJECT_NOT_BOUND', 'Bind the current project first')
        with execution_lock(state, project['file_id']):
            prior = store.db.execute('SELECT * FROM native_cli_commands WHERE command_id=?', (command_id,)).fetchone()
            if prior:
                if prior['request_digest'] == signature and prior['phase'] == 'readback_verified':
                    return {**json.loads(prior['result']), 'receipt_only': True, 'new_save_dispatches': 0}
                raise Rejected('COMMAND_ALREADY_RECORDED', 'Inspect the original Save receipt before continuing')
            ctx = SessionContext(**json.loads(project['metadata'])['session'])
            profile = profile_path(ctx.profile_id)
            manager = SessionManager(store, WindowsDesktop(), RuntimeProfile.load(profile))
            meta = manager.validate_session(ctx)
            if (meta.get('dirty') or store.load_pending(project_id) or NativeSaveAttempts(store).active(project_id)
                    or store.db.execute("SELECT 1 FROM native_cli_commands WHERE project_id=? AND phase='armed_unknown'",
                                        (project_id,)).fetchone()):
                raise Rejected('PROJECT_NEEDS_RECONCILIATION', 'Resolve the pending operation before Save')
            path = Path(meta['workcopy_path'])
            directory = Path(runtime_root).resolve() / 'project-save' / uuid.uuid4().hex
            source = render_transport(TEMPLATES, directory, command_id, meta['session'], profile,
                                      project_window_prefix(path))
            source['operations.h'] = (TEMPLATES / 'project_save.h.in').read_text(encoding='utf-8')
            directory.mkdir(parents=True)
            for filename, content in source.items():
                (directory / filename).write_text(content, encoding='utf-8', newline='\n')
            compile_bridge(directory)
            bridge = load_bridge(directory)
            before = bridge.call('inspect', timeout=25)
            budget = table(before, '工程量表达式')
            bridge.call('save.check', timeout=25)
            if check_only:
                return dict(status='checked', save_dispatches=0, budget_rows=len(budget['rows']))
            manager.validate_session(ctx)
            original_hash = file_sha256(path)
            intent = 'intent.' + uuid.uuid4().hex
            proof = store.put_evidence(project_id, ctx.session_id, 'native_project_save_preflight',
                                       dict(capture=before, sha256=original_hash, intent_id=intent))
            result = dict(command_id=command_id, status='armed_unknown', runtime=str(directory),
                          preflight_evidence_id=proof)
            with store._transaction():
                store.db.execute('INSERT INTO native_cli_commands VALUES(?,?,?,?,?,?)',
                                 (command_id, project_id, signature, 'armed_unknown',
                                  encoded(result), int(time.time()*1000)))
            try:
                dispatch = bridge.call('save', intent, timeout=30)
                if dispatch.get('business_dispatches') != 1:
                    raise ValueError('Save dispatch acknowledgement missing')
                result.update(save_dispatches=1, ipc_records=bridge.records)
                save_result(store, result)
                saved_hash = wait_for_saved_workcopy(path, original_hash, timeout=timeout)
                after = bridge.call('inspect', timeout=30)
                if table(after, '工程量表达式')['rows'] != budget['rows'] or file_sha256(path) != saved_hash:
                    raise ValueError('The saved file or open budget changed during verification')
                manager.reconcile_open_native_save(ctx, saved_hash)
                evidence = store.put_evidence(project_id, ctx.session_id, 'native_project_save_readback',
                    dict(capture=after, dispatch=dispatch, sha256=saved_hash, preflight_evidence_id=proof))
                result.update(status='native_project_save_verified', path=str(path), sha256=saved_hash,
                              budget_rows=len(budget['rows']), evidence_id=evidence, ipc_records=bridge.records)
                save_result(store, result, 'readback_verified')
                return result
            except (ValueError, OSError, RuntimeError) as exc:
                result.update(error=str(exc), ipc_records=bridge.records)
                save_result(store, result)
                raise Rejected('NEEDS_RECONCILIATION', 'Inspect the original Save without redispatching') from exc
