"""主窗口：工具栏（SVG 图标）、菜单与文件操作。"""

import os

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from . import icons
from . import io as editor_io
from .canvas import EditorCanvas
from .frame import DEFAULT_SETTINGS
from .frame_dialog import FrameDialog
from .sidebar import ToolSidebar

THEME_MODES = ["auto", "light", "dark"]
THEME_LABELS = ["跟随系统", "亮色", "暗色"]

TOOLS = [
    ("select", "选择", "拖动移动标注；Delete 删除选中；双击文字可修改内容"),
    ("crop", "裁剪", "拖拽框选要保留的区域，松开鼠标完成裁剪"),
    ("rect", "矩形", "拖拽绘制矩形"),
    ("ellipse", "圆形", "拖拽绘制圆形/椭圆"),
    ("line", "直线", "拖拽绘制直线"),
    ("arrow", "箭头", "拖拽绘制箭头"),
    ("number", "序号", "单击放置序号，自动递增"),
    ("text", "文字", "单击画布直接输入；Enter 完成，Shift+Enter 换行，Esc 取消"),
    ("brush", "画笔", "按住拖动自由绘制"),
    ("eraser", "橡皮擦", "在标注上拖动，擦除碰到的标注"),
    ("mosaic", "马赛克", "拖拽框选需要打码的区域"),
]

