"""右侧工具设置侧栏（仿 PixPin）：随工具/选中标注切换参数面板。

- 绘制工具激活时：面板编辑新标注的默认参数；
- 选择模式下单选标注时：面板切换为该标注类型的页面并实时编辑它
  （同时更新默认值，后续新标注沿用）；
- 绘制工具下点选同类型已有标注时：临时显示并编辑该标注的属性，
  不影响新标注的默认值。
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDockWidget,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _swatch(color, w=40, h=18):
    pix = QPixmap(w, h)
    pix.fill(QColor(color))
    return QIcon(pix)


# 标注类型 -> 侧栏页面 key（默认与类型同名，仅画笔 path 特殊）
_ANNO_PAGE = {"path": "brush"}


class ColorButton(QPushButton):
    """色块按钮：点击弹出取色器。"""
    colorChanged = Signal(QColor)

    def __init__(self, color="#E60012", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedHeight(28)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self):
        return QColor(self._color)

    def set_color(self, color):
        self._color = QColor(color)
        self._refresh()

    def _refresh(self):
        self.setIcon(_swatch(self._color.name()))
        self.setIconSize(QSize(40, 18))
        self.setText(self._color.name())

    def _pick(self):
        c = QColorDialog.getColor(self._color, self, "选择颜色")
        if c.isValid():
            self.set_color(c)
            self.colorChanged.emit(QColor(c))


def _slider(lo, hi, value, on_change):
    """滑杆 + 数值标签，返回 (容器, QSlider, 标签)。"""
    box = QHBoxLayout()
    box.setContentsMargins(0, 0, 0, 0)
    slider = QSlider(Qt.Horizontal, minimum=lo, maximum=hi, value=value)
    slider.setMinimumWidth(60)
    val = QLabel(str(value))
    val.setMinimumWidth(28)
    slider.valueChanged.connect(
        lambda v: (val.setText(str(v)), on_change(v)))
    box.addWidget(slider, 1)
    box.addWidget(val)
    host = QWidget()
    host.setLayout(box)
    return host, slider, val


class ToolSidebar(QDockWidget):
    """工具设置侧栏；show_tool() 切换页面，selection_changed 时联动。"""

    def __init__(self, canvas, parent=None):
        super().__init__("工具设置", parent)
        self.setObjectName("toolSettingsDock")
        self.setFeatures(QDockWidget.DockWidgetClosable
                         | QDockWidget.DockWidgetMovable)
        self.canvas = canvas
        self._tool = "select"
        self._stack = QStackedWidget(self)
        self._stack.setMinimumWidth(150)
        self.setWidget(self._stack)
        self._pages = {}
        self._ctl = {}  # 页面 key -> 控件 dict
        self._build_pages()
        canvas.selection_changed.connect(self.refresh)

    # ------------------------------------------------------------------
    # 页面构建
    # ------------------------------------------------------------------

    def _add_page(self, key, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        head = QLabel(f"<b>{title}</b>")
        layout.addWidget(head)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        layout.addLayout(form)
        layout.addStretch(1)
        self._pages[key] = page
        self._stack.addWidget(page)
        self._ctl[key] = {}
        return form

    def _add_color(self, key, form, label, value, slot):
        btn = ColorButton(value)
        btn.colorChanged.connect(slot)
        form.addRow(label, btn)
        self._ctl[key][label] = btn
        return btn

    def _add_spin(self, key, form, label, lo, hi, value, slot):
        spin = QSpinBox(minimum=lo, maximum=hi, value=value)
        spin.setMinimumWidth(64)
        spin.valueChanged.connect(slot)
        form.addRow(label, spin)
        self._ctl[key][label] = spin
        return spin

    def _add_slider(self, key, form, label, lo, hi, value, slot):
        host, slider, val = _slider(lo, hi, value, slot)
        form.addRow(label, host)
        self._ctl[key][label] = slider
        self._ctl[key][label + "__val"] = val
        return slider

    def _build_pages(self):
        c = self.canvas

        # 选择
        form = self._add_page("select", "选择")
        hint = QLabel("单击选择标注\n拖动移动 · 手柄缩放\nDelete 删除 · 双击文字编辑")
        hint.setWordWrap(True)
        form.addRow(hint)

        # 裁剪
        form = self._add_page("crop", "裁剪")
        hint = QLabel("拖动手柄调整选区，或框选新区域；\n再次点击「裁剪」可恢复原图重选。")
        hint.setWordWrap(True)
        form.addRow(hint)
        btn_ok = QPushButton("应用裁剪")
        btn_ok.clicked.connect(lambda: c.confirm_crop())
        form.addRow(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(lambda: c.cancel_crop())
        form.addRow(btn_cancel)

        # 矩形 / 圆形（共用布局）
        for key, title in (("rect", "矩形设置"), ("ellipse", "圆形设置")):
            form = self._add_page(key, title)
            self._add_color(key, form, "描边颜色", c._color.name(), c.set_color)
            self._add_spin(key, form, "描边宽度", 1, 30, c._width, c.set_width)
            fill = QCheckBox("启用填充")
            fill.setChecked(c._fill_enabled)
            fill.toggled.connect(c.set_fill_enabled)
            form.addRow("填充", fill)
            self._ctl[key]["填充"] = fill
            self._add_color(key, form, "填充颜色", c._fill_color.name(),
                            c.set_fill_color)
            self._add_slider(key, form, "填充透明度", 0, 100, c._fill_opacity,
                             c.set_fill_opacity)

        # 直线 / 箭头 / 画笔（共用布局）
        for key, title in (("line", "直线设置"), ("arrow", "箭头设置"),
                           ("brush", "画笔设置")):
            form = self._add_page(key, title)
            self._add_color(key, form, "颜色", c._color.name(), c.set_color)
            self._add_spin(key, form, "线宽", 1, 30, c._width, c.set_width)

        # 序号
        form = self._add_page("number", "序号设置")
        self._add_color("number", form, "颜色", c._color.name(), c.set_color)
        self._add_spin("number", form, "大小", 8, 80, c._number_r,
                       c.set_number_r)
        self._add_spin("number", form, "序号值", 1, 9999, c.number_counter,
                       c.set_number_value)

        # 文字
        form = self._add_page("text", "文本设置")
        fc = QFontComboBox()
        fc.setCurrentFont(QFont("Microsoft YaHei"))
        fc.currentFontChanged.connect(lambda f: c.set_font_family(f.family()))
        form.addRow("字体", fc)
        self._ctl["text"]["字体"] = fc
        self._add_spin("text", form, "字号", 8, 120, c._font_size,
                       c.set_font_size)
        style_box = QHBoxLayout()
        style_box.setContentsMargins(0, 0, 0, 0)
        btn_b = QToolButton(text="B", checkable=True)
        font_b = QFont()
        font_b.setBold(True)
        btn_b.setFont(font_b)
        btn_b.setChecked(c._bold)
        btn_b.toggled.connect(c.set_bold)
        btn_i = QToolButton(text="I", checkable=True)
        font_i = QFont()
        font_i.setItalic(True)
        btn_i.setFont(font_i)
        btn_i.setChecked(c._italic)
        btn_i.toggled.connect(c.set_italic)
        style_box.addWidget(btn_b)
        style_box.addWidget(btn_i)
        style_box.addStretch(1)
        style_host = QWidget()
        style_host.setLayout(style_box)
        form.addRow("样式", style_host)
        self._ctl["text"]["B"] = btn_b
        self._ctl["text"]["I"] = btn_i
        self._add_color("text", form, "颜色", c._color.name(), c.set_color)
        self._add_color("text", form, "描边颜色", c._outline_color.name(),
                        c.set_outline_color)
        self._add_spin("text", form, "描边粗细", 0, 20, c._outline_width,
                       c.set_outline_width)
        bg = QCheckBox("启用背景")
        bg.setChecked(c._text_bg_enabled)
        bg.toggled.connect(c.set_text_bg_enabled)
        form.addRow("背景", bg)
        self._ctl["text"]["背景"] = bg
        self._add_color("text", form, "背景颜色", c._text_bg_color.name(),
                        c.set_text_bg_color)

        # 橡皮擦
        form = self._add_page("eraser", "橡皮擦设置")
        self._add_slider("eraser", form, "大小", 4, 80, c._eraser_size,
                         c.set_eraser_size)
        hint = QLabel("在标注上拖动，擦除碰到的标注。")
        hint.setWordWrap(True)
        form.addRow(hint)

        # 马赛克
        form = self._add_page("mosaic", "马赛克设置")
        self._add_slider("mosaic", form, "块大小", 4, 48, c._mosaic_block,
                         c.set_mosaic_block)

    # ------------------------------------------------------------------
    # 页面切换与同步
    # ------------------------------------------------------------------

    def show_tool(self, tool):
        self._tool = tool
        self.refresh()

    def refresh(self):
        """按当前工具/选中标注选择页面并同步控件值。"""
        c = self.canvas
        anno = None
        if self._tool == "select":
            sel = c._selected_annos()
            if len(sel) == 1:
                anno = sel[0].anno
        else:
            # 绘制工具软选中同类型标注：临时显示标注自身属性，
            # 编辑只作用于它，不影响新标注的默认值
            anno = c.soft_selected_anno()
        key = _ANNO_PAGE.get(anno["type"], anno["type"]) if anno is not None \
            else self._tool
        page = self._pages.get(key)
        if page is None:
            return
        self._sync(key, anno)
        self._stack.setCurrentWidget(page)

    def _sync(self, key, anno):
        """把控件值同步为 anno（选中标注）或画布默认值。"""
        c = self.canvas
        ctl = self._ctl[key]

        def src(name, default):
            return anno.get(name, default) if anno is not None else default

        def set_color_btn(label, value):
            if label in ctl:
                ctl[label].set_color(value)

        def set_spin(label, value):
            if label in ctl:
                ctl[label].blockSignals(True)
                ctl[label].setValue(int(value))
                ctl[label].blockSignals(False)
                if label + "__val" in ctl:  # 滑杆旁的数值标签同步
                    ctl[label + "__val"].setText(str(int(value)))

        def set_check(label, value):
            if label in ctl:
                ctl[label].blockSignals(True)
                ctl[label].setChecked(bool(value))
                ctl[label].blockSignals(False)

        if key in ("rect", "ellipse"):
            set_color_btn("描边颜色", src("color", c._color.name()))
            set_spin("描边宽度", src("width", c._width))
            set_check("填充", bool(src("fill", None)) if anno else c._fill_enabled)
            set_color_btn("填充颜色", src("fill", None) or c._fill_color.name())
            set_spin("填充透明度", src("fill_opacity", c._fill_opacity))
        elif key in ("line", "arrow", "brush"):
            set_color_btn("颜色", src("color", c._color.name()))
            set_spin("线宽", src("width", c._width))
        elif key == "number":
            set_color_btn("颜色", src("color", c._color.name()))
            set_spin("大小", src("r", c._number_r))
            set_spin("序号值", src("n", c.number_counter))
        elif key == "text":
            family = src("font_family", c._font_family) or "Microsoft YaHei"
            fc = ctl["字体"]
            fc.blockSignals(True)
            fc.setCurrentFont(QFont(family))
            fc.blockSignals(False)
            set_spin("字号", src("font_size", c._font_size))
            set_check("B", src("bold", c._bold))
            set_check("I", src("italic", c._italic))
            set_color_btn("颜色", src("color", c._color.name()))
            set_color_btn("描边颜色",
                          src("outline_color", None) or c._outline_color.name())
            set_spin("描边粗细", src("outline_width", c._outline_width))
            set_check("背景", bool(src("bg_color", None)) if anno
                      else c._text_bg_enabled)
            set_color_btn("背景颜色",
                          src("bg_color", None) or c._text_bg_color.name())
        elif key == "eraser":
            set_spin("大小", c._eraser_size)
        elif key == "mosaic":
            set_spin("块大小", src("block", c._mosaic_block))
