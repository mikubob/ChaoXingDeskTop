"""Find the project's Python when an editor invokes a global interpreter."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def project_python(root):
    for environment in (root / '.venv', root.parent / '.venv', root / '.venv-build', root / '.venv-ci'):
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file() or python.resolve() == Path(sys.executable).resolve():
            continue
        try:
            probe = subprocess.run([str(python), '-c',
                'from PySide6.QtCore import QCoreApplication; from PySide6.QtNetwork import QLocalSocket'],
                capture_output=True, timeout=15)
            if probe.returncode == 0:
                return python
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def relaunch_if_needed():
    if getattr(sys, 'frozen', False) or importlib.util.find_spec('PySide6') is not None:
        return None
    root = Path(__file__).resolve().parents[1]
    python = project_python(root)
    if python is None:
        raise RuntimeError('当前 Python 未安装 PySide6，且未找到可用的项目虚拟环境。'
                           '请使用 desktop/.venv/Scripts/python.exe 运行，或在虚拟环境安装 desktop/requirements.txt。')
    # stdout belongs to the JSON-lines protocol in bridge/worker modes.
    if sys.stderr is not None:
        message = f'当前 Python 缺少桌面依赖，使用项目环境：{python}'
        encoding = sys.stderr.encoding or 'utf-8'
        print(message.encode(encoding, errors='backslashreplace').decode(encoding), file=sys.stderr, flush=True)
    return subprocess.call([str(python), str(root / 'run.py'), *sys.argv[1:]], cwd=root)
