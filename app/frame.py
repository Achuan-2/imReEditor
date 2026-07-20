"""边框装裱：背景（纯色/渐变）、间距、圆角、阴影。

边框以 FrameItem 的形式直接存在于画布场景中，实时显示；
设置随 PNG 元数据保存，导出时渲染整个场景即得最终效果。
底图与标注始终使用图片坐标系，不受边框影响。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsItem, QGraphicsScene

# 渐变背景预设：(名称, 起点色, 终点色)，方向为左上 → 右下
PRESETS = [
    ("蜜桃", "#FF9A8B", "#FF6A88"),
    ("晚霞", "#FA709A", "#FEE140"),
    ("葡萄", "#A18CD1", "#FBC2EB"),
    ("海洋", "#4FACFE", "#00F2FE"),
    ("薄荷", "#84FAB0", "#8FD3F4"),
    ("晨曦", "#F6D365", "#FDA085"),
    ("星空", "#30CFD0", "#330867"),
    ("石墨", "#232526", "#414345"),
]

DEFAULT_SETTINGS = {
    "enabled": True,
    "bg_mode": "gradient",     # solid | gradient
    "bg_color": "#FFFFFF",
    "bg_preset": 0,
    "padding": 48,             # 边框间距
    "image_radius": 12,        # 图片圆角
    "frame_radius": 24,        # 边框圆角
    "shadow_size": 24,         # 阴影大小（模糊半径）
    "shadow_color": "#000000",
    "shadow_opacity": 35,      # 阴影透明度（百分比）
}


def _rounded_path(rect, radius):
    radius = max(0.0, min(float(radius), rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _blur(img, radius):
    """Qt 原生高斯模糊（用于阴影）。"""
    if radius <= 0:
        return img
    scene = QGraphicsScene()
    scene.setSceneRect(QRectF(img.rect()))
    item = scene.addPixmap(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(float(radius))
    item.setGraphicsEffect(effect)
    out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    scene.render(painter, QRectF(out.rect()), QRectF(img.rect()))
    painter.end()
    return out


def round_image(img, radius):
    """把图片裁剪为圆角（抗锯齿）。radius<=0 时原样返回。"""
    if radius <= 0:
        return img
    out = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.drawImage(0, 0, img)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    painter.fillPath(_rounded_path(QRectF(img.rect()), radius), QColor("white"))
    painter.end()
    return out


def make_bg_brush(s, rect):
    """背景画刷：纯色或渐变预设，渐变横跨给定矩形。"""
    if s.get("bg_mode") == "gradient":
        _, c1, c2 = PRESETS[int(s.get("bg_preset", 0)) % len(PRESETS)]
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, QColor(c1))
        grad.setColorAt(1, QColor(c2))
        return QBrush(grad)
    return QBrush(QColor(s.get("bg_color", "#FFFFFF")))


def make_shadow(img_w, img_h, s):
    """生成整幅边框大小的阴影图（图片形状圆角矩形 + 高斯模糊）。"""
    size = int(s.get("shadow_size", 0))
    opacity = int(s.get("shadow_opacity", 0))
    if size <= 0 or opacity <= 0:
        return None
    pad = int(s.get("padding", 0))
    w, h = img_w + 2 * pad, img_h + 2 * pad
    shadow = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    shadow.fill(Qt.transparent)
    painter = QPainter(shadow)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(s.get("shadow_color", "#000000"))
    color.setAlphaF(min(100, opacity) / 100.0)
    dy = min(size * 0.35, float(pad))  # 轻微下移，更自然
    painter.fillPath(
        _rounded_path(QRectF(pad, pad + dy, img_w, img_h),
                      int(s.get("image_radius", 0))),
        color)
    painter.end()
    return _blur(shadow, size)


class FrameItem(QGraphicsItem):
    """画布中的边框：绘制背景与阴影，垫在底图之下（z=-2）。

    图片内容固定在 (0,0) 原点，边框外扩 padding，因此标注坐标系不变。
    """

    def __init__(self):
        super().__init__()
        self.setZValue(-2)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self._s = None
        self._outer = QRectF()
        self._shadow = None

    def set_geometry(self, settings, img_w, img_h):
        self.prepareGeometryChange()
        self._s = settings
        pad = int(settings.get("padding", 0))
        self._outer = QRectF(-pad, -pad, img_w + 2 * pad, img_h + 2 * pad)
        self._shadow = make_shadow(img_w, img_h, settings)
        self.update()

    def outer_rect(self):
        return self._outer

    def boundingRect(self):
        return self._outer

    def paint(self, painter, option, widget=None):
        if self._s is None:
            return
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(
            _rounded_path(self._outer, int(self._s.get("frame_radius", 0))),
            make_bg_brush(self._s, self._outer))
        if self._shadow is not None:
            painter.drawImage(self._outer.topLeft(), self._shadow)
