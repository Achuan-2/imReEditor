"""SVG 图标库：内嵌 SVG 源码，运行时按当前主题色渲染为 QIcon。

图标为 24x24 线框风格（stroke 绘制），`{c}` 是颜色占位符：
默认取应用调色板的文字色，深色/浅色主题下图标自动适配。
"""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
    'viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">{b}</svg>'
)

_SVGS = {
    # 应用图标：相框 + 铅笔
    "logo": '<rect x="3" y="3" width="18" height="18" rx="4"/>'
            '<path d="m8.5 15.5.8-3 6.7-6.7 2.2 2.2-6.7 6.7z"/>',
    # 文件操作
    "new": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
           '<polyline points="14 2 14 8 20 8"/>'
           '<line x1="12" y1="12" x2="12" y2="18"/><line x1="9" y1="15" x2="15" y2="15"/>',
    "open": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9'
            'a2 2 0 0 1 2 2z"/>',
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
            '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "save_as": '<g transform="translate(-1,-1) scale(0.85)">'
               '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
               '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'
               '</g>'
               '<line x1="18.5" y1="16" x2="18.5" y2="22"/>'
               '<line x1="15.5" y1="19" x2="21.5" y2="19"/>',
    # 工具
    "select": '<path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "rect": '<rect x="3" y="5" width="18" height="14" rx="1"/>',
    "ellipse": '<circle cx="12" cy="12" r="9"/>',
    "line": '<line x1="5" y1="19" x2="19" y2="5"/>'
            '<circle cx="5" cy="19" r="1.5"/><circle cx="19" cy="5" r="1.5"/>',
    "arrow": '<line x1="5" y1="19" x2="19" y2="5"/><polyline points="12 5 19 5 19 12"/>',
    "number": '<circle cx="12" cy="12" r="9"/>'
              '<polyline points="10 9.5 12 8 12 16"/><line x1="9.5" y1="16" x2="14.5" y2="16"/>',
    "text": '<polyline points="4 7 4 4 20 4 20 7"/>'
            '<line x1="12" y1="4" x2="12" y2="20"/><line x1="9" y1="20" x2="15" y2="20"/>',
    "brush": '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>'
             '<path d="m15 5 4 4"/>',
    "eraser": '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0'
              'l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
    "mosaic": '<rect x="3" y="3" width="18" height="18" rx="1"/>'
              '<line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/>'
              '<line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/>'
              '<rect x="3.7" y="3.7" width="4.6" height="4.6" fill="{c}" fill-opacity="0.45" stroke="none"/>'
              '<rect x="9.7" y="9.7" width="4.6" height="4.6" fill="{c}" fill-opacity="0.45" stroke="none"/>'
              '<rect x="15.7" y="15.7" width="4.6" height="4.6" fill="{c}" fill-opacity="0.45" stroke="none"/>',
    # 底图变换
    "flip_h": '<path d="M3 5v14l6-7z"/><path d="M21 5v14l-6-7z"/>',
    "flip_v": '<path d="M5 3h14l-7 6z"/><path d="M5 21h14l-7-6z"/>',
    "rotate_left": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>'
                   '<path d="M3 3v5h5"/>',
    "rotate_right": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/>'
                    '<path d="M21 3v5h-5"/>',
    "frame": '<rect x="3" y="3" width="18" height="18" rx="3"/>'
             '<rect x="7.5" y="7.5" width="9" height="9" rx="1"/>',
    "delete": '<polyline points="3 6 5 6 21 6"/>'
              '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4'
              'a2 2 0 0 1 2 2v2"/>'
              '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
    "color": '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5'
             'c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
}

_cache = {}


def names():
    return list(_SVGS.keys())


def get(name, color=None):
    """渲染指定图标为 QIcon；color 缺省时取应用文字色（随主题变化）。"""
    if color is None:
        app = QApplication.instance()
        color = app.palette().windowText().color().name() if app else "#333333"
    key = (name, color)
    if key in _cache:
        return _cache[key]
    body = _SVGS[name].replace("{c}", color)
    svg = _TEMPLATE.replace("{c}", color).replace("{b}", body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter, QRectF(0, 0, 64, 64))
    painter.end()
    icon = QIcon(pix)
    _cache[key] = icon
    return icon
