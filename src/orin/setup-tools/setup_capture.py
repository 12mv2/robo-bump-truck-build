#!/usr/bin/env python3
# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Offline setup capture adapters. These functions never contact or actuate hardware.

Deployment: install this file and setup_store.py together in ~/live/setup-tools/
or the setup-tools/ directory beside live_trail.py. A
missing adapter/store is an error, never permission to launch without capture.
"""
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

try:
    from . import setup_store
except ImportError:
    import setup_store


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    """Keep unknown/invalid observations explicit and strict-JSON serializable."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {'status': 'unknown', 'reason': 'nonfinite observation'}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return {'status': 'unknown', 'reason': 'not JSON serializable', 'type': type(value).__name__}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(json_safe(value), stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def unknown(reason):
    return {'status': 'unknown', 'reason': reason}


def _store_path(store, home=None):
    return Path(store or os.environ.get('SETUP_STORE') or (Path(home or Path.home()) / 'live/setup-store')).expanduser().resolve()


def _file(files, path, role, required=True):
    if not path:
        return
    path = Path(path).expanduser().resolve()
    existing = next((row for row in files if row['path'] == str(path)), None)
    if existing:
        existing['required'] = existing['required'] or required
        if role not in existing['role'].split(','):
            existing['role'] += ',' + role
    else:
        files.append({'path': str(path), 'target_path': str(path), 'role': role, 'required': required})


def _path_value(value, source, home):
    value = value.strip().split(' #', 1)[0].strip()
    if value.startswith(('"', "'")):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(value, str) or not value or value.lower() in ('none', 'null', 'false', 'true'):
        return None
    if value.startswith('~/'):
        return Path(home) / value[2:]
    path = Path(value)
    return path if path.is_absolute() else Path(source).parent / path


def _config_references(files, home):
    """Preserve active path scalars without loading YAML or invoking runtime code."""
    checked = set()
    while True:
        pending = [row for row in files if row['path'] not in checked
                   and Path(row['path']).suffix.lower() in ('.yaml', '.yml', '.json', '.conf')]
        if not pending:
            break
        for row in pending:
            checked.add(row['path'])
            path = Path(row['path'])
            if not path.is_file():
                continue
            text = path.read_text(errors='replace')
            for match in re.finditer(r'(?m)^[ \t]*([A-Za-z_][\w]*):[ \t]*([^\n]+)$', text):
                key, raw = match.groups()
                if (key.endswith(('_path', '_file', '_yaml')) or key in ('model', 'config', 'intrinsics', 'extrinsics')):
                    referenced = _path_value(raw, path, home)
                    if referenced:
                        _file(files, referenced, 'config-reference:' + key, required=row['required'])


