"""Lazy legacy file dialogs; the native bridge supplies paths from WinUI."""


class FileDialogs:
    @staticmethod
    def getOpenFileName(*args, **kwargs):
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getOpenFileName(*args, **kwargs)

    @staticmethod
    def getSaveFileName(*args, **kwargs):
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getSaveFileName(*args, **kwargs)

    @staticmethod
    def getExistingDirectory(*args, **kwargs):
        from PySide6.QtWidgets import QFileDialog
        return QFileDialog.getExistingDirectory(*args, **kwargs)
