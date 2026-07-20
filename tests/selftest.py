"""离屏自检：不打开窗口，验证编辑、变换、保存、二次编辑全链路。

运行方式:
    python main.py --selftest
或打包后:
    ImageEditor.exe --selftest
"""

import os
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app import io as editor_io
from app.annotations import translate_anno
from app.canvas import EditorCanvas

W, H = 800, 600


def _make_canvas():
    c = EditorCanvas()
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    c.set_image(img)
    return c


def _add_all_tools(c):
    c.add_annotation({"type": "rect", "rect": [100, 100, 200, 120],
                      "color": "#FF0000", "width": 3})
    c.add_annotation({"type": "ellipse", "rect": [350, 100, 150, 100],
                      "color": "#00AA00", "width": 3})
    c.add_annotation({"type": "line", "p1": [100, 300], "p2": [500, 300],
                      "color": "#0000FF", "width": 3})
    c.add_annotation({"type": "arrow", "p1": [100, 350], "p2": [500, 450],
                      "color": "#FF0000", "width": 3})
    c.add_annotation({"type": "number", "center": [600, 120], "n": 1, "r": 16,
                      "color": "#FF0000", "width": 2})
    c.add_annotation({"type": "text", "center": [400, 250], "text": "测试 Text 123",
                      "font_size": 24, "color": "#000000", "rotation": 0.0})
    c.add_annotation({"type": "path",
                      "points": [[100, 500], [200, 520], [300, 480], [400, 510]],
                      "color": "#8800FF", "width": 4})
    c.add_annotation({"type": "mosaic", "rect": [90, 90, 60, 60],
                      "color": "#FF0000", "width": 3})
    return 8