APP_NAME = "imgReEditor"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.get("logo"))
        self.resize(1160, 780)

        self.canvas = EditorCanvas(self)
        self.canvas.file_dropped.connect(self._open_path)
        self.canvas.image_dropped.connect(self._set_image_data)
        self.setCentralWidget(self.canvas)

        self._path = None
        self._frame_dlg = None
        self._icon_actions = []  # (QAction, 图标名)，主题切换后重渲染
        self._build_toolbars()

        # 右侧工具设置侧栏（随工具/选中标注切换）
        self._sidebar = ToolSidebar(self.canvas, self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._sidebar)
        self.resizeDocks([self._sidebar], [230], Qt.Horizontal)

        QShortcut(QKeySequence.Paste, self, self._paste_from_clipboard)
        self.statusBar().showMessage("请打开或新建图片（支持拖入图片、Ctrl+V 粘贴）")

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------

    def _build_toolbars(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBar(tb)

        self._add_action(tb, "新建", "new", self.new_image, "Ctrl+N")
        self._add_action(tb, "打开", "open", self.open_image, "Ctrl+O")
        self._add_action(tb, "保存", "save", self.save_image, "Ctrl+S")
        self._add_action(tb, "另存为", "save_as", self.save_image_as, "Ctrl+Shift+S")
        tb.addSeparator()

        group = QActionGroup(self)
        group.setExclusive(True)
        for tool, label, tip in TOOLS:
            act = QAction(icons.get(tool), label, self)
            act.setCheckable(True)
            act.setToolTip(f"{label}：{tip}")
            act.triggered.connect(
                lambda checked, t=tool, p=tip: self._on_tool(t, p))
            group.addAction(act)
            tb.addAction(act)
            self._icon_actions.append((act, tool))
            if tool == "select":
                act.setChecked(True)
        tb2 = QToolBar("样式工具栏")
        tb2.setMovable(False)
        tb2.setIconSize(QSize(20, 20))
        tb2.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBarBreak()
        self.addToolBar(tb2)

        self._add_action(tb2, "边框", "frame", self._open_frame_dialog)
        tb2.addSeparator()
        self._add_action(tb2, "水平翻转", "flip_h", self.canvas.flip_horizontal)
        self._add_action(tb2, "垂直翻转", "flip_v", self.canvas.flip_vertical)
        self._add_action(tb2, "左旋90°", "rotate_left",
                         lambda: self.canvas.rotate(cw=False))
        self._add_action(tb2, "右旋90°", "rotate_right",
                         lambda: self.canvas.rotate(cw=True))
        self._add_action(tb2, "删除选中", "delete", self.canvas.delete_selected,
                         QKeySequence.Delete)
        tb2.addSeparator()

        tb2.addWidget(QLabel(" 主题 "))
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(THEME_LABELS)
        saved = QSettings("imgReEditor", "imgReEditor").value("theme", "auto")
        if saved in THEME_MODES:
            self._theme_combo.setCurrentIndex(THEME_MODES.index(saved))
        self._theme_combo.currentIndexChanged.connect(self._apply_theme)
        tb2.addWidget(self._theme_combo)

    def _add_action(self, toolbar, text, icon_name, slot, shortcut=None):
        act = QAction(icons.get(icon_name), text, self)
        act.triggered.connect(slot)
        self._icon_actions.append((act, icon_name))
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        toolbar.addAction(act)
        return act

    def _on_tool(self, tool, tip):
        self.canvas.set_tool(tool)
        self._sidebar.show_tool(tool)
        self.statusBar().showMessage(tip)

    # ------------------------------------------------------------------
    # 主题切换
    # ------------------------------------------------------------------

    def _apply_theme(self, index):
        mode = THEME_MODES[index]
        try:
            import qdarktheme
            qdarktheme.setup_theme(mode)
        except Exception:
            pass
        QSettings("imgReEditor", "imgReEditor").setValue("theme", mode)
        self._refresh_icons()

    def _refresh_icons(self):
        # 主题色变化后，按新调色板重渲染全部 SVG 图标
        for act, name in self._icon_actions:
            act.setIcon(icons.get(name))
        self.setWindowIcon(icons.get("logo"))

    # ------------------------------------------------------------------
    # 边框装裱
    # ------------------------------------------------------------------

    def _open_frame_dialog(self):
        if not self.canvas.has_image:
            return
        if self._frame_dlg is None:
            self._frame_dlg = FrameDialog(
                self, self.canvas.frame_settings or dict(DEFAULT_SETTINGS))
            self._frame_dlg.settings_changed.connect(self._on_frame_changed)
            self._frame_dlg.removed.connect(self._on_frame_removed)
        else:
            self._frame_dlg.set_settings(
                self.canvas.frame_settings or dict(DEFAULT_SETTINGS))
        self._frame_dlg.show()
        self._frame_dlg.raise_()
        self._frame_dlg.activateWindow()

    def _on_frame_changed(self, settings):
        self.canvas.set_frame(settings)
        self.statusBar().showMessage("边框样式已实时应用到画布")

    def _on_frame_removed(self):
        self.canvas.set_frame(None)
        self.statusBar().showMessage("已移除边框样式")

    def _sync_frame_dialog(self):
        if self._frame_dlg is not None:
            self._frame_dlg.set_settings(
                self.canvas.frame_settings or dict(DEFAULT_SETTINGS))

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------

    def _update_title(self):
        name = os.path.basename(self._path) if self._path else "未命名"
        self.setWindowTitle(f"{APP_NAME} - {name}")

    def new_image(self):
        w, ok = QInputDialog.getInt(self, "新建图片", "宽度：", 800, 100, 10000)
        if not ok:
            return
        h, ok = QInputDialog.getInt(self, "新建图片", "高度：", 600, 100, 10000)
        if not ok:
            return
        self.canvas.new_blank(w, h)
        self._path = None
        self._update_title()
        self._sync_frame_dialog()
        self.statusBar().showMessage("已新建空白图片")

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)")
        if not path:
            return
        self._open_path(path)

    def _open_path(self, path):
        base, annotations, frame, original, crop_rect = editor_io.load_image(path)
        if base.isNull():
            QMessageBox.warning(self, "打开失败", "无法读取该图片文件。")
            return
        self.canvas.set_image(base, annotations, frame)
        if original is not None:
            self.canvas.set_original(original, crop_rect)
        self._path = path
        self._update_title()
        self._sync_frame_dialog()
        tip = "已载入可编辑标注" if annotations else "已打开图片"
        if frame:
            tip += "（含边框样式）"
        if crop_rect is not None:
            tip += "（可重新裁剪）"
        self.statusBar().showMessage(f"{tip}：{path}")

    def _set_image_data(self, img, source="剪贴板"):
        """载入位图数据（来自粘贴或拖放，无文件路径）。"""
        if img.isNull():
            return
        self.canvas.set_image(img)
        self._path = None
        self._update_title()
        self.statusBar().showMessage(f"已从{source}载入图片")

    def _paste_from_clipboard(self):
        mime = QApplication.clipboard().mimeData()
        # 优先本地文件：之前保存的 PNG 能恢复可编辑标注
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    self._open_path(url.toLocalFile())
                    return
        if mime.hasImage():
            img = QApplication.clipboard().image()
            if not img.isNull():
                self._set_image_data(img)
                return
        self.statusBar().showMessage("剪贴板中没有可粘贴的图片")

    def save_image(self):
        if not self.canvas.has_image:
            return
        if self._path and self._path.lower().endswith(".png"):
            self._save_to(self._path)
        else:
            self.save_image_as()

    def save_image_as(self):
        if not self.canvas.has_image:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 PNG", self._path or "未命名.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self._save_to(path)

    def _save_to(self, path):
        if self.canvas._crop_mode:
            self.canvas.confirm_crop()  # 裁剪模式未决改动先应用
        try:
            editor_io.save_with_metadata(
                path,
                self.canvas.render_output(),
                self.canvas.base_image,
                self.canvas.collect_annotations(),
                frame=self.canvas.frame_settings,
                original=self.canvas.original_info,
            )
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._path = path
        self._update_title()
        self.statusBar().showMessage(f"已保存（含可编辑标注）：{path}")
