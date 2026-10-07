"""启动入口"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from ui import MainWindow
from version import __version__

def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationVersion(__version__)

    base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    win = MainWindow(base_dir, version=__version__)
    win.show()

    # 退出时确保子线程被停止
    app.aboutToQuit.connect(win._stop_threads)

    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
