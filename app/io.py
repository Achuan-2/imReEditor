"""PNG 读写：在 PNG 里嵌入可编辑数据，实现二次编辑。

保存内容 = 两部分：
1. PNG 像素本身：底图 + 标注 + 边框装裱合成后的最终效果（任何看图软件可见）；
2. zTXt 元数据块（key 为 ImageEditorData）：JSON 字符串，包含
   - base: 当前底图（不含标注）的 PNG 字节（base64）
   - annotations: 全部标注的矢量数据
   - frame: 边框装裱参数（可为 null）
   - original: 未裁剪原图的 PNG 字节（base64，可为 null）
   - crop: 当前裁剪选区 [x, y, w, h]（原图坐标系，可为 null）

original + crop 使裁剪可逆：重新打开后可随时恢复原图重选裁剪区域。
重新打开时若检测到元数据，则还原底图、标注、边框与裁剪历史；
否则按普通图片打开。
"""

import base64
import io
import json

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage

META_KEY = "ImageEditorData"
FORMAT_VERSION = 3


def _qimage_to_png_bytes(img):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def save_with_metadata(path, final_img, base_img, annotations, frame=None,
                       original=None):
    """把合成结果与可编辑数据一起写入 PNG。

    original: None 或 {"base": QImage, "crop_rect": QRectF}（裁剪历史）。
    """
    orig_b64 = None
    crop = None
    if original:
        rect = original["crop_rect"]
        orig_b64 = base64.b64encode(
            _qimage_to_png_bytes(original["base"])).decode("ascii")
        crop = [rect.x(), rect.y(), rect.width(), rect.height()]
    payload = {
        "version": FORMAT_VERSION,
        "base": base64.b64encode(_qimage_to_png_bytes(base_img)).decode("ascii"),
        "annotations": annotations,
        "frame": frame,
        "original": orig_b64,
        "crop": crop,
    }
    im = Image.open(io.BytesIO(_qimage_to_png_bytes(final_img)))
    meta = PngInfo()
    meta.add_text(META_KEY, json.dumps(payload, ensure_ascii=False), zip=True)
    im.save(path, "PNG", pnginfo=meta)


def load_image(path):
    """打开图片。

    返回 (base_image, annotations, frame, original, crop_rect)。
    annotations 为 None 表示普通图片（无可编辑数据），其余字段相应为空。
    """
    raw = None
    try:
        with Image.open(path) as im:
            raw = im.info.get(META_KEY)
    except Exception:
        raw = None

    if raw:
        try:
            payload = json.loads(raw)
            annotations = payload.get("annotations", [])
            frame = payload.get("frame")
            original = None
            crop_rect = None
            orig_b64 = payload.get("original")
            if orig_b64:
                img = QImage.fromData(base64.b64decode(orig_b64), "PNG")
                if not img.isNull():
                    original = img
            if payload.get("crop"):
                crop_rect = QRectF(*payload["crop"])
            base = None
            if original is not None and crop_rect is not None:
                base = original.copy(crop_rect.toAlignedRect())
            elif original is not None:
                base = original
            else:
                base = QImage.fromData(base64.b64decode(payload["base"]), "PNG")
            if not base.isNull():
                return base, annotations, frame, original, crop_rect
        except Exception:
            pass

    return QImage(path), None, None, None, None
