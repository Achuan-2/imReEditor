"""imgReEditor 入口。

用法:
    python main.py            启动编辑器
    python main.py --selftest 运行离屏自检（打包后也可用 imgReEditor.exe --selftest）
"""

import os
import sys


def main():
    if "--selftest" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from tests.selftest import run
        return run()

    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("imgReEditor")
    app.setApplicationDisplayName("imgReEditor")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    # 主题美化：读取用户上次的选择（auto/light/dark）
    theme = QSettings("imgReEditor", "imgReEditor").value("theme", "auto")
    try:
        import qdarktheme
        qdarktheme.setup_theme(theme)
    except Exception:
        pass  # 主题包缺失时使用默认样式

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
