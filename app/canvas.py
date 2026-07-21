"""编辑画布：基于 QGraphicsView/QGraphicsScene。

图层模型：
- 底图（QGraphicsPixmapItem，z=-1）：裁剪/翻转/旋转会直接作用于它；
- 标注（AnnotationItem，z 递增）：全部以矢量数据保存，可随时再编辑；
- 马赛克是一类特殊标注：只记录矩形区域，在渲染时对其下方的
  已合成内容做像素化，因此马赛克本身也可以移动、删除、重新框选。
"""

import copy
import math

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from .annotations import (
    AnnotationItem,
    anno_bbox,
    apply_bbox_resize,
    base_anno_bbox,
    bbox_with_handle,
    flip_h_anno,
    flip_v_anno,
    make_font,
    measure_text,
    rotate_anno,
    translate_anno,
)
from .frame import FrameItem, round_image

MOSAIC_BLOCK = 12  # 马赛克块大小（像素）

_HANDLE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "tc": Qt.SizeVerCursor, "bc": Qt.SizeVerCursor,
    "lc": Qt.SizeHorCursor, "rc": Qt.SizeHorCursor,
}

# 绘制工具下可“即选即调”的同类标注类型
_ADJUST_TYPES = {
    "rect": ("rect",), "ellipse": ("ellipse",),
    "line": ("line",), "arrow": ("arrow",),
    "number": ("number",), "text": ("text",),
    "brush": ("path",), "mosaic": ("mosaic",),
}


class SelectionBox(QGraphicsItem):
    """单选标注的选框覆盖层：包围盒 + 8 个缩放手柄 + 底部旋转按钮。

    不接收鼠标事件（事件穿透到底层标注），由画布视图统一处理交互；
    渲染输出时被隐藏，不会出现在保存结果里。
    选框随标注 rotation 一起旋转；马赛克区域不支持旋转（不画按钮）。
    """

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._target = None
        self._rect = QRectF()  # 未旋转的基准包围盒（旋转绕其中心）
        self._rotation = 0.0
        self.setZValue(1e9)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.hide()

    def target(self):
        return self._target

    def set_target(self, item):
        self._target = item
        self.refresh()
        self.show()

    def hide_box(self):
        self._target = None
        self.hide()

    def refresh(self):
        """目标几何变化后跟随。"""
        if self._target is None:
            return
        self.prepareGeometryChange()
        self._rect = self._target.mapRectToScene(
            base_anno_bbox(self._target.anno))
        self._rotation = self._target.anno.get("rotation", 0.0)
        self.update()

    def _rot_tr(self):
        """绕选框中心的旋转变换（未旋转时返回 None）。"""
        if not self._rotation:
            return None
        c = self._rect.center()
        return QTransform().translate(c.x(), c.y()) \
            .rotate(self._rotation).translate(-c.x(), -c.y())

    def handles(self):
        r = self._rect
        cx, cy = r.center().x(), r.center().y()
        pts = {
            "tl": r.topLeft(), "tc": QPointF(cx, r.top()), "tr": r.topRight(),
            "rc": QPointF(r.right(), cy), "br": r.bottomRight(),
            "bc": QPointF(cx, r.bottom()), "bl": r.bottomLeft(),
            "lc": QPointF(r.left(), cy),
        }
        tr = self._rot_tr()
        return {k: tr.map(p) for k, p in pts.items()} if tr else pts

    def handle_at_view(self, view_pos):
        """视图坐标下的手柄命中检测（容差 6 屏幕像素）。"""
        if not self.isVisible() or self._target is None:
            return None
        for hid, hp in self.handles().items():
            vp = self._view.mapFromScene(hp)
            if abs(vp.x() - view_pos.x()) <= 6 and abs(vp.y() - view_pos.y()) <= 6:
                return hid
        return None

    def rotate_pos(self):
        """旋转按钮中心（场景坐标）：选框底边中点外侧，随标注一起旋转。"""
        scale = max(self._view.transform().m11(), 0.25)
        p = QPointF(self._rect.center().x(), self._rect.bottom() + 30.0 / scale)
        tr = self._rot_tr()
        return tr.map(p) if tr else p

    def rotate_at_view(self, view_pos):
        """旋转按钮命中检测（容差 10 屏幕像素；马赛克不支持旋转）。"""
        if not self.isVisible() or self._target is None \
                or self._target.anno.get("type") == "mosaic":
            return False
        bp = self._view.mapFromScene(self.rotate_pos())
        return abs(bp.x() - view_pos.x()) <= 10 \
            and abs(bp.y() - view_pos.y()) <= 10

    def boundingRect(self):
        base = self._rect
        tr = self._rot_tr()
        if tr:
            base = tr.mapRect(base)
        scale = max(self._view.transform().m11(), 0.25)
        pad = 50.0 / scale  # 容纳手柄与外侧旋转按钮
        return base.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        if self._target is None:
            return
        scale = max(self._view.transform().m11(), 0.25)
        hs = 8.0 / scale  # 手柄保持恒定屏幕尺寸
        c = self._rect.center()
        painter.save()
        painter.translate(c.x(), c.y())
        painter.rotate(self._rotation)
        painter.translate(-c.x(), -c.y())
        painter.setPen(QPen(QColor("#1E78FF"), max(1.0, 1.5 / scale)))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self._rect)
        painter.restore()
        painter.setPen(QPen(QColor("#1E78FF"), max(1.0, 1.5 / scale)))
        painter.setBrush(QColor("white"))
        for hp in self.handles().values():
            painter.drawRect(QRectF(hp.x() - hs / 2, hp.y() - hs / 2, hs, hs))
        if self._target.anno.get("type") == "mosaic":
            return
        # 旋转按钮：圆形底 + 圆弧箭头图标
        bp = self.rotate_pos()
        rad = 10.0 / scale
        painter.setBrush(QColor("white"))
        painter.drawEllipse(bp, rad, rad)
        ar = rad * 0.55
        painter.setPen(QPen(QColor("#1E78FF"), max(1.2, 1.8 / scale),
                            Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(bp.x() - ar, bp.y() - ar, 2 * ar, 2 * ar),
                        40 * 16, 280 * 16)
        # 箭头位于圆弧终点（40° + 280° = 320°），沿运动切线方向
        a = math.radians(320)
        tip = QPointF(bp.x() + ar * math.cos(a), bp.y() - ar * math.sin(a))
        ta = math.atan2(-math.cos(a), -math.sin(a))
        L = 5.0 / scale
        for s in (1.0, -1.0):
            ang = ta + s * math.radians(140)
            painter.drawLine(tip, QPointF(tip.x() + L * math.cos(ang),
                                          tip.y() + L * math.sin(ang)))


class CropOverlay(QGraphicsItem):
    """裁剪模式覆盖层：变暗遮罩 + 选区边框 + 8 手柄 + ✓/✗ 按钮。

    不接收鼠标事件（由画布统一处理），渲染输出时被隐藏。
    """

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._rect = QRectF()
        self._bounds = QRectF()  # 图片范围（裁剪约束）
        self.setZValue(1e9)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_bounds(self, bounds):
        self.prepareGeometryChange()
        self._bounds = bounds
        self.update()

    def rect(self):
        return QRectF(self._rect)

    def set_rect(self, r):
        self.prepareGeometryChange()
        self._rect = r.intersected(self._bounds) if self._bounds.isValid() else r
        self.update()

    def handles(self):
        r = self._rect
        cx, cy = r.center().x(), r.center().y()
        return {
            "tl": r.topLeft(), "tc": QPointF(cx, r.top()), "tr": r.topRight(),
            "rc": QPointF(r.right(), cy), "br": r.bottomRight(),
            "bc": QPointF(cx, r.bottom()), "bl": r.bottomLeft(),
            "lc": QPointF(r.left(), cy),
        }

    def handle_at_view(self, view_pos):
        for hid, hp in self.handles().items():
            vp = self._view.mapFromScene(hp)
            if abs(vp.x() - view_pos.x()) <= 6 and abs(vp.y() - view_pos.y()) <= 6:
                return hid
        return None

    def button_pos(self, which):
        """✓/✗ 按钮中心（场景坐标）：优先放选区上沿外侧，空间不足改下沿。"""
        r = self._rect
        by = r.top() - 22 if r.top() >= 34 else r.bottom() + 22
        ok = QPointF(r.right() - 18, by)
        cancel = QPointF(r.right() + 14, by)
        max_x = self._bounds.right() - 12
        if cancel.x() > max_x:
            shift = cancel.x() - max_x
            ok.setX(ok.x() - shift)
            cancel.setX(max_x)
        return ok if which == "ok" else cancel

    def button_at_view(self, view_pos):
        for which in ("ok", "cancel"):
            bp = self._view.mapFromScene(self.button_pos(which))
            if abs(bp.x() - view_pos.x()) <= 12 and abs(bp.y() - view_pos.y()) <= 12:
                return which
        return None

    def boundingRect(self):
        base = self._bounds if self._bounds.isValid() else self._rect
        return base.adjusted(-90, -90, 90, 90)

    def paint(self, painter, option, widget=None):
        if self._rect.isNull():
            return
        scale = max(self._view.transform().m11(), 0.25)
        # 选区外变暗
        path = QPainterPath()
        path.addRect(self._bounds)
        path.addRect(self._rect)
        path.setFillRule(Qt.OddEvenFill)
        painter.fillPath(path, QColor(0, 0, 0, 110))
        # 边框
        painter.setPen(QPen(QColor("#1E78FF"), max(1.5, 2.0 / scale)))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self._rect)
        # 手柄
        hs = 8.0 / scale
        painter.setPen(QPen(QColor("#1E78FF"), max(1.0, 1.5 / scale)))
        painter.setBrush(QColor("white"))
        for hp in self.handles().values():
            painter.drawRect(QRectF(hp.x() - hs / 2, hp.y() - hs / 2, hs, hs))
        # ✓ / ✗ 按钮
        rad = 11.0 / scale
        k = rad / 11.0
        for which, color in (("ok", "#2EAF4E"), ("cancel", "#E64545")):
            c = self.button_pos(which)
            painter.setPen(QPen(QColor(color), max(1.0, 1.5 / scale)))
            painter.setBrush(QColor(color))
            painter.drawEllipse(c, rad, rad)
            painter.setPen(QPen(QColor("white"), max(1.5, 2.0 / scale),
                                Qt.SolidLine, Qt.RoundCap))
            if which == "ok":
                painter.drawPolyline([c + QPointF(-5 * k, 0),
                                      c + QPointF(-1.5 * k, 4 * k),
                                      c + QPointF(5 * k, -4 * k)])
            else:
                painter.drawLine(c + QPointF(-4 * k, -4 * k),
                                 c + QPointF(4 * k, 4 * k))
                painter.drawLine(c + QPointF(-4 * k, 4 * k),
                                 c + QPointF(4 * k, -4 * k))