def python_closure(files, loaded_modules=None):
    """Copy local import source bytes; inventory external imports separately.

    Static local imports include conditionally loaded owner/recovery modules even
    before a frame uses them. Loaded module source paths include the actual DINO
    hub implementation; package binaries remain environment inventory.
    """
    checked = set()
    inventory = []
    for name, module in (loaded_modules or {}).items():
        # Module __getattr__ can create lazy objects (notably torch.classes).
        # Inspect stored metadata without executing those hooks or object reprs.
        namespace = vars(module) if module is not None else {}
        path = namespace.get('__file__')
        if not isinstance(path, str) or not path:
            continue
        version = namespace.get('__version__', 'unknown')
        version = str.__str__(version) if isinstance(version, str) else (
            str(version) if type(version) in (int, float) else 'unknown')
        row = {'module': name, 'path': str(Path(path).resolve()),
               'version': version}
        inventory.append(row)
        # The hub checkout is runtime source, unlike installed framework packages.
        if path.endswith('.py') and ('dinov2' in name or '/hub/' in path):
            _file(files, path, 'loaded-backbone-source')
    while True:
        pending = [row for row in files if row['path'] not in checked and row['path'].endswith('.py')]
        if not pending:
            break
        for row in pending:
            checked.add(row['path'])
            path = Path(row['path'])
            if not path.is_file():
                continue
            try:
                tree = ast.parse(path.read_text())
            except (ValueError, SyntaxError, UnicodeError):
                continue
            for node in ast.walk(tree):
                names = [item.name for item in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    parts = name.split('.')
                    candidate = path.parent.joinpath(*parts).with_suffix('.py')
                    package = path.parent.joinpath(*parts, '__init__.py')
                    if candidate.is_file():
                        _file(files, candidate, 'runtime-import')
                    elif package.is_file():
                        _file(files, package, 'runtime-import')
    return sorted(inventory, key=lambda row: row['module'])


def _external_checks(effective=None, physical_notes=None):
    effective = effective or {}
    effective = dict(effective, physical=physical_notes or unknown('physical notes/photo not supplied'))
    return [dict(id=name, kind=kind, description=description,
                 expected=json_safe(effective.get(name, unknown('not yet observed'))), verification='manual')
            for name, kind, description in (
                ('fc', 'flight_controller', 'Read relevant FC parameters and confirm against this run.'),
                ('phone', 'phone', 'Confirm phone camera resolution, FPS and stream configuration.'),
                ('radio', 'radio', 'Confirm radio model/mixes/calibration; effective per-frame values are historical evidence.'),
                ('physical', 'physical', 'Confirm lens mount, axle/servo/mechanical setup and battery/surface notes.'))]


def _capture(store, files, metadata, run_dir=None):
    record = setup_store.capture_snapshot(store, files, json_safe(metadata))
    if not record.get('file_capture_complete', record.get('complete')):
        raise RuntimeError('setup capture failed: required file bytes missing: ' + str(record.get('missing_required')))
    # store path is a locator for subsequent observation receipts, outside identity.
    result = dict(record, store=str(store))
    if run_dir is not None:
        atomic_json(Path(run_dir) / 'setup-snapshot.json', result)
    return result


def _physical_files(files, notes):
    if not isinstance(notes, dict):
        return
    for key in ('photo', 'photo_path', 'photos'):
        paths = notes.get(key) or []
        for path in paths if isinstance(paths, (list, tuple)) else [paths]:
            _file(files, path, 'physical-reference-photo')


def capture_controller_start(args, runtime_path, hub_dir, fc, phone, pitch, pitch_live):
    """Shared startup capture after model load and before output-owner creation.

    Receives runtime state from its existing owners; never imports model libraries,
    opens hardware or starts a controller. Relay-steered controller children and standalone
    DINO use the same capture recipe, with capture_dino_run retaining their identity.
    """
    if not args.log:
        args.log = str(Path.home() / 'live/runs' /
                       ('setup-' + time.strftime('%Y%m%dT%H%M%S') + '-' + str(os.getpid())))
    hub_dir = Path(hub_dir)
    runtime_files = {'controller': runtime_path,
                     'backbone-checkpoint': str(hub_dir / 'checkpoints/dinov2_vits14_pretrain.pth'),
                     'setup-tools': [str(path) for path in Path(__file__).parent.glob('*.py')],
                     'backbone-source': [str(path) for checkout in hub_dir.glob('facebookresearch_dinov2*')
                                         for path in checkout.rglob('*.py')]}
    for runtime_name in ('auto.conf', 'start.sh', 'auto_boot.sh', 'STREAM_FPS'):
        path = Path.home() / 'live' / runtime_name
        if path.is_file():
            runtime_files['launch-' + runtime_name] = str(path)
    recovery = getattr(args, 'recovery_config', None)
    phone = phone or {}
    return capture_dino_run(
        args.log, {**vars(args), 'recovery_config': asdict(recovery) if recovery else None},
        runtime_files, loaded_modules=dict(sys.modules), argv=list(sys.argv),
        effective={'fc': fc.setup_parameters if fc is not None else {},
                   'phone': {**(phone.get('status') or {}), 'video_config': phone.get('video_config'),
                             'hello': phone.get('hello')},
                   'radio': fc.poll() if fc is not None else {},
                   'preprocess': {'mean': [0.485, 0.456, 0.406], 'std': [0.229, 0.224, 0.225]},
                   'geometry': {'pitch': pitch, 'pitch_live': pitch_live, 'cam_h': args.cam_h}})


def capture_dino_run(run_dir, args, runtime_files, loaded_modules=None, effective=None,
                     physical_notes=None, store=None, argv=None):
    """Capture DINO startup state before sending; runtime_files must name backbone."""
    args = vars(args) if hasattr(args, '__dict__') else dict(args)
    if not any('backbone' in role or 'checkpoint' in role for role in runtime_files):
        raise RuntimeError('setup capture requires the actual loaded DINO backbone checkpoint')
    files = []
    for role, paths in runtime_files.items():
        for path in paths if isinstance(paths, (list, tuple)) else [paths]:
            _file(files, path, role)
    for key in ('head', 'trail_head', 'danger_head', 'path', 'cell_plan', 'reverse_profile'):
        _file(files, args.get(key), 'dino-argument:' + key)
    _file(files, Path.home() / 'live/STREAM_FPS', 'phone-stream-setting', required=False)
    _config_references(files, Path.home())
    inventory = python_closure(files, loaded_modules)
    _physical_files(files, physical_notes)
    effective = effective or {}
    metadata = dict(scenario='dino', reason='before-run', run_dir=str(run_dir),
                    argv=list(sys.argv if argv is None else argv), resolved_args=args,
                    arguments=args,
                    launch={'kind': 'dino', 'argv': list(sys.argv if argv is None else argv),
                            'entrypoint': str(Path((sys.argv if argv is None else argv)[0]).resolve()),
                            'cwd': str(Path.cwd()), 'bundle': None, 'throttle_profile': None},
                    effective=effective, runtime_imports=inventory,
                    physical=physical_notes or unknown('physical mount, servo/mechanics and photo not provided'),
                    observation_gaps=[key for key in ('phone', 'fc', 'radio', 'intrinsics', 'extrinsics', 'preprocess') if key not in effective],
                    external_checks=_external_checks(effective, physical_notes),
                    setup_equality='file bytes captured; phone/FC/radio/physical require separate verification')
    if args.get('relay_mode') == 'steer':
        # A relay-steered controller child cannot recreate the ROS perception processes by
        # itself. Its parent snapshot owns that launch; retain this as readback.
        metadata['scenario'] = 'relay-controller'
        metadata['launch']['kind'] = 'relay-controller'
        parent_snapshot = Path(run_dir).parent / 'setup-snapshot.json'
        metadata['parent_snapshot_id'] = (json.loads(parent_snapshot.read_text()).get('id')
                                          if parent_snapshot.is_file() else None)
    if args.get('path'):
        metadata['external_checks'].append({
            'id': 'taught_path_frame', 'kind': 'taught_path_frame',
            'description': 'An old path file does not restore its ARKit coordinate frame; re-teach or verify registration.',
            'expected': unknown('new-session ARKit registration not established by file restoration')})
    return _capture(_store_path(store), files, metadata, run_dir)


def _summarize(state, value, prefix=''):
    """Accumulate scalar ranges for effective settings with no invented defaults."""
    if isinstance(value, dict):
        for key, child in value.items():
            _summarize(state, child, prefix + '.' + str(key) if prefix else str(key))
    elif isinstance(value, (str, bool, int, float)) and not (isinstance(value, float) and not math.isfinite(value)):
        row = state.setdefault(prefix, {'first': value, 'last': value, 'samples': 0})
        row['last'] = value
        row['samples'] += 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            row['min'] = min(row.get('min', value), value)
            row['max'] = max(row.get('max', value), value)


def _journal_summary(path):
    result = {'frames': 0, 'partial_or_invalid_lines': 0, 'effective_ranges': {}, 'startup_events': []}
    if not path.is_file():
        return result
    with path.open() as source:
        for line in source:
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                result['partial_or_invalid_lines'] += 1
                continue
            if not isinstance(row, dict):
                continue
            if row.get('event') == 'start':
                result['startup_events'].append(row)
            if 'n' in row and not row.get('event'):
                result['frames'] += 1
                _summarize(result['effective_ranges'], row)
    return result


def observe_run(run_dir, snapshot, frame=None, receipt=None, final=False, outcome=None,
                journal=None, effective=None):
    """Durable evolving observation, separate from immutable startup identity.

    Flush per observed frame; raw JSONL remains authoritative for every sample.
    Abrupt power loss leaves startup snapshot + latest atomic observation intact.
    """
    run_dir = Path(run_dir)
    path = run_dir / 'setup-observed.json'
    state = json.loads(path.read_text()) if path.exists() else dict(
        schema_version=1, snapshot_id=snapshot['id'], started_at=utc_now(),
        complete=False, observed_frames=0, effective_ranges={},
        unobserved=['phone FPS/resolution until stream metadata is logged',
                    'physical mount/photo and radio configuration unless operator supplied'])
    if state['snapshot_id'] != snapshot['id']:
        raise RuntimeError('observation receipt belongs to another setup snapshot')
    frame = frame or {}
    if frame and frame.get('n') != state.get('last_frame_n'):
        state['observed_frames'] += 1
        state['last_frame_n'] = frame.get('n')
        state['last_frame_epoch'] = frame.get('t')
        state['latest_frame'] = json_safe(frame)
        # Include effective per-frame lookahead/gain/pitch, receiver PWM, owner
        # values, phone metadata and output profile exactly as the logger names them.
        _summarize(state['effective_ranges'], frame)
    if receipt is not None:
        state['launcher_receipt'] = json_safe(receipt)
    if effective is not None:
        state['effective_startup'] = json_safe(effective)
    journal = Path(journal or (run_dir / 'frames.jsonl'))
    sources = [journal, *sorted(run_dir.glob('*.log'))]
    for nested in ('controller-run/setup-snapshot.json', 'controller-run/setup-observed.json'):
        if (run_dir / nested).is_file():
            sources.append(run_dir / nested)
    state['sources'] = [{'path': str(source.resolve()), 'exists': source.exists(),
                         'size_bytes': source.stat().st_size if source.exists() else None,
                         'mtime_epoch': source.stat().st_mtime if source.exists() else None} for source in sources]
    state['effective_ranges_scope'] = 'sampled launcher frames; raw journal records every logged frame'
    if final:
        state['journal_summary'] = _journal_summary(journal)
        state['effective_ranges'] = state['journal_summary']['effective_ranges']
        state['effective_ranges_scope'] = 'all complete frame records in referenced journal'
    state['updated_at'] = utc_now()
    state['complete'] = bool(final)
    if outcome is not None:
        state['outcome'] = json_safe(outcome)
    atomic_json(path, state)
    if final:
        store = snapshot.get('store') or _store_path(None)
        setup_store.write_receipt(store, 'run-observation', state)
        # A nonactuating field "check" is not a tested model/controller run.
        metadata = snapshot.get('metadata', {})
        args = metadata.get('arguments', {})
        failed = bool((receipt or {}).get('error') or (receipt or {}).get('phone_cleanup_error')
                      or (isinstance(outcome, dict) and outcome.get('error')))
        if (metadata.get('reason') == 'before-run' and not args.get('startup_check') and not failed
                and state.get('journal_summary', {}).get('frames', 0) > 0):
            setup_store.update_ref(store, metadata.get('scenario', 'unknown'), snapshot['id'], 'last_tested')
    return state


def observe_dino_run(run_dir, snapshot, frame=None, final=False, outcome=None, effective=None):
    return observe_run(run_dir, snapshot, frame=frame, final=final, outcome=outcome, effective=effective)
