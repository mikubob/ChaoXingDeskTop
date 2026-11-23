"""Shared public-distribution boundary for portable and installed releases."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def validate_app_tree(root):
    root = Path(root)
    forbidden = {'settings.ini', 'config.ini', 'cookies.txt', 'cookies.dat', 'accounts.json', 'credentials.json', '.env'}
    files = []
    def check(path):
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f'Unexpected link/reparse point in application: {path.name}')

    check(root)
    for directory, folders, names in os.walk(root, followlinks=False):
        for name in folders:
            path = Path(directory) / name
            check(path)
            if name.lower() == 'qmltooling' or (name.lower() == 'qml' and path.parent.name.lower() == 'pyside6'):
                raise ValueError(f'QML runtime component in application: {path.relative_to(root).as_posix()}')
        for name in names:
            path = Path(directory) / name
            check(path)
            relative = path.relative_to(root).as_posix()
            lower = name.lower()
            suffix = path.suffix.lower()
            if (lower == 'qmldir' or suffix in {'.qml', '.qmltypes'}
                    or (suffix == '.dll' and lower.startswith(('qt6qml', 'qt6quick')))
                    or (suffix == '.pyd' and lower.startswith(('qtqml', 'qtquick')))):
                raise ValueError(f'QML runtime component in application: {relative}')
            if (lower in forbidden or lower.startswith('.env.')
                    or (lower.startswith('cookies') and suffix in {'.txt', '.dat'})
                    or suffix in {'.ini', '.log', '.key', '.p12', '.pfx', '.tmp', '.bak'}
                    or (suffix == '.pem' and relative != '_internal/certifi/cacert.pem')):
                raise ValueError(f'Private or temporary file in application: {relative}')
            files.append(path)
    return sorted(files)


def verify_worker(root):
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    # 排除开发机 Python 和虚拟环境，确认安装载荷能够独立运行。
    environment['PATH'] = os.path.join(environment.get('WINDIR', 'C:/Windows'), 'System32')
    with tempfile.TemporaryDirectory(prefix='chaoxing-worker-') as data:
        command = json.dumps(dict(action='probe', config={}, data_dir=data))
        probe = subprocess.run([str(root / 'ChaoxingWorker.exe'), '--worker'], input=command,
                               capture_output=True, encoding='utf-8', timeout=30, env=environment)
    if probe.returncode != 0 or not probe.stdout.strip():
        raise RuntimeError('冻结后台探针运行失败。')
    result = json.loads(probe.stdout.splitlines()[-1])
    checks = ('captcha_ready', 'review_policy_ready', 'matching_ready', 'ocs_ready',
              'discussion_ready', 'activity_ready', 'exam_ready', 'homework_ready')
    if (result.get('type') != 'probe' or result.get('bank_protocol') != 'v2-text-and-images'
            or not all(result.get(check) is True for check in checks)):
        raise RuntimeError('冻结后台探针未通过资源与协议自检。')
    print('冻结后台探针通过，已核验题库、图片编码、OCR、讨论、活动、考试和作业模块。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='验收冻结后的 Windows 后台程序')
    parser.add_argument('app', type=Path)
    verify_worker(parser.parse_args().app.resolve())
