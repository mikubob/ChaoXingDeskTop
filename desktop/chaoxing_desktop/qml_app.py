"""旧版 Qt Quick 启动入口。"""
import os
from pathlib import Path
import sys

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from .config import resource_root, data_dir
from .qml_backend import Backend


def create_engine(backend):
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", backend)
    engine.load(QUrl.fromLocalFile(str(resource_root() / "chaoxing_desktop/qml/Main.qml")))
    return engine


def configure_app(app):
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        if (fonts / name).exists():
            QFontDatabase.addApplicationFont(str(fonts / name))
    app.setApplicationName("ChaoxingDesktop")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(QIcon(str(resource_root() / "assets/app.svg")))
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    QQuickStyle.setStyle("FluentWinUI3" if sys.platform == "win32" else "Fusion")


def main():
    app = QApplication(sys.argv)
    configure_app(app)
    from .instance import InstanceGuard
    guard = InstanceGuard(data_dir(), app)
    if not guard.acquire():
        return 0
    backend = Backend()
    engine = create_engine(backend)
    if not engine.rootObjects():
        backend.close()
        guard.close()
        return 1
    def activate():
        root = engine.rootObjects()[0]
        root.showNormal()
        root.raise_()
        root.requestActivate()
    guard.activate = activate
    try:
        return app.exec()
    finally:
        # Destroy bindings before their Python context object during shutdown.
        from shiboken6 import delete
        delete(engine)
        backend.close()
        guard.close()
