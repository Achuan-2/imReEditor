"""标注对象模型：定义各类标注的数据结构、绘制方式与几何变换。

所有标注以 dict 形式存储（可直接 JSON 序列化），随 PNG 一起保存，
重新打开时可还原为可继续编辑的对象。字段约定：

- 公共: type, color("#RRGGBB"), width
- rect / ellipse / mosaic: rect: [x, y, w, h]
- line / arrow: p1: [x, y], p2: [x, y]
- path(画笔): points: [[x, y], ...]
- text: center: [x, y], text, font_size, rotation(角度)
- number(序号): center: [x, y], n, r

坐标均为场景坐标（= 图片像素坐标），锚点统一使用中心点或矩形框，
这样翻转 / 旋转 / 裁剪对所有标注都是纯粹的坐标变换，不会丢失可编辑性。
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem

ANNOTATION_TYPES = (
    "rect", "ellipse", "line", "arrow", "text", "number", "path", "mosaic",
)


# ---------------------------------------------------------------------------
# 测量与几何工具
# ---------------------------------------------------------------------------

FONT_FAMILIES = ["Microsoft YaHei", "SimHei", "PingFang SC", "Arial"]


def make_font(anno):
    """按标注参数构造字体：自定义字体族（可空）+ 粗体/斜体。"""
    font = QFont()
    family = anno.get("font_family") or ""
    font.setFamilies([family] + FONT_FAMILIES if family else FONT_FAMILIES)
    font.setPointSize(max(1, int(anno.get("font_size", 24))))
    font.setBold(bool(anno.get("bold", False)))
    font.setItalic(bool(anno.get("italic", False)))
    return font


def measure_text(anno):
    """返回 (宽, 高, QFont)。"""
    font = make_font(anno)
    fm = QFontMetricsF(font)
    lines = anno.get("text", "").split("\n")
    w = max((fm.horizontalAdvance(line) for line in lines), default=0.0)
    h = fm.height() * len(lines)
    return w, h, font


def path_from_points(points):
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(QPointF(*points[0]))
    for x, y in points[1:]:
        path.lineTo(QPointF(x, y))
    return path


def arrow_head_points(p1, p2, width):
    """箭头头部的两个端点（箭尖为 p2）。"""
    dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    head = min(max(10.0, width * 4.0), length * 0.8)
    angle = math.atan2(dy, dx)
    spread = 0.5  # 弧度
    pts = []
    for sign in (1.0, -1.0):
        a = angle + sign * (math.pi - spread)
        pts.append(QPointF(p2.x() + head * math.cos(a), p2.y() + head * math.sin(a)))
    return pts


def _point_transform(anno, func):
    """对标注里的所有坐标点应用 func(x, y) -> (nx, ny)。"""
    t = anno["type"]
    if t in ("rect", "ellipse", "mosaic"):
        x, y, w, h = anno["rect"]
        (x1, y1) = func(x, y)
        (x2, y2) = func(x + w, y + h)
        anno["rect"] = [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]
    elif t in ("line", "arrow"):
        anno["p1"] = list(func(*anno["p1"]))
        anno["p2"] = list(func(*anno["p2"]))
    elif t == "path":
        anno["points"] = [list(func(x, y)) for x, y in anno["points"]]
    elif t in ("text", "number"):
        anno["center"] = list(func(*anno["center"]))


def translate_anno(anno, dx, dy):
    _point_transform(anno, lambda x, y: (x + dx, y + dy))


def flip_h_anno(anno, img_w):
    _point_transform(anno, lambda x, y: (img_w - x, y))
    if anno["type"] == "text":
        anno["rotation"] = -anno.get("rotation", 0.0)


def flip_v_anno(anno, img_h):
    _point_transform(anno, lambda x, y: (x, img_h - y))
    if anno["type"] == "text":
        anno["rotation"] = -anno.get("rotation", 0.0)


def rotate_anno(anno, img_w, img_h, cw):
    """旋转 90 度。cw=True 顺时针，否则逆时针。img_w/img_h 为旋转前尺寸。"""
    if cw:
        _point_transform(anno, lambda x, y: (img_h - y, x))
    else:
        _point_transform(anno, lambda x, y: (y, img_w - x))
    if anno["type"] == "text":
        anno["rotation"] = anno.get("rotation", 0.0) + (90 if cw else -90)


# ---------------------------------------------------------------------------
# 选框手柄缩放
# ---------------------------------------------------------------------------

HANDLES = ("tl", "tc", "tr", "rc", "br", "bc", "bl", "lc")


def bbox_with_handle(bbox, handle, pos, min_size=4.0):
    """拖动手柄得到的新包围盒（不允许越过对边翻转）。"""
    l, t, r, b = bbox.left(), bbox.top(), bbox.right(), bbox.bottom()
    px, py = pos.x(), pos.y()
    # handle 为 "tl"/"tc"/"tr"/"rc"/"br"/"bc"/"bl"/"lc"，按包含字母调整对应边
    if "l" in handle:
        l = min(px, r - min_size)
    if "r" in handle:
        r = max(px, l + min_size)
    if "t" in handle:
        t = min(py, b - min_size)
    if "b" in handle:
        b = max(py, t + min_size)
    return QRectF(QPointF(l, t), QPointF(r, b))


def apply_bbox_resize(anno, orig_anno, orig_bbox, new_bbox):
    """把标注从 orig_bbox 仿射映射到 new_bbox。

    orig_anno 是拖拽开始时的深拷贝，anno 是被修改的目标。
    """
    t = anno["type"]
    if t in ("rect", "ellipse", "mosaic"):
        anno["rect"] = [new_bbox.x(), new_bbox.y(),
                        new_bbox.width(), new_bbox.height()]
        return
    ow, oh = orig_bbox.width(), orig_bbox.height()
    sx = new_bbox.width() / ow if ow > 1e-6 else None
    sy = new_bbox.height() / oh if oh > 1e-6 else None

    def map_pt(x, y):
        nx = (new_bbox.x() + (x - orig_bbox.x()) * sx) if sx is not None \
            else new_bbox.center().x()
        ny = (new_bbox.y() + (y - orig_bbox.y()) * sy) if sy is not None \
            else new_bbox.center().y()
        return [nx, ny]

    if t in ("line", "arrow"):
        anno["p1"] = map_pt(*orig_anno["p1"])
        anno["p2"] = map_pt(*orig_anno["p2"])
    elif t == "path":
        anno["points"] = [map_pt(x, y) for x, y in orig_anno["points"]]
    elif t == "number":
        anno["center"] = [new_bbox.center().x(), new_bbox.center().y()]
        anno["r"] = max(6.0, min(new_bbox.width(), new_bbox.height()) / 2)
    elif t == "text":
        factor = sy if sy is not None else (sx if sx is not None else 1.0)
        anno["font_size"] = max(6, int(round(
            orig_anno.get("font_size", 24) * factor)))
        anno["center"] = [new_bbox.center().x(), new_bbox.center().y()]


def anno_bbox(anno):
    """标注在场景坐标下的紧凑包围盒（不含画笔宽度），用于裁剪相交判断。"""
    t = anno["type"]
    if t in ("rect", "ellipse", "mosaic"):
        return QRectF(*anno["rect"])
    if t in ("line", "arrow"):
        return QRectF(QPointF(*anno["p1"]), QPointF(*anno["p2"])).normalized()
    if t == "path":
        return path_from_points(anno["points"]).boundingRect()
    if t == "number":
        cx, cy = anno["center"]
        r = anno.get("r", 14.0)
        return QRectF(cx - r, cy - r, 2 * r, 2 * r)
    if t == "text":
        w, h, _ = measure_text(anno)
        cx, cy = anno["center"]
        pad = int(anno.get("outline_width", 0) or 0) \
            + (4 if anno.get("bg_color") else 0)
        tr = QTransform().translate(cx, cy).rotate(anno.get("rotation", 0.0))
        return tr.mapRect(QRectF(-w / 2 - pad, -h / 2 - pad,
                                 w + 2 * pad, h + 2 * pad))
    return QRectF()


# ---------------------------------------------------------------------------
# 画布图元
# ---------------------------------------------------------------------------

def _fill_brush(a):
    """矩形/圆形的填充画刷；未启用填充返回 None。"""
    fill = a.get("fill")
    if not fill:
        return None
    color = QColor(fill)
    color.setAlphaF(min(100, int(a.get("fill_opacity", 100))) / 100.0)
    return QBrush(color)


class AnnotationItem(QGraphicsItem):
    """所有标注共用的 QGraphicsItem，几何数据直接取自 anno dict。"""

    def __init__(self, anno, z=1.0):
        super().__init__()
        self.anno = anno
        self.mosaic_pixmap = None  # 由画布生成（马赛克是渲染期效果）
        self.setZValue(z)

    # -- Qt 接口 -----------------------------------------------------------

    def boundingRect(self):
        a = self.anno
        t = a["type"]
        pad = a.get("width", 3) / 2.0 + 2.0
        if t == "text":
            w, h, _ = measure_text(a)
            cx, cy = a["center"]
            pad = 3 + int(a.get("outline_width", 0) or 0) \
                + (4 if a.get("bg_color") else 0)
            tr = QTransform().translate(cx, cy).rotate(a.get("rotation", 0.0))
            return tr.mapRect(QRectF(-w / 2 - pad, -h / 2 - pad,
                                     w + 2 * pad, h + 2 * pad))
        if t == "mosaic":
            return QRectF(*a["rect"])
        if t in ("rect", "ellipse"):
            return QRectF(*a["rect"]).adjusted(-pad, -pad, pad, pad)
        if t in ("line", "arrow"):
            r = QRectF(QPointF(*a["p1"]), QPointF(*a["p2"])).normalized()
            extra = pad + (max(10.0, a.get("width", 3) * 4.0) if t == "arrow" else 0.0)
            return r.adjusted(-extra, -extra, extra, extra)
        if t == "path":
            return path_from_points(a["points"]).boundingRect().adjusted(
                -pad, -pad, pad, pad)
        if t == "number":
            cx, cy = a["center"]
            r = a.get("r", 14.0) + pad
            return QRectF(cx - r, cy - r, 2 * r, 2 * r)
        return QRectF()

    def paint(self, painter, option, widget=None):
        a = self.anno
        t = a["type"]
        painter.setRenderHint(QPainter.Antialiasing)

        if t == "mosaic":
            r = QRectF(*a["rect"])
            if self.mosaic_pixmap is not None and not self.mosaic_pixmap.isNull():
                painter.drawPixmap(r, self.mosaic_pixmap,
                                   QRectF(self.mosaic_pixmap.rect()))
            else:
                # 尚未生成马赛克预览时（例如拖拽框选过程中）的占位显示
                painter.setPen(QPen(QColor(110, 110, 110), 1, Qt.DashLine))
                painter.setBrush(QColor(140, 140, 140, 60))
                painter.drawRect(r)
        else:
            color = QColor(a.get("color", "#FF0000"))
            width = a.get("width", 3)
            pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            if t == "rect":
                painter.setPen(pen)
                painter.setBrush(_fill_brush(a) or QBrush(Qt.NoBrush))
                painter.drawRect(QRectF(*a["rect"]))
            elif t == "ellipse":
                painter.setPen(pen)
                painter.setBrush(_fill_brush(a) or QBrush(Qt.NoBrush))
                painter.drawEllipse(QRectF(*a["rect"]))
            elif t == "line":
                painter.setPen(pen)
                painter.drawLine(QPointF(*a["p1"]), QPointF(*a["p2"]))
            elif t == "arrow":
                painter.setPen(pen)
                p1, p2 = QPointF(*a["p1"]), QPointF(*a["p2"])
                painter.drawLine(p1, p2)
                for hp in arrow_head_points(p1, p2, width):
                    painter.drawLine(p2, hp)
            elif t == "path":
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(path_from_points(a["points"]))
            elif t == "number":
                cx, cy = a["center"]
                r = a.get("r", 14.0)
                painter.setPen(QPen(color, 2))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(cx, cy), r, r)
                digits = len(str(a.get("n", 1)))
                scale = 1.0 if digits == 1 else (0.8 if digits == 2 else 0.62)
                font = QFont()
                font.setFamilies(FONT_FAMILIES)
                font.setBold(True)
                font.setPointSize(max(6, int(r * scale)))
                painter.setFont(font)
                painter.setPen(QColor("white"))
                painter.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r),
                                 Qt.AlignCenter, str(a.get("n", 1)))
            elif t == "text":
                w, h, font = measure_text(a)
                cx, cy = a["center"]
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(a.get("rotation", 0.0))
                rect = QRectF(-w / 2, -h / 2, w, h)
                bg = a.get("bg_color")
                if bg:  # 文字背景
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(bg))
                    painter.drawRoundedRect(rect.adjusted(-4, -2, 4, 2), 4, 4)
                ow = int(a.get("outline_width", 0) or 0)
                oc = a.get("outline_color")
                if ow > 0 and oc:  # 描边 + 填充：逐行 QPainterPath 排版
                    fm = QFontMetricsF(font)
                    y0 = -h / 2 + fm.ascent()
                    path = QPainterPath()
                    for i, line in enumerate(a.get("text", "").split("\n")):
                        lw = fm.horizontalAdvance(line)
                        path.addText(-lw / 2, y0 + i * fm.height(), font, line)
                    painter.setPen(QPen(QColor(oc), ow, Qt.SolidLine,
                                        Qt.RoundCap, Qt.RoundJoin))
                    painter.setBrush(color)
                    painter.drawPath(path)
                else:
                    painter.setFont(font)
                    painter.setPen(QPen(color))
                    painter.drawText(rect, Qt.AlignCenter, a.get("text", ""))
                painter.restore()
        # 选中态由画布的 SelectionBox 覆盖层统一绘制，
        # 此处不画，保证导出/马赛克合成渲染永远干净。

    # -- 编辑辅助 -----------------------------------------------------------

    def update_geometry(self):
        """anno 被外部修改后调用，通知场景重新计算包围盒并重绘。"""
        self.prepareGeometryChange()
        self.update()

    def sync_pos_if_moved(self):
        """选择模式下拖动结束后，把 pos 偏移合并回 anno 坐标。"""
        d = self.pos()
        if d.isNull():
            return False
        self.prepareGeometryChange()
        translate_anno(self.anno, d.x(), d.y())
        self.setPos(0, 0)
        self.update()
        return True
