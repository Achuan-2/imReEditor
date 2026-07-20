"""边框设置面板：非模态，所有改动实时应用到画布。"""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from .frame import DEFAULT_SETTINGS, PRESETS

SLIDERS = [
    ("边框间距", "padding", 0, 300, "px"),
    ("图片圆角", "image_radius", 0, 200, "px"),
    ("边框圆角", "frame_radius", 0, 200, "px"),
    ("阴影大小", "shadow_size", 0, 150, "px"),
    ("阴影透明度", "shadow_opacity", 0, 100, "%"),
]


def _swatch(color, w=52, h=26):
    pix = QPixmap(w, h)
    pix.fill(QColor(color))
    return QIcon(pix)


def _gradient_icon(c1, c2, w=52, h=26):
    img = QImage(w, h, QImage.Format_ARGB32)
    painter = QPainter(img)
    grad = QLinearGradient(0, 0, w, 0)
    grad.setColorAt(0, QColor(c1))
    grad.setColorAt(1, QColor(c2))
    painter.fillRect(img.rect(), grad)
    painter.end()
    return QIcon(QPixmap.fromImage(img))


class FrameDialog(QDialog):
    """边框参数面板；画布即预览。

    settings_changed(dict): 参数变化（防抖后）发出，enabled 恒为 True
    removed(): 点击「移除边框」
    """

    settings_changed = Signal(dict)
    removed = Signal()

    def __init__(self, parent, settings=None):
        super().__init__(parent)
        self.setWindowTitle("边框与背景")
        self.setModal(False)
        self._s = dict(DEFAULT_SETTINGS)
        self._s.update(settings or {})
        self._s["enabled"] = True

        panel = QVBoxLayout(self)

        bg_box = QGroupBox("背景")
        bg = QGridLayout(bg_box)
        self._btn_bg_color = QPushButton("纯色…")
        self._btn_bg_color.setIcon(_swatch(self._s["bg_color"]))
        self._btn_bg_color.clicked.connect(self._pick_bg_color)
        bg.addWidget(self._btn_bg_color, 0, 0, 1, 4)
        for i, (name, c1, c2) in enumerate(PRESETS):
            btn = QPushButton(_gradient_icon(c1, c2), name)
            btn.setIconSize(QSize(40, 20))
            btn.clicked.connect(lambda checked, idx=i: self._pick_preset(idx))
            bg.addWidget(btn, 1 + i // 4, i % 4)
        panel.addWidget(bg_box)

        self._sliders = {}
        for label, key, lo, hi, unit in SLIDERS:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(64)
            slider = QSlider(Qt.Horizontal, minimum=lo, maximum=hi,
                             value=int(self._s[key]))
            val = QLabel(f"{slider.value()}{unit}")
            val.setMinimumWidth(52)
            slider.valueChanged.connect(
                lambda v, k=key, vl=val, u=unit: self._on_slider(k, v, vl, u))
            row.addWidget(name)
            row.addWidget(slider, 1)
            row.addWidget(val)
            panel.addLayout(row)
            self._sliders[key] = (slider, val, unit)

        row = QHBoxLayout()
        lab = QLabel("阴影颜色")
        lab.setMinimumWidth(64)
        row.addWidget(lab)
        self._btn_shadow_color = QPushButton()
        self._btn_shadow_color.setIcon(_swatch(self._s["shadow_color"]))
        self._btn_shadow_color.setIconSize(QSize(52, 26))
        self._btn_shadow_color.clicked.connect(self._pick_shadow_color)
        row.addWidget(self._btn_shadow_color)
        row.addStretch(1)
        panel.addLayout(row)

        row = QHBoxLayout()
        btn_remove = QPushButton("移除边框")
        btn_remove.clicked.connect(self._remove)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        row.addStretch(1)
        row.addWidget(btn_remove)
        row.addWidget(btn_close)
        panel.addLayout(row)

        self._timer = QTimer(self, singleShot=True, interval=120,
                             timeout=self._emit)

    # ------------------------------------------------------------------

    def _emit(self):
        self.settings_changed.emit(dict(self._s))

    def _on_slider(self, key, value, val_label, unit):
        self._s[key] = value
        val_label.setText(f"{value}{unit}")
        self._timer.start()

    def _pick_bg_color(self):
        c = QColorDialog.getColor(QColor(self._s["bg_color"]), self, "背景颜色")
        if c.isValid():
            self._s["bg_mode"] = "solid"
            self._s["bg_color"] = c.name()
            self._btn_bg_color.setIcon(_swatch(c.name()))
            self._timer.start()

    def _pick_preset(self, idx):
        self._s["bg_mode"] = "gradient"
        self._s["bg_preset"] = idx
        self._timer.start()

    def _pick_shadow_color(self):
        c = QColorDialog.getColor(QColor(self._s["shadow_color"]), self, "阴影颜色")
        if c.isValid():
            self._s["shadow_color"] = c.name()
            self._btn_shadow_color.setIcon(_swatch(c.name()))
            self._timer.start()

    def _remove(self):
        self.removed.emit()
        self.close()

    # ------------------------------------------------------------------

    def set_settings(self, settings):
        """外部（打开/新建图片）同步当前边框设置到面板。"""
        self._s = dict(DEFAULT_SETTINGS)
        self._s.update(settings or {})
        self._s["enabled"] = True
        for key, (slider, val, unit) in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(self._s[key]))
            slider.blockSignals(False)
            val.setText(f"{int(self._s[key])}{unit}")
        self._btn_bg_color.setIcon(_swatch(self._s["bg_color"]))
        self._btn_shadow_color.setIcon(_swatch(self._s["shadow_color"]))
