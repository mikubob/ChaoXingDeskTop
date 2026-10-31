"""Launch the native frontend; never silently fall back to another UI."""
import os
from pathlib import Path
import subprocess
import sys


def find_frontend(root):
    candidates = list((root / 'winui').glob('publish*/ChaoxingDesktop.exe'))
    candidates.extend((root / 'winui').glob('dev*/ChaoxingDesktop.exe'))
    candidates.append(root / 'winui/bin/x64/Release/net8.0-windows10.0.19041.0/win-x64/ChaoxingDesktop.exe')
    required = ('ChaoxingDesktop.dll', 'App.xbf', 'MainWindow.xbf', 'ChaoxingDesktop.pri')
    complete = [path for path in candidates if path.is_file() and
                all(path.with_name(name).is_file() for name in required)]
    return max(complete, key=lambda path: path.with_suffix('.dll').stat().st_mtime_ns, default=None)


def activate_existing():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    from .config import data_dir
    from .instance import InstanceGuard

    app = QCoreApplication.instance() or QCoreApplication([])
    guard = InstanceGuard(data_dir(), app)
    socket = QLocalSocket()
    socket.connectToServer(guard.name)
    if not socket.waitForConnected(200):
        return False
    socket.write(b'activate\n')
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def main():
    root = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).with_name('ChaoxingDesktop.exe') if getattr(sys, 'frozen', False) else find_frontend(root)
    if executable is None:
        raise RuntimeError("尚未构建 WinUI 3 界面，请运行 desktop/scripts/build-winui.ps1。")
    if '--smoke-test' not in sys.argv and activate_existing():
        print('工作台已在运行，已切换到现有窗口。关闭现有窗口后再次运行 run.py 可加载更新。')
        return 0
    environment = dict(os.environ, CHAOXING_PYTHON=sys.executable, CHAOXING_BACKEND_ROOT=str(root))
    result = subprocess.call([str(executable), *sys.argv[1:]], env=environment, cwd=root)
    if result:
        raise RuntimeError(f'WinUI 窗口异常退出（0x{result & 0xffffffff:08X}）。')
    return 0
