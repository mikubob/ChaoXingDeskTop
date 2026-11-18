"""Bind public packages to their source/dependency and accepted payload hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.source_bundle import source_files
from scripts.release_support import validate_app_tree

RECORD = 'build-record.json'


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def source_snapshot(root):
    return {path.relative_to(root).as_posix(): digest(path) for path in source_files(root)}


def payload_snapshot(app):
    return {path.relative_to(app).as_posix(): digest(path) for path in validate_app_tree(app)
            if path.relative_to(app).as_posix() != RECORD}


def record_build(root, app, gui_verified):
    record = dict(format=2, source=source_snapshot(root), payload=payload_snapshot(app),
                  checks=['worker'] + (['gui'] if gui_verified else []))
    (app/RECORD).write_text(json.dumps(record, ensure_ascii=True, indent=2), encoding='utf-8')
    return record


def verify_build(root, app, require_gui=True):
    path = app/RECORD
    if not path.is_file():
        raise ValueError('Build record missing: rebuild and run acceptance before publishing')
    record = json.loads(path.read_text(encoding='utf-8'))
    required = {'worker', 'gui'} if require_gui else {'worker'}
    if (record.get('format') != 2 or not required.issubset(set(record.get('checks', [])))
            or record.get('source') != source_snapshot(root) or record.get('payload') != payload_snapshot(app)):
        raise ValueError('Source, payload or acceptance differs from the build record; rebuild and re-accept')
    return record


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode', choices=['record', 'verify'])
    parser.add_argument('app', type=Path)
    parser.add_argument('--gui-verified', action='store_true')
    args=parser.parse_args()
    if args.mode == 'record':record_build(ROOT,args.app.resolve(),args.gui_verified)
    else:verify_build(ROOT,args.app.resolve())
    print('Build source/payload record ' + args.mode + ' passed')