class _TextEditItem(QGraphicsTextItem):
    """画布内嵌文字编辑器：Enter 完成，Shift/Ctrl+Enter 换行，Esc 取消。"""

    def __init__(self, canvas):
        super().__init__()
        self._canvas = canvas
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setZValue(1e9)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._canvas._commit_text_editor(self, cancel=False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._canvas._commit_text_editor(self, cancel=True)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
                super().keyPressEvent(event)  # 换行
            else:
                self._canvas._commit_text_editor(self, cancel=False)
            return
        super().keyPressEvent(event)


class EditorCanvas(QGraphicsView):
    # 拖放信号：文件路径（可恢复元数据）或位图数据（来自浏览器/截图工具）
    file_dropped = Signal(str)
    image_dropped = Signal(QImage)
    # 选择变化（侧栏联动用）
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.RubberBandDrag)

        self._base_image = QImage()
        self._base_item = None
        self._base_radius = 0     # 底图圆角（来自边框设置）

        self._tool = "select"
        # 工具参数（侧栏编辑；新标注取这里的默认值）
        self._color = QColor("#E60012")      # 描边/主色
        self._width = 3                      # 描边宽度
        self._font_size = 24
        self._font_family = ""               # 空 = 默认字体
        self._bold = False
        self._italic = False
        self._fill_enabled = False           # 矩形/圆形填充
        self._fill_color = QColor("#E60012")
        self._fill_opacity = 26
        self._outline_color = QColor("#FFFFFF")  # 文字描边
        self._outline_width = 0
        self._text_bg_enabled = False        # 文字背景
        self._text_bg_color = QColor("#FFFF00")
        self._number_r = 18                  # 序号半径
        self._eraser_size = 12
        self._mosaic_block = MOSAIC_BLOCK

        self._z = 1.0
        self.number_counter = 1
        self.frame_settings = None  # 边框装裱参数（场景实时显示）
        self._frame_item = None

        # 选框与缩放
        self._selection_box = SelectionBox(self)
        self._scene.addItem(self._selection_box)
        self._resize = None
        self._rotate = None
        self._scene.selectionChanged.connect(self._update_selection_box)
        self.viewport().setMouseTracking(True)

        # 内嵌文字编辑器状态
        self._text_editor = None
        self._text_editor_target = None  # 正在编辑的已有文字标注（编辑期间隐藏）

        # 裁剪模式状态
        self._original = None       # {"base": QImage, "crop_rect": QRectF} 裁剪历史
        self._crop_mode = False
        self._crop_overlay = None
        self._crop_drag = None
        self._crop_backup = None    # 重新裁剪时的当前状态备份（用于取消）

        # 拖拽状态
        self._mouse_down = False
        self._drag_start = None
        self._current = None      # 正在绘制的 AnnotationItem
        self._adjust = None       # 绘制工具下同类型标注的移动拖拽

        # 马赛克刷新调度
        self._refresh_pending = False
        self._dirty_after_drag = False

        self.set_tool("select")

    # ------------------------------------------------------------------
    # 基础属性
    # ------------------------------------------------------------------

    @property
    def has_image(self):
        return self._base_item is not None and not self._base_image.isNull()

    @property
    def base_image(self):
        return self._base_image

    def _anno_items(self):
        return [it for it in self._scene.items() if isinstance(it, AnnotationItem)]

    def collect_annotations(self):
        """按 z 顺序导出全部标注数据（用于保存）。"""
        return [it.anno for it in sorted(self._anno_items(),
                                         key=lambda i: i.zValue())]

    # ------------------------------------------------------------------
    # 图片装载
    # ------------------------------------------------------------------

    def set_image(self, qimg, annotations=None, frame=None):
        self._exit_crop_mode()
        self._original = None
        self._crop_backup = None
        # 仅移除内容图元，保留 SelectionBox（scene.clear() 会把它删掉）
        for it in list(self._scene.items()):
            if it is not self._selection_box:
                self._scene.removeItem(it)
        self._z = 1.0
        self._current = None
        self._adjust = None
        self._frame_item = None
        self.frame_settings = None
        self._text_editor = None  # 图元已随上面移除，仅重置引用
        self._text_editor_target = None
        self._selection_box.hide_box()

        self._base_image = qimg.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._base_item = QGraphicsPixmapItem()
        self._base_item.setZValue(-1)
        self._scene.addItem(self._base_item)
        self._refresh_base_pixmap()
        self._update_view_bounds()

        max_n = 0
        for anno in annotations or []:
            self.add_annotation(anno)
            if anno.get("type") == "number":
                max_n = max(max_n, int(anno.get("n", 0)))
        self.number_counter = max_n + 1

        self.set_frame(frame)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self.refresh_mosaics()

    def new_blank(self, width=800, height=600, color=QColor("white")):
        img = QImage(width, height, QImage.Format_ARGB32)
        img.fill(color)
        self.set_image(img)

    # ------------------------------------------------------------------
    # 边框装裱（场景实时显示）
    # ------------------------------------------------------------------

    def set_frame(self, settings):
        """应用/更新/移除边框（settings 为 None 或 enabled=False 时移除）。"""
        had_frame = self._frame_item is not None
        self.frame_settings = settings if (settings and settings.get("enabled")) \
            else None
        if self.frame_settings:
            if self._frame_item is None:
                self._frame_item = FrameItem()
                self._scene.addItem(self._frame_item)
            self._frame_item.set_geometry(
                self.frame_settings,
                self._base_image.width(), self._base_image.height())
            self._base_radius = int(self.frame_settings.get("image_radius", 0))
        else:
            if self._frame_item is not None:
                self._scene.removeItem(self._frame_item)
                self._frame_item = None
            self._base_radius = 0
        self._refresh_base_pixmap()
        self._update_view_bounds()
        if had_frame != (self._frame_item is not None):
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self.refresh_mosaics()

    def _refresh_base_pixmap(self):
        """底图 pixmap 重建（应用图片圆角）。"""
        if self._base_item is None:
            return
        img = round_image(self._base_image, self._base_radius) \
            if self._base_radius > 0 else self._base_image
        self._base_item.setPixmap(QPixmap.fromImage(img))

    def _update_view_bounds(self):
        """场景范围：有边框时为边框外沿，否则为图片范围。"""
        if self._frame_item is not None:
            self._scene.setSceneRect(self._frame_item.outer_rect())
        elif self._base_item is not None:
            self._scene.setSceneRect(QRectF(self._base_image.rect()))

    # ------------------------------------------------------------------
    # 工具与属性设置
    # ------------------------------------------------------------------

    def set_tool(self, tool):
        if self._text_editor is not None:
            self._commit_text_editor(self._text_editor, cancel=False)
        if self._crop_mode and tool != "crop":
            self.cancel_crop()
        self._adjust = None
        self._tool = tool
        selectable = tool == "select"
        for it in self._anno_items():
            it.setFlag(AnnotationItem.ItemIsSelectable, selectable)
            it.setFlag(AnnotationItem.ItemIsMovable, selectable)
        if not selectable:
            self._scene.clearSelection()
        self.setDragMode(QGraphicsView.RubberBandDrag if selectable
                         else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.ArrowCursor if selectable else Qt.CrossCursor)
        if tool == "crop" and not self._crop_mode:
            self.enter_crop_mode()

    def _for_selected(self, types=None):
        """选中的、类型匹配的标注图元。"""
        return [it for it in self._selected_annos()
                if types is None or it.anno.get("type") in types]

    def _soft_selected(self):
        """绘制工具下软选中的同类型标注图元。

        绘制工具点选已有标注时可临时编辑它（侧栏随之显示其属性），
        这些编辑不写入新标注的默认值。
        """
        types = _ADJUST_TYPES.get(self._tool)
        if not types:
            return []
        return self._for_selected(types)

    def soft_selected_anno(self):
        """软选中的标注数据（无则 None），供侧栏临时显示其属性。"""
        sel = self._soft_selected()
        return sel[0].anno if len(sel) == 1 else None

    def _apply(self, items, fn, geometry=False):
        """对选中图元应用修改并刷新；geometry=True 表示包围盒可能变化。"""
        changed = False
        for it in items:
            if geometry:
                it.prepareGeometryChange()
            fn(it.anno)
            it.update()
            changed = True
        if changed:
            self.mark_dirty()
        return changed

    def set_color(self, qcolor):
        if not self._soft_selected():
            self._color = qcolor
        self._apply(self._for_selected(
            ("rect", "ellipse", "line", "arrow", "path", "text", "number")),
            lambda a: a.__setitem__("color", qcolor.name()))

    def set_width(self, w):
        if not self._soft_selected():
            self._width = w
        self._apply(self._for_selected(("rect", "ellipse", "line", "arrow", "path")),
                    lambda a: a.__setitem__("width", w), geometry=True)

    def set_fill_enabled(self, enabled):
        if not self._soft_selected():
            self._fill_enabled = enabled
        fill = self._fill_color.name() if enabled else None
        self._apply(self._for_selected(("rect", "ellipse")),
                    lambda a: a.__setitem__("fill", fill))

    def set_fill_color(self, qcolor):
        soft = self._soft_selected()
        if not soft:
            self._fill_color = qcolor
        if soft or self._fill_enabled:
            self._apply([it for it in self._for_selected(("rect", "ellipse"))
                         if it.anno.get("fill")],
                        lambda a: a.__setitem__("fill", qcolor.name()))

    def set_fill_opacity(self, opacity):
        if not self._soft_selected():
            self._fill_opacity = opacity
        self._apply([it for it in self._for_selected(("rect", "ellipse"))
                     if it.anno.get("fill")],
                    lambda a: a.__setitem__("fill_opacity", opacity))

    def set_font_size(self, size):
        if not self._soft_selected():
            self._font_size = size
        self._apply(self._for_selected(("text",)),
                    lambda a: a.__setitem__("font_size", size), geometry=True)

    def set_font_family(self, family):
        if not self._soft_selected():
            self._font_family = family
        self._apply(self._for_selected(("text",)),
                    lambda a: a.__setitem__("font_family", family), geometry=True)

    def set_bold(self, bold):
        if not self._soft_selected():
            self._bold = bold
        self._apply(self._for_selected(("text",)),
                    lambda a: a.__setitem__("bold", bold), geometry=True)

    def set_italic(self, italic):
        if not self._soft_selected():
            self._italic = italic
        self._apply(self._for_selected(("text",)),
                    lambda a: a.__setitem__("italic", italic), geometry=True)

    def set_outline_color(self, qcolor):
        soft = self._soft_selected()
        if not soft:
            self._outline_color = qcolor
        if soft or self._outline_width > 0:
            self._apply(self._for_selected(("text",)),
                        lambda a: a.__setitem__("outline_color", qcolor.name()))

    def set_outline_width(self, w):
        if not self._soft_selected():
            self._outline_width = w
        def apply(a):
            a["outline_width"] = w
            a["outline_color"] = self._outline_color.name() if w > 0 else None
        self._apply(self._for_selected(("text",)), apply, geometry=True)

    def set_text_bg_enabled(self, enabled):
        if not self._soft_selected():
            self._text_bg_enabled = enabled
        bg = self._text_bg_color.name() if enabled else None
        self._apply(self._for_selected(("text",)),
                    lambda a: a.__setitem__("bg_color", bg), geometry=True)

    def set_text_bg_color(self, qcolor):
        soft = self._soft_selected()
        if not soft:
            self._text_bg_color = qcolor
        if soft or self._text_bg_enabled:
            self._apply([it for it in self._for_selected(("text",))
                         if it.anno.get("bg_color")],
                        lambda a: a.__setitem__("bg_color", qcolor.name()))

    def set_number_r(self, r):
        if not self._soft_selected():
            self._number_r = r
        self._apply(self._for_selected(("number",)),
                    lambda a: a.__setitem__("r", float(r)), geometry=True)

    def set_number_value(self, n):
        """编辑序号值：无选中时设置下一个序号的起始值；
        选中序号标注时改它的值——绘制工具软选中不影响计数器，
        选择模式下顺推计数器为 n+1。"""
        sel = self._for_selected(("number",))
        if sel:
            self._apply(sel, lambda a: a.__setitem__("n", n))
            if not self._soft_selected():
                self.number_counter = n + 1
        else:
            self.number_counter = n

    def set_eraser_size(self, size):
        self._eraser_size = size

    def set_mosaic_block(self, block):
        if not self._soft_selected():
            self._mosaic_block = block
        changed = False
        for it in self._for_selected(("mosaic",)):
            it.anno["block"] = block
            changed = True
        if changed:
            self.refresh_mosaics()

    def _selected_annos(self):
        return [it for it in self._scene.selectedItems()
                if isinstance(it, AnnotationItem)]

    # ------------------------------------------------------------------
    # 标注增删
    # ------------------------------------------------------------------

    def add_annotation(self, anno):
        """创建标注图元并返回（鼠标绘制与程序调用都走这里）。"""
        anno.setdefault("color", self._color.name())
        anno.setdefault("width", self._width)
        t = anno.get("type")
        if t in ("rect", "ellipse"):
            anno.setdefault("fill", self._fill_color.name()
                            if self._fill_enabled else None)
            anno.setdefault("fill_opacity", self._fill_opacity)
        elif t == "text":
            anno.setdefault("font_size", self._font_size)
            anno.setdefault("font_family", self._font_family)
            anno.setdefault("bold", self._bold)
            anno.setdefault("italic", self._italic)
            anno.setdefault("outline_color", self._outline_color.name()
                            if self._outline_width > 0 else None)
            anno.setdefault("outline_width", self._outline_width)
            anno.setdefault("bg_color", self._text_bg_color.name()
                            if self._text_bg_enabled else None)
        elif t == "mosaic":
            anno.setdefault("block", self._mosaic_block)
        elif t == "number":
            anno.setdefault("r", float(self._number_r))
        item = AnnotationItem(anno, z=self._z)
        self._z += 1.0
        selectable = self._tool == "select"
        item.setFlag(AnnotationItem.ItemIsSelectable, selectable)
        item.setFlag(AnnotationItem.ItemIsMovable, selectable)
        self._scene.addItem(item)
        self.mark_dirty()
        return item

    def delete_selected(self):
        if self._crop_mode:
            return
        items = self._selected_annos()
        if not items:
            return
        for it in items:
            self._scene.removeItem(it)
        self.mark_dirty()

    def erase_at(self, pos):
        """橡皮擦：删除擦除范围内碰到的标注。"""
        r = max(4.0, float(self._eraser_size))
        area = QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r)
        removed = False
        for it in self._anno_items():
            if it.mapRectToScene(it.boundingRect()).intersects(area):
                self._scene.removeItem(it)
                removed = True
        if removed:
            self.mark_dirty()

    # ------------------------------------------------------------------
    # 底图操作：裁剪 / 翻转 / 旋转
    # ------------------------------------------------------------------

    def _update_base(self):
        self._refresh_base_pixmap()
        if self._frame_item is not None:
            self._frame_item.set_geometry(
                self.frame_settings,
                self._base_image.width(), self._base_image.height())
        self._update_view_bounds()
        self._update_selection_box()
        self.mark_dirty()

    # ------------------------------------------------------------------
    # 裁剪模式（可逆：保存原图与选区，随时重选；随元数据持久化）
    # ------------------------------------------------------------------

    @property
    def original_info(self):
        """裁剪历史：None 或 {"base": QImage, "crop_rect": QRectF}。"""
        if self._original is None:
            return None
        if self._original["crop_rect"] == QRectF(self._original["base"].rect()):
            return None  # 选区=整图，等同未裁剪
        return self._original

    def set_original(self, base_img, crop_rect):
        """从元数据恢复裁剪历史。"""
        if base_img is None or base_img.isNull():
            self._original = None
        else:
            self._original = {"base": base_img,
                              "crop_rect": crop_rect or QRectF(base_img.rect())}

    def enter_crop_mode(self):
        """进入裁剪：首次记录原图；再次进入时恢复显示原图与当前选区。"""
        if not self.has_image or self._crop_mode:
            return
        if self._text_editor is not None:
            self._commit_text_editor(self._text_editor, cancel=False)
        self._crop_mode = True
        self._crop_drag = None
        if self._original is None:
            # 首次裁剪：当前图即原图
            self._original = {"base": self._base_image,
                              "crop_rect": QRectF(self._base_image.rect())}
            self._crop_backup = None
        else:
            # 重新裁剪：备份当前状态（供取消），载入原图并映射标注
            self._crop_backup = {
                "base": self._base_image,
                "annos": copy.deepcopy(self.collect_annotations()),
                "counter": self.number_counter,
            }
            r0 = self._original["crop_rect"]
            annos_orig = []
            for a in self.collect_annotations():
                a2 = copy.deepcopy(a)
                translate_anno(a2, r0.x(), r0.y())
                annos_orig.append(a2)
            self._load_base_and_annos(self._original["base"], annos_orig)
        self._crop_overlay = CropOverlay(self)
        self._crop_overlay.set_bounds(QRectF(self._original["base"].rect()))
        self._scene.addItem(self._crop_overlay)
        self._crop_overlay.set_rect(self._original["crop_rect"])
        self.viewport().update()

    def cancel_crop(self):
        if not self._crop_mode:
            return
        self._exit_crop_mode()
        if self._crop_backup is not None:
            self._load_base_and_annos(self._crop_backup["base"],
                                      self._crop_backup["annos"])
            self.number_counter = self._crop_backup["counter"]
        self._crop_backup = None

    def confirm_crop(self, rect=None):
        """应用裁剪（rect 为空时用当前选区）。选区=整图则移除裁剪。"""
        if not self._crop_mode:
            return
        if rect is not None:
            self._crop_overlay.set_rect(rect)
        full = QRect(self._original["base"].rect())
        r = self._crop_overlay.rect().toAlignedRect().intersected(full)
        backup = self._crop_backup
        self._exit_crop_mode()
        self._crop_backup = None
        if r.width() < 4 or r.height() < 4:
            # 无效选区：放弃本次裁剪，还原状态
            if backup is not None:
                self._load_base_and_annos(backup["base"], backup["annos"])
                self.number_counter = backup["counter"]
            return
        if r == full:
            # 选区=整图：恢复原图，视为未裁剪
            self._load_base_and_annos(self._original["base"],
                                      self.collect_annotations())
            self._original = None
            return
        self._apply_crop_rect(QRectF(r))

    def _exit_crop_mode(self):
        self._crop_mode = False
        self._crop_drag = None
        if self._crop_overlay is not None:
            self._scene.removeItem(self._crop_overlay)
            self._crop_overlay = None

    def _apply_crop_rect(self, rect):
        """按选区裁剪（进入裁剪模式后标注均在原图坐标系）。"""
        self._base_image = self._original["base"].copy(rect.toAlignedRect())
        keep = QRectF(0, 0, rect.width(), rect.height())
        for it in self._anno_items():
            it.prepareGeometryChange()
            translate_anno(it.anno, -rect.x(), -rect.y())
            # 加容差：水平/垂直直线的包围盒面积为 0，直接 intersects 会误判
            if not anno_bbox(it.anno).adjusted(-2, -2, 2, 2).intersects(keep):
                self._scene.removeItem(it)
            else:
                it.update()
        self._original["crop_rect"] = QRectF(rect)
        self._update_base()

    def _load_base_and_annos(self, qimg, annos):
        """裁剪模式内的底图/标注切换（不重置工具、序号与裁剪历史）。"""
        for it in list(self._scene.items()):
            if it is not self._selection_box and it is not self._crop_overlay:
                self._scene.removeItem(it)
        self._frame_item = None
        self._base_image = qimg.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        self._base_item = QGraphicsPixmapItem()
        self._base_item.setZValue(-1)
        self._scene.addItem(self._base_item)
        self._refresh_base_pixmap()
        if self.frame_settings:
            self._frame_item = FrameItem()
            self._scene.addItem(self._frame_item)
        self._z = 1.0
        for anno in annos:
            self.add_annotation(anno)
        self._update_base()

    def _crop_press(self, event):
        vp = event.position().toPoint()
        btn = self._crop_overlay.button_at_view(vp)
        if btn == "ok":
            self.confirm_crop()
            return
        if btn == "cancel":
            self.cancel_crop()
            return
        pos = self._to_scene(event)
        hid = self._crop_overlay.handle_at_view(vp)
        if hid:
            self._crop_drag = {"kind": "resize", "handle": hid,
                               "orig": self._crop_overlay.rect()}
        elif self._crop_overlay.rect().contains(pos):
            self._crop_drag = {"kind": "move", "start": pos,
                               "orig": self._crop_overlay.rect()}
        else:
            self._crop_drag = {"kind": "new", "start": pos}
            self._crop_overlay.set_rect(QRectF(pos, pos))

    def _crop_move(self, event):
        pos = self._to_scene(event)
        d = self._crop_drag
        if d is None:
            vp = event.position().toPoint()
            hid = self._crop_overlay.handle_at_view(vp)
            if hid:
                self.viewport().setCursor(_HANDLE_CURSORS[hid])
            elif self._crop_overlay.rect().contains(pos):
                self.viewport().setCursor(Qt.SizeAllCursor)
            else:
                self.viewport().setCursor(Qt.CrossCursor)
            return
        bounds = QRectF(self._original["base"].rect())
        if d["kind"] == "resize":
            nb = bbox_with_handle(d["orig"], d["handle"], pos)
        elif d["kind"] == "move":
            nb = d["orig"].translated(pos - d["start"])
            if nb.left() < bounds.left():
                nb.moveLeft(bounds.left())
            if nb.top() < bounds.top():
                nb.moveTop(bounds.top())
            if nb.right() > bounds.right():
                nb.moveRight(bounds.right())
            if nb.bottom() > bounds.bottom():
                nb.moveBottom(bounds.bottom())
        else:
            nb = QRectF(d["start"], pos).normalized()
        self._crop_overlay.set_rect(nb)

    def keyPressEvent(self, event):
        if self._crop_mode:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.confirm_crop()
                return
            if event.key() == Qt.Key_Escape:
                self.cancel_crop()
                return
        super().keyPressEvent(event)

    def flip_horizontal(self):
        if not self.has_image:
            return
        if self._crop_mode:
            self.cancel_crop()
        self._original = None  # 坐标系变化后裁剪历史失效
        self._base_image = self._base_image.mirrored(True, False)
        w = self._base_image.width()
        for it in self._anno_items():
            flip_h_anno(it.anno, w)
            it.update_geometry()
        self._update_base()

    def flip_vertical(self):
        if not self.has_image:
            return
        if self._crop_mode:
            self.cancel_crop()
        self._original = None
        self._base_image = self._base_image.mirrored(False, True)
        h = self._base_image.height()
        for it in self._anno_items():
            flip_v_anno(it.anno, h)
            it.update_geometry()
        self._update_base()

    def rotate(self, cw=True):
        if not self.has_image:
            return
        if self._crop_mode:
            self.cancel_crop()
        self._original = None
        w, h = self._base_image.width(), self._base_image.height()
        tr = QTransform().rotate(90 if cw else -90)
        self._base_image = self._base_image.transformed(tr)
        for it in self._anno_items():
            rotate_anno(it.anno, w, h, cw)
            it.update_geometry()
        self._update_base()

    # ------------------------------------------------------------------
    # 马赛克渲染
    # ------------------------------------------------------------------

    def mark_dirty(self):
        """内容变化后调度马赛克重算（拖拽期间延迟到松手）。"""
        if self._mouse_down:
            self._dirty_after_drag = True
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(30, self._do_refresh)

    def _do_refresh(self):
        self._refresh_pending = False
        self.refresh_mosaics()

    @staticmethod
    def _pixelate(img, block):
        w, h = img.width(), img.height()
        block = max(2, int(block))
        if w < 2 or h < 2:
            return img
        small = img.scaled(max(1, w // block), max(1, h // block),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)

    def _render(self, source_rect, hide=()):
        """把场景的 source_rect 区域渲染为图片；hide 中的图元临时隐藏。"""
        hidden = []
        for it in hide:
            if it is not None and it.isVisible():
                it.setVisible(False)
                hidden.append(it)
        img = QImage(source_rect.size().toSize(),
                     QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        self._scene.render(painter, QRectF(img.rect()), source_rect)
        painter.end()
        for it in hidden:
            it.setVisible(True)
        return img

    def _decor_items(self):
        """装饰性图元（选框/边框/内嵌编辑器/裁剪覆盖层），不参与内容渲染。"""
        return [self._selection_box, self._frame_item, self._text_editor,
                self._crop_overlay]

    def refresh_mosaics(self):
        """按 z 顺序重算所有马赛克：每个马赛克像素化其下方已合成的内容。"""
        if not self.has_image:
            return
        items = sorted(self._anno_items(), key=lambda i: i.zValue())
        mosaics = [it for it in items if it.anno.get("type") == "mosaic"]
        if not mosaics:
            return
        image_rect = QRectF(self._base_image.rect())
        self.setUpdatesEnabled(False)
        try:
            for it in items:
                it.setVisible(False)
            for m in mosaics:
                for it in items:
                    it.setVisible(it.zValue() < m.zValue())
                composed = self._render(image_rect, hide=self._decor_items())
                r = QRectF(*m.anno["rect"]).toAlignedRect().intersected(
                    image_rect.toAlignedRect())
                if r.width() >= 2 and r.height() >= 2:
                    pix = QPixmap.fromImage(self._pixelate(
                        composed.copy(r), m.anno.get("block", MOSAIC_BLOCK)))
                    m.mosaic_pixmap = pix
                m.setVisible(True)
                m.update()
        finally:
            for it in items:
                it.setVisible(True)
            self.setUpdatesEnabled(True)
            self.viewport().update()

    # ------------------------------------------------------------------
    # 最终合成（保存用）
    # ------------------------------------------------------------------

    def render_final(self):
        """内容合成图（不含边框/选框），图片坐标范围。"""
        self.refresh_mosaics()
        return self._render(QRectF(self._base_image.rect()),
                            hide=self._decor_items())

    def render_output(self):
        """最终输出：有边框时渲染整个场景（含边框），否则仅内容。"""
        self.refresh_mosaics()
        return self._render(self._scene.sceneRect(), hide=[self._selection_box])

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------

    def _to_scene(self, event):
        pos = self.mapToScene(event.position().toPoint())
        r = self._scene.sceneRect()
        pos.setX(min(max(pos.x(), r.left()), r.right()))
        pos.setY(min(max(pos.y(), r.top()), r.bottom()))
        return pos

    # ------------------------------------------------------------------
    # 选框与缩放
    # ------------------------------------------------------------------

    def _update_selection_box(self):
        sel = self._selected_annos()
        if len(sel) == 1:
            self._selection_box.set_target(sel[0])
        else:
            self._selection_box.hide_box()
        self.selection_changed.emit()

    def _begin_resize(self, handle):
        item = self._selection_box.target()
        if item is None:
            return
        bbox = item.mapRectToScene(base_anno_bbox(item.anno))
        self._resize = {
            "item": item,
            "handle": handle,
            "anno": copy.deepcopy(item.anno),
            "bbox": bbox,  # 未旋转坐标系下的基准包围盒
            "rotation": item.anno.get("rotation", 0.0),
            "center": bbox.center(),
        }
        self._mouse_down = True  # 让马赛克刷新延迟到缩放结束

    def _update_resize(self, pos):
        st = self._resize
        if st["rotation"]:  # 拖拽点先逆旋转回标注的未旋转坐标系
            c = st["center"]
            tr = QTransform().translate(c.x(), c.y()) \
                .rotate(-st["rotation"]).translate(-c.x(), -c.y())
            pos = tr.map(pos)
        new_bbox = bbox_with_handle(st["bbox"], st["handle"], pos)
        apply_bbox_resize(st["item"].anno, st["anno"], st["bbox"], new_bbox)
        st["item"].update_geometry()
        self._selection_box.refresh()
        self.mark_dirty()

    def _end_resize(self):
        self._resize = None
        self._mouse_down = False
        if self._dirty_after_drag:
            self._dirty_after_drag = False
            self.refresh_mosaics()

    # ------------------------------------------------------------------
    # 选框旋转按钮拖拽
    # ------------------------------------------------------------------

    def _begin_rotate(self, pos):
        item = self._selection_box.target()
        if item is None:
            return
        c = item.mapRectToScene(base_anno_bbox(item.anno)).center()
        self._rotate = {
            "item": item,
            "center": c,
            "start_angle": math.degrees(
                math.atan2(pos.y() - c.y(), pos.x() - c.x())),
            "orig_rotation": item.anno.get("rotation", 0.0),
        }
        self._mouse_down = True  # 让马赛克刷新延迟到旋转结束

    def _update_rotate(self, pos, modifiers=Qt.NoModifier):
        st = self._rotate
        c = st["center"]
        angle = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))
        rot = st["orig_rotation"] + (angle - st["start_angle"])
        if modifiers & Qt.ShiftModifier:
            rot = round(rot / 15.0) * 15.0  # Shift：吸附到 15° 步进
        st["item"].prepareGeometryChange()
        st["item"].anno["rotation"] = (rot + 180.0) % 360.0 - 180.0
        st["item"].update()
        self._selection_box.refresh()
        self.mark_dirty()

    def _end_rotate(self):
        self._rotate = None
        self._mouse_down = False
        if self._dirty_after_drag:
            self._dirty_after_drag = False
            self.refresh_mosaics()

    def _update_hover_cursor(self, view_pos):
        if self._selection_box.rotate_at_view(view_pos):
            self.viewport().setCursor(Qt.PointingHandCursor)
            return
        hid = self._selection_box.handle_at_view(view_pos)
        self.viewport().setCursor(
            _HANDLE_CURSORS[hid] if hid else Qt.ArrowCursor)

    # ------------------------------------------------------------------
    # 绘制工具下同类型标注即选即调
    # ------------------------------------------------------------------

    def _anno_at(self, pos, types):
        """命中检测：pos（场景坐标）处最上层的指定类型标注。"""
        if not types:
            return None
        for it in self._anno_items():  # scene.items() 按 z 降序，先命中最上层
            if it.anno.get("type") in types \
                    and it.mapRectToScene(it.boundingRect()).contains(pos):
                return it
        return None

    def _soft_select(self, item):
        """绘制工具下软选中：临时给选中标记，选框/侧栏随 selectionChanged 联动。"""
        self._scene.clearSelection()
        item.setFlag(AnnotationItem.ItemIsSelectable, True)
        item.setSelected(True)

    def _begin_move(self, item, pos):
        self._adjust = {"item": item, "anno": copy.deepcopy(item.anno),
                        "start": pos}
        self._mouse_down = True

    def _update_move(self, pos):
        d = self._adjust
        item = d["item"]
        item.prepareGeometryChange()
        item.anno.clear()
        item.anno.update(copy.deepcopy(d["anno"]))
        translate_anno(item.anno,
                       pos.x() - d["start"].x(), pos.y() - d["start"].y())
        item.update()
        self._selection_box.refresh()
        self.mark_dirty()

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if not self.has_image or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if self._crop_mode:
            self._crop_press(event)
            return
        if self._tool == "select":
            vp = event.position().toPoint()
            if self._selection_box.rotate_at_view(vp):
                self._begin_rotate(self._to_scene(event))
                return
            hid = self._selection_box.handle_at_view(vp)
            if hid:
                self._begin_resize(hid)
                return
            super().mousePressEvent(event)
            return

        pos = self._to_scene(event)
        # 绘制类工具不转发事件给场景，编辑器的焦点不会自动转移，先提交
        if self._text_editor is not None:
            self._commit_text_editor(self._text_editor, cancel=False)

        # 即选即调：已显示选框时先查旋转按钮与手柄；再查同类型标注本体
        if self._selection_box.isVisible():
            vp = event.position().toPoint()
            if self._selection_box.rotate_at_view(vp):
                self._begin_rotate(pos)
                return
            hid = self._selection_box.handle_at_view(vp)
            if hid:
                self._begin_resize(hid)
                return
        hit = self._anno_at(pos, _ADJUST_TYPES.get(self._tool, ()))
        if hit is not None:
            self._soft_select(hit)
            self._begin_move(hit, pos)
            return
        if self._scene.selectedItems():
            self._scene.clearSelection()  # 点空白：取消软选中，开始新绘制

        self._mouse_down = True
        tool = self._tool

        if tool in ("rect", "ellipse", "mosaic"):
            self._drag_start = pos
            self._current = self.add_annotation(
                {"type": tool, "rect": [pos.x(), pos.y(), 0, 0]})
        elif tool in ("line", "arrow"):
            self._current = self.add_annotation(
                {"type": tool, "p1": [pos.x(), pos.y()], "p2": [pos.x(), pos.y()]})
        elif tool == "brush":
            self._current = self.add_annotation(
                {"type": "path", "points": [[pos.x(), pos.y()]]})
        elif tool == "eraser":
            self.erase_at(pos)
        elif tool == "number":
            self.add_annotation({"type": "number",
                                 "center": [pos.x(), pos.y()],
                                 "n": self.number_counter, "width": 2})
            self.number_counter += 1
            self.selection_changed.emit()  # 侧栏「序号值」跟随计数器
            self._mouse_down = False
        elif tool == "text":
            self._start_text_editor(pos)
            self._mouse_down = False

    def mouseMoveEvent(self, event):
        if self._crop_mode:
            self._crop_move(event)
            return
        if self._rotate:
            self._update_rotate(self._to_scene(event), event.modifiers())
            return
        if self._resize:
            self._update_resize(self._to_scene(event))
            return
        if self._adjust:
            self._update_move(self._to_scene(event))
            return
        if self._tool == "select":
            super().mouseMoveEvent(event)
            if event.buttons() & Qt.LeftButton:
                self._selection_box.refresh()  # 拖动移动时选框跟随
            else:
                self._update_hover_cursor(event.position().toPoint())
            return
        if not self._mouse_down:
            super().mouseMoveEvent(event)
            return
        pos = self._to_scene(event)
        if self._current is not None:
            anno = self._current.anno
            t = anno["type"]
            if t in ("rect", "ellipse", "mosaic"):
                r = QRectF(self._drag_start, pos).normalized()
                anno["rect"] = [r.x(), r.y(), r.width(), r.height()]
            elif t in ("line", "arrow"):
                anno["p2"] = [pos.x(), pos.y()]
            elif t == "path":
                last = anno["points"][-1]
                if abs(pos.x() - last[0]) + abs(pos.y() - last[1]) > 1.5:
                    anno["points"].append([pos.x(), pos.y()])
            self._current.update_geometry()
            self.mark_dirty()
        elif self._tool == "eraser":
            self.erase_at(pos)

    def mouseReleaseEvent(self, event):
        if self._crop_mode:
            self._crop_drag = None
            return
        if self._rotate:
            self._end_rotate()
            return
        if self._resize:
            self._end_resize()
            return
        if self._adjust:
            self._adjust = None
            self._mouse_down = False
            if self._dirty_after_drag:
                self._dirty_after_drag = False
                self.refresh_mosaics()
            return
        if self._tool == "select":
            super().mouseReleaseEvent(event)
            moved = any(it.sync_pos_if_moved() for it in self._anno_items())
            self._update_selection_box()
            if moved:
                self.mark_dirty()
            return

        self._mouse_down = False
        if self._current is not None:
            anno = self._current.anno
            t = anno["type"]
            tiny = False
            if t in ("rect", "ellipse", "mosaic"):
                tiny = anno["rect"][2] < 3 and anno["rect"][3] < 3
            elif t in ("line", "arrow"):
                p1, p2 = anno["p1"], anno["p2"]
                tiny = abs(p2[0] - p1[0]) + abs(p2[1] - p1[1]) < 3
            elif t == "path":
                tiny = len(anno["points"]) < 2
            if tiny:
                self._scene.removeItem(self._current)
            else:
                # 绘制完成自动选中新形状，便于立即调参/移动/缩放
                self._soft_select(self._current)
            self._current = None

        if self._dirty_after_drag:
            self._dirty_after_drag = False
            self.refresh_mosaics()

    def mouseDoubleClickEvent(self, event):
        # 选择/文字工具下双击文字标注 → 内嵌编辑
        if self._tool in ("select", "text"):
            for item in self.items(event.position().toPoint()):
                if isinstance(item, AnnotationItem) \
                        and item.anno.get("type") == "text":
                    self._start_text_editor(anno_item=item)
                    return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # 内嵌文字编辑器
    # ------------------------------------------------------------------

    def _start_text_editor(self, pos=None, anno_item=None):
        """在画布中创建文字输入框。anno_item 非空表示编辑已有文字标注。"""
        if self._text_editor is not None:
            self._commit_text_editor(self._text_editor, cancel=False)
        editor = _TextEditItem(self)
        if anno_item is not None:
            anno = anno_item.anno
            editor.setFont(make_font(anno))
            editor.setDefaultTextColor(QColor(anno.get("color", "#FF0000")))
            editor.setPlainText(anno.get("text", ""))
            # 输入框覆盖原文本位置，编辑期间隐藏原标注
            w, h, _ = measure_text(anno)
            cx, cy = anno["center"]
            editor.setPos(cx - w / 2, cy - h / 2)
            anno_item.setVisible(False)
            self._text_editor_target = anno_item
        else:
            editor.setFont(make_font({"font_size": self._font_size}))
            editor.setDefaultTextColor(self._color)
            editor.setPos(pos)
            self._text_editor_target = None
        self._scene.addItem(editor)
        self._text_editor = editor
        self._scene.setFocusItem(editor)
        editor.setFocus(Qt.OtherFocusReason)

    def _commit_text_editor(self, item, cancel):
        """结束内嵌编辑：应用或丢弃内容。"""
        if self._text_editor is not item:
            return
        target = self._text_editor_target
        self._text_editor = None
        self._text_editor_target = None
        text = item.toPlainText()
        bounds = item.mapRectToScene(item.boundingRect())
        item.clearFocus()
        self._scene.removeItem(item)
        if target is not None:
            target.setVisible(True)
        if cancel or not text.strip():
            return
        if target is not None:
            target.prepareGeometryChange()
            target.anno["text"] = text
            target.update()
        else:
            self.add_annotation({"type": "text",
                                 "center": [bounds.center().x(),
                                            bounds.center().y()],
                                 "text": text,
                                 "font_size": self._font_size,
                                 "rotation": 0.0})
        self._update_selection_box()
        self.mark_dirty()

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    # ------------------------------------------------------------------
    # 拖入图片
    # ------------------------------------------------------------------

    @staticmethod
    def _can_accept(mime):
        return mime.hasUrls() or mime.hasImage()

    def dragEnterEvent(self, event):
        if self._can_accept(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._can_accept(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        # 优先本地文件：之前保存的 PNG 能恢复可编辑标注
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    self.file_dropped.emit(url.toLocalFile())
                    event.acceptProposedAction()
                    return
        if mime.hasImage():
            data = mime.imageData()
            img = data.toImage() if isinstance(data, QPixmap) else QImage(data)
            if not img.isNull():
                self.image_dropped.emit(img)
                event.acceptProposedAction()