def run():
    app = QApplication.instance() or QApplication([])
    tmpdir = tempfile.mkdtemp(prefix="imgeditor_test_")

    # 1. 全部标注工具 + 马赛克渲染
    c = _make_canvas()
    n = _add_all_tools(c)
    assert len(c.collect_annotations()) == n, "标注数量不符"
    final = c.render_final()
    assert (final.width(), final.height()) == (W, H), "合成尺寸不符"
    # 马赛克区域压住了红色矩形的左上角，像素化后不应再是纯白
    px = final.pixelColor(101, 101)
    assert px != QColor("white"), f"马赛克未生效: {px.name()}"

    # 2. 翻转 / 旋转 / 裁剪（对底图与标注的坐标变换）
    c.flip_horizontal()
    rect = next(a for a in c.collect_annotations() if a["type"] == "rect")
    assert abs(rect["rect"][0] - (W - 100 - 200)) < 1, "水平翻转坐标错误"
    c.flip_vertical()
    assert abs(rect["rect"][1] - (H - 100 - 120)) < 1, "垂直翻转坐标错误"
    c.flip_horizontal()
    c.flip_vertical()  # 还原
    assert abs(rect["rect"][0] - 100) < 1 and abs(rect["rect"][1] - 100) < 1
    c.rotate(cw=True)
    assert (c.base_image.width(), c.base_image.height()) == (H, W), "旋转尺寸错误"
    text = next(a for a in c.collect_annotations() if a["type"] == "text")
    assert abs(text["rotation"] - 90) < 1e-6, "文字旋转角度错误"
    c.rotate(cw=False)  # 还原
    assert abs(text["rotation"]) < 1e-6
    c.enter_crop_mode()
    assert c._crop_mode and c._crop_overlay is not None, "未进入裁剪模式"
    c.confirm_crop(QRectF(50, 50, 650, 500))
    assert (c.base_image.width(), c.base_image.height()) == (650, 500), "裁剪尺寸错误"
    assert len(c.collect_annotations()) == n, "裁剪不应丢失任何标注"
    assert c.original_info is not None, "裁剪历史未保存"
    rect = next(a for a in c.collect_annotations() if a["type"] == "rect")
    assert abs(rect["rect"][0] - 50) < 1 and abs(rect["rect"][1] - 50) < 1, "裁剪平移错误"

    # 3. 保存 PNG（含可编辑元数据）并重新打开
    p1 = os.path.join(tmpdir, "round1.png")
    editor_io.save_with_metadata(p1, c.render_final(), c.base_image,
                                 c.collect_annotations(),
                                 original=c.original_info)
    assert os.path.getsize(p1) > 0
    base, anns, frame1, orig1, crop1 = editor_io.load_image(p1)
    assert anns is not None, "未读到可编辑元数据"
    assert frame1 is None, "未设置边框时 frame 应为 None"
    assert len(anns) == n, f"元数据标注数量不符: {len(anns)}"
    assert (base.width(), base.height()) == (650, 500), "元数据底图尺寸不符"
    assert orig1 is not None and (orig1.width(), orig1.height()) == (800, 600), \
        "原图未随元数据保存"
    assert crop1 is not None and abs(crop1.x() - 50) < 1 \
        and abs(crop1.width() - 650) < 1, "裁剪选区未随元数据保存"

    # 4. 二次编辑：载入后修改一个标注，再保存再打开验证
    c2 = EditorCanvas()
    c2.set_image(base, anns)
    c2.set_original(orig1, crop1)
    assert len(c2.collect_annotations()) == n
    rect_item = next(it for it in c2._anno_items() if it.anno["type"] == "rect")
    rect_item.prepareGeometryChange()
    translate_anno(rect_item.anno, 10, 5)
    rect_item.update()
    p2 = os.path.join(tmpdir, "round2.png")
    editor_io.save_with_metadata(p2, c2.render_final(), c2.base_image,
                                 c2.collect_annotations(),
                                 original=c2.original_info)
    _, anns2, _ = editor_io.load_image(p2)[:3]
    rect2 = next(a for a in anns2 if a["type"] == "rect")
    assert abs(rect2["rect"][0] - 60) < 1 and abs(rect2["rect"][1] - 55) < 1, \
        "二次编辑未生效"

    # 5. 普通 PNG（无元数据）按普通图片打开
    p3 = os.path.join(tmpdir, "plain.png")
    c2.render_final().save(p3, "PNG")
    base3, anns3, frame3 = editor_io.load_image(p3)[:3]
    assert anns3 is None and frame3 is None and not base3.isNull(), "普通 PNG 打开失败"

    # 6. SVG 图标全部可正常渲染（非全透明）
    from app import icons
    for name in icons.names():
        pix = icons.get(name, "#333333").pixmap(32, 32)
        img = pix.toImage()
        assert any(img.pixelColor(x, y).alpha() > 0
                   for x in range(0, 32, 4) for y in range(0, 32, 4)), \
            f"图标渲染为空: {name}"

    # 7. 橡皮擦删除标注
    from PySide6.QtCore import QPointF
    before = len(c2.collect_annotations())
    c2.erase_at(QPointF(250, 165))  # rect 右下角附近（裁剪后平移到 60,55）
    assert len(c2.collect_annotations()) == before - 1, "橡皮擦未删除标注"

    # 8. 边框装裱（场景实时显示）：尺寸、纯色背景、圆角透明角、元数据往返
    from app.frame import DEFAULT_SETTINGS
    s = dict(DEFAULT_SETTINGS)
    s.update({"bg_mode": "solid", "bg_color": "#112233", "padding": 40,
              "frame_radius": 24, "shadow_size": 20})
    c2.set_frame(s)
    assert c2.frame_settings is not None, "边框未应用到画布"
    out = c2.render_output()  # 650x500 + 2*40 边距
    assert (out.width(), out.height()) == (730, 580), "边框尺寸错误"
    bg = out.pixelColor(365, 10)  # 顶边中点：背景色
    assert abs(bg.red() - 0x11) < 4 and abs(bg.green() - 0x22) < 4 \
        and abs(bg.blue() - 0x33) < 4, f"背景色错误: {bg.name()}"
    assert out.pixelColor(1, 1).alpha() == 0, "边框圆角外应为透明"
    center = out.pixelColor(365, 290)  # 中心仍是图片内容
    assert center.alpha() == 255, "中心应是不透明图片内容"

    p4 = os.path.join(tmpdir, "framed.png")
    editor_io.save_with_metadata(p4, out, c2.base_image,
                                 c2.collect_annotations(), frame=c2.frame_settings)
    _, anns4, frame4 = editor_io.load_image(p4)[:3]
    assert frame4 and frame4["padding"] == 40 and frame4["enabled"], \
        "边框设置未随元数据保存"
    assert anns4 is not None, "带边框的 PNG 标注元数据丢失"

    # 9. 渐变背景 + 移除边框
    s2 = dict(DEFAULT_SETTINGS)
    s2["bg_mode"] = "gradient"
    c2.set_frame(s2)
    assert (c2.render_output().width(), c2.render_output().height()) != (650, 500)
    c2.set_frame(None)
    assert c2.frame_settings is None
    out2 = c2.render_output()
    assert (out2.width(), out2.height()) == (650, 500), "移除边框后尺寸应还原"

    # 10. 粘贴与拖放
    from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from app.main_window import MainWindow
    win = MainWindow()

    # 粘贴位图（截图来源）
    clip = app.clipboard()
    raw = QImage(320, 240, QImage.Format_ARGB32)
    raw.fill(QColor("#123456"))
    clip.setImage(raw)
    win._paste_from_clipboard()
    assert win.canvas.has_image and win.canvas.base_image.width() == 320, \
        "粘贴位图失败"

    # 粘贴文件（含元数据的 PNG → 恢复标注，p2 保存时有 8 个标注）
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p2)])
    clip.setMimeData(mime)
    win._paste_from_clipboard()
    assert len(win.canvas.collect_annotations()) == 8, "粘贴文件未恢复标注"

    # 拖入文件：dragEnter 接受、drop 发出 file_dropped 信号
    # 注意：上面的 mime 所有权已被 clipboard 接管，拖放测试须用新的 QMimeData
    mime2 = QMimeData()
    mime2.setUrls([QUrl.fromLocalFile(p2)])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime2,
                            Qt.LeftButton, Qt.NoModifier)
    win.canvas.dragEnterEvent(enter)
    assert enter.isAccepted(), "拖入文件未被接受"
    got = []
    win.canvas.file_dropped.connect(got.append)
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime2,
                      Qt.LeftButton, Qt.NoModifier)
    win.canvas.dropEvent(drop)
    assert got == [QUrl.fromLocalFile(p2).toLocalFile()], "拖入文件信号错误"

    # 拖入位图数据（浏览器来源）
    mime_img = QMimeData()
    mime_img.setImageData(raw)
    imgs = []
    win.canvas.image_dropped.connect(imgs.append)
    drop_img = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime_img,
                          Qt.LeftButton, Qt.NoModifier)
    win.canvas.dropEvent(drop_img)
    assert imgs and imgs[0].width() == 320, "拖入位图数据失败"
    # 释放剪贴板持有的 QMimeData，避免解释器退出时的释放顺序崩溃
    clip.clear()

    # 11. 选框缩放
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    from app.annotations import apply_bbox_resize, bbox_with_handle

    c3 = _make_canvas()
    c3.resize(900, 700)
    rect_item = c3.add_annotation({"type": "rect", "rect": [100, 100, 200, 120],
                                   "color": "#FF0000", "width": 3})
    rect_item.setSelected(True)
    box = c3._selection_box
    assert box.isVisible() and box.target() is rect_item, "单选后选框未显示"

    # 鼠标级：拖 br 手柄 (300,220) → (350,260)，矩形应变为 [100,100,250,160]
    vp0 = c3.mapFromScene(box.handles()["br"])
    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(vp0),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    c3.mousePressEvent(press)
    assert c3._resize is not None, "按下手柄未进入缩放状态"
    vp1 = c3.mapFromScene(QPointF(350, 260))
    c3.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(vp1),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    c3.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp1),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    r = rect_item.anno["rect"]
    assert all(abs(a - b) < 1.5 for a, b in
               zip(r, [100, 100, 250, 160])), f"手柄缩放结果错误: {r}"

    # 几何级：直线（退化包围盒）、序号、文字
    nb = bbox_with_handle(QRectF(100, 300, 400, 0), "rc", QPointF(600, 300))
    line_anno = {"type": "line", "p1": [100, 300], "p2": [500, 300]}
    orig = {"type": "line", "p1": [100, 300], "p2": [500, 300]}
    apply_bbox_resize(line_anno, orig, QRectF(100, 300, 400, 0), nb)
    assert abs(line_anno["p1"][0] - 100) < 1 and abs(line_anno["p2"][0] - 600) < 1 \
        and abs(line_anno["p2"][1] - 300) < 1, f"直线缩放错误: {line_anno}"

    num = {"type": "number", "center": [600, 120], "n": 1, "r": 16}
    apply_bbox_resize(num, dict(num), QRectF(584, 104, 32, 32),
                      QRectF(580, 100, 40, 40))
    assert abs(num["r"] - 20) < 1 and abs(num["center"][0] - 600) < 1, \
        f"序号缩放错误: {num}"

    txt = {"type": "text", "center": [400, 250], "text": "abc",
           "font_size": 24, "rotation": 0.0}
    apply_bbox_resize(txt, dict(txt), QRectF(370, 236, 60, 28),
                      QRectF(340, 222, 120, 56))
    assert txt["font_size"] == 48 and abs(txt["center"][0] - 400) < 1, \
        f"文字缩放错误: {txt}"

    # 取消选择后选框隐藏
    c3._scene.clearSelection()
    assert not c3._selection_box.isVisible(), "取消选择后选框应隐藏"

    # 12. 画布内嵌文字输入
    c4 = _make_canvas()
    c4.resize(900, 700)
    c4.set_tool("text")
    vp = c4.mapFromScene(QPointF(200, 200))
    c4.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    ed = c4._text_editor
    assert ed is not None, "文字工具单击未出现输入框"
    ed.setPlainText("画布输入")
    c4._commit_text_editor(ed, cancel=False)
    texts = [a for a in c4.collect_annotations() if a["type"] == "text"]
    assert len(texts) == 1 and texts[0]["text"] == "画布输入", "内嵌输入未生成文字标注"
    # 点击处为输入起点，文字向右下生长，中心应位于点击位置右下方
    assert texts[0]["center"][0] >= 200 and 200 <= texts[0]["center"][1] < 260, \
        "文字锚点位置偏差过大"

    # Esc 取消不产生标注
    vp = c4.mapFromScene(QPointF(400, 300))
    c4.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    ed2 = c4._text_editor
    assert ed2 is not None
    ed2.setPlainText("废弃")
    c4._commit_text_editor(ed2, cancel=True)
    assert len([a for a in c4.collect_annotations() if a["type"] == "text"]) == 1, \
        "Esc 取消仍生成了标注"

    # 双击已有文字标注 → 内嵌编辑修改
    c4.set_tool("select")
    t_item = next(it for it in c4._anno_items() if it.anno["type"] == "text")
    cx, cy = t_item.anno["center"]
    vp = c4.mapFromScene(QPointF(cx, cy))
    c4.mouseDoubleClickEvent(QMouseEvent(
        QEvent.MouseButtonDblClick, QPointF(vp),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    ed3 = c4._text_editor
    assert ed3 is not None and ed3.toPlainText() == "画布输入", "双击未进入内嵌编辑"
    assert not t_item.isVisible(), "编辑期间原标注应隐藏"
    ed3.setPlainText("改过的文字")
    c4._commit_text_editor(ed3, cancel=False)
    assert t_item.anno["text"] == "改过的文字" and t_item.isVisible(), \
        "内嵌编辑未生效"

    # 13. 工具参数体系（填充/字体样式/文字描边背景/块大小/序号大小）与侧栏
    from app.sidebar import ToolSidebar
    c5 = _make_canvas()
    sidebar = ToolSidebar(c5)

    # 新标注取画布默认参数
    c5.set_fill_enabled(True)
    c5.set_fill_opacity(50)
    r_item = c5.add_annotation({"type": "rect", "rect": [10, 10, 100, 80]})
    assert r_item.anno["fill"] == c5._fill_color.name() \
        and r_item.anno["fill_opacity"] == 50, "填充默认值未写入标注"

    # 选中后调参实时作用于标注
    c5.set_tool("select")
    r_item.setSelected(True)
    c5.set_fill_opacity(80)
    assert r_item.anno["fill_opacity"] == 80, "填充透明度未作用于选中标注"
    c5.set_fill_enabled(False)
    assert r_item.anno["fill"] is None, "取消填充未生效"
    c5.set_width(7)
    assert r_item.anno["width"] == 7, "线宽未作用于选中标注"

    # 文字样式
    c5.set_bold(True)
    c5.set_italic(True)
    c5.set_outline_width(2)
    c5.set_text_bg_enabled(True)
    t5 = c5.add_annotation({"type": "text", "center": [300, 200],
                            "text": "样式", "font_size": 30})
    assert t5.anno["bold"] and t5.anno["italic"], "粗体/斜体未写入"
    assert t5.anno["outline_width"] == 2 and t5.anno["outline_color"], "文字描边未写入"
    assert t5.anno["bg_color"], "文字背景未写入"
    c5.render_final()  # 渲染不报错

    # 选中文字标注改字号
    t5.setSelected(True)
    c5.set_font_size(40)
    assert t5.anno["font_size"] == 40, "字号未作用于选中文字"

    # 马赛克块大小：默认值 + 选中修改
    c5.set_mosaic_block(20)
    m5 = c5.add_annotation({"type": "mosaic", "rect": [200, 200, 80, 60]})
    assert m5.anno["block"] == 20, "马赛克块大小默认值未写入"
    c5._scene.clearSelection()
    m5.setSelected(True)
    c5.set_mosaic_block(30)
    assert m5.anno["block"] == 30, "马赛克块大小未作用于选中标注"
    c5.refresh_mosaics()

    # 序号大小默认值
    c5.set_number_r(30)
    n5 = c5.add_annotation({"type": "number", "center": [500, 100], "n": 1})
    assert n5.anno["r"] == 30.0, "序号半径默认值未写入"

    # 橡皮擦大小
    c5.set_eraser_size(33)
    assert c5._eraser_size == 33

    # 侧栏：逐工具/选中类型切换页面不报错，选中标注时联动到对应页面
    for tool in ("select", "crop", "rect", "ellipse", "line", "arrow",
                 "number", "text", "brush", "eraser", "mosaic"):
        sidebar.show_tool(tool)
    c5._scene.clearSelection()
    sidebar.show_tool("select")
    assert sidebar._stack.currentWidget() is sidebar._pages["select"]
    m5.setSelected(True)
    assert sidebar._stack.currentWidget() is sidebar._pages["mosaic"], \
        "选中马赛克时侧栏未联动"

    # 14. 重新裁剪：恢复原图、选区还原、确认/取消/翻转失效（用 p2 元数据）
    base_r, anns_r, _, orig_r, crop_r = editor_io.load_image(p2)
    c7 = EditorCanvas()
    c7.set_image(base_r, anns_r)
    c7.set_original(orig_r, crop_r)
    assert c7.original_info is not None
    r0 = next(a for a in c7.collect_annotations() if a["type"] == "rect")
    assert abs(r0["rect"][0] - 60) < 1 and abs(r0["rect"][1] - 55) < 1

    c7.enter_crop_mode()
    assert (c7.base_image.width(), c7.base_image.height()) == (800, 600), \
        "重新裁剪应显示完整原图"
    r1 = next(a for a in c7.collect_annotations() if a["type"] == "rect")
    assert abs(r1["rect"][0] - 110) < 1 and abs(r1["rect"][1] - 105) < 1, \
        "标注未映射回原图坐标"
    ov_r = c7._crop_overlay.rect()
    assert abs(ov_r.x() - 50) < 1 and abs(ov_r.width() - 650) < 1, "选区未还原"
    # 取消：回到裁剪后状态
    c7.cancel_crop()
    assert (c7.base_image.width(), c7.base_image.height()) == (650, 500)
    r2 = next(a for a in c7.collect_annotations() if a["type"] == "rect")
    assert abs(r2["rect"][0] - 60) < 1 and abs(r2["rect"][1] - 55) < 1, "取消裁剪未还原"
    # 再次进入并扩大选区确认
    c7.enter_crop_mode()
    c7.confirm_crop(QRectF(0, 0, 700, 550))
    assert (c7.base_image.width(), c7.base_image.height()) == (700, 550)
    r3 = next(a for a in c7.collect_annotations() if a["type"] == "rect")
    assert abs(r3["rect"][0] - 110) < 1 and abs(r3["rect"][1] - 105) < 1
    # 选区拉回整图 = 移除裁剪
    c7.enter_crop_mode()
    c7.confirm_crop(QRectF(0, 0, 800, 600))
    assert c7._original is None, "选区=整图应移除裁剪历史"
    assert (c7.base_image.width(), c7.base_image.height()) == (800, 600)
    # 翻转后裁剪历史失效
    c7.enter_crop_mode()
    c7.confirm_crop(QRectF(50, 50, 650, 500))
    assert c7._original is not None
    c7.flip_horizontal()
    assert c7._original is None, "翻转后裁剪历史应失效"

    # 15. 裁剪覆盖层鼠标交互：拖手柄缩放选区、点 ✓ 确认
    c8 = _make_canvas()
    c8.resize(900, 700)
    c8.set_tool("crop")
    assert c8._crop_mode, "点击裁剪工具未进入裁剪模式"
    ov = c8._crop_overlay
    assert abs(ov.rect().width() - 800) < 1, "初始选区应为整图"
    # 覆盖层绘制冒烟（离屏不会触发视口重绘，手动调用）
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    _img = QImage(900, 700, QImage.Format_ARGB32_Premultiplied)
    _p = QPainter(_img)
    ov.paint(_p, QStyleOptionGraphicsItem(), None)
    _p.end()
    vp0 = c8.mapFromScene(QPointF(800, 600))
    c8.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp0),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert c8._crop_drag and c8._crop_drag["kind"] == "resize", "手柄拖拽未识别"
    vp1 = c8.mapFromScene(QPointF(700, 500))
    c8.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(vp1),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    c8.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp1),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert abs(ov.rect().width() - 700) < 2 and abs(ov.rect().height() - 500) < 2, \
        f"手柄缩放选区错误: {ov.rect()}"
    # 点击 ✓ 按钮确认
    bp = c8.mapFromScene(ov.button_pos("ok"))
    c8.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(bp),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert not c8._crop_mode and c8.base_image.width() == 700, "✓ 按钮未确认裁剪"

    # 16. 绘制工具下同类型标注即选即调
    c9 = _make_canvas()
    c9.resize(900, 700)
    c9.set_tool("rect")
    target = c9.add_annotation({"type": "rect", "rect": [100, 100, 200, 120],
                                "color": "#FF0000", "width": 3})
    count0 = len(c9.collect_annotations())
    # 点击已有矩形本体 → 选中而不是新增
    vp = c9.mapFromScene(QPointF(200, 160))
    c9.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert len(c9.collect_annotations()) == count0, "同类型点击不应新增标注"
    assert target.isSelected() and c9._adjust is not None, "同类型点击未选中"
    # 拖动移动 +50, +30
    vp1 = c9.mapFromScene(QPointF(250, 190))
    c9.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(vp1),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    c9.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp1),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    r = target.anno["rect"]
    assert all(abs(a - b) < 1.5 for a, b in zip(r, [150, 130, 200, 120])), \
        f"同类型拖动移动错误: {r}"
    # 拖 br 手柄缩放
    vp2 = c9.mapFromScene(c9._selection_box.handles()["br"])
    c9.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp2),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert c9._resize is not None, "绘制工具下手柄缩放未进入"
    vp3 = c9.mapFromScene(QPointF(450, 350))
    c9.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(vp3),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    c9.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp3),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    r = target.anno["rect"]
    assert all(abs(a - b) < 1.5 for a, b in zip(r, [150, 130, 300, 220])), \
        f"绘制工具下手柄缩放错误: {r}"
    # 点击空白 → 取消选中并开始新增
    vp4 = c9.mapFromScene(QPointF(600, 450))
    c9.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(vp4),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert not target.isSelected(), "点空白未取消软选中"
    vp5 = c9.mapFromScene(QPointF(660, 500))
    c9.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(vp5),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    c9.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(vp5),
                                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    assert len(c9.collect_annotations()) == count0 + 1, "点空白后应正常新增"

    print("SELFTEST PASS")
    print(f"  测试产物目录: {tmpdir}")
    return 0
