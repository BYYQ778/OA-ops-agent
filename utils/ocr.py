"""OCR 工具模块（共享）
--------------
封装 CnOCR（RapidOCR 后端），提供图片文字提取能力。
绿色版离线策略：显式指定 rapidocr 包内置的 ch_PP-OCRv4 检测/识别 ONNX 模型
与字体文件，避免运行期从 HuggingFace/OSS 下载模型（冻结环境离线不可用）。
"""

import os
import sys
import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger("ocr")

_ocr = None  # 全局单例，延迟加载


def _resolve_font_path():
    """优先使用打包字体（_MEIPASS/models/fonts），其次系统常见中文字体。"""
    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(os.path.join(bundle, "models", "fonts", "msyh.ttc"))
    candidates += [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _get_ocr():
    """懒加载 CnOCR 实例（RapidOCR v4 内置模型，完全离线）。"""
    global _ocr
    if _ocr is None:
        from cnocr import CnOcr
        from rapidocr import __file__ as _rapidocr_file

        _models_dir = os.path.join(os.path.dirname(_rapidocr_file), "models")
        _rec_fp = os.path.join(_models_dir, "ch_PP-OCRv4_rec_infer.onnx")
        _det_fp = os.path.join(_models_dir, "ch_PP-OCRv4_det_infer.onnx")
        if not (os.path.isfile(_rec_fp) and os.path.isfile(_det_fp)):
            raise FileNotFoundError(
                "RapidOCR 内置模型缺失: %s / %s" % (_rec_fp, _det_fp)
            )

        _font = _resolve_font_path()
        _extra = {"font_path": _font} if _font else {}
        logger.info(
            "正在加载 OCR 模型（RapidOCR v4 内置，离线模式）... font=%s", _font
        )
        _ocr = CnOcr(
            rec_model_name="ch_PP-OCRv4",
            det_model_name="ch_PP-OCRv4_det",
            rec_model_fp=_rec_fp,
            det_model_fp=_det_fp,
            rec_more_configs=dict(_extra),
            det_more_configs=dict(_extra),
        )
        logger.info("OCR 模型加载完成")
    return _ocr


def _bytes_to_ndarray(image_bytes: bytes) -> np.ndarray:
    """bytes -> RGB ndarray（RapidOCR 后端不支持直接传 bytes）。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return np.array(img)


def extract_text(image_path: str) -> str:
    """
    从图片中提取文字。

    Args:
        image_path: 图片文件路径（支持 jpg/png/bmp/tiff/webp 等）

    Returns:
        识别出的文字内容，段落之间用换行分隔
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    ocr = _get_ocr()
    results = ocr.ocr(image_path)
    lines = [item["text"] for item in results if item["text"].strip()]
    return "\n".join(lines)


def extract_text_from_bytes(image_bytes: bytes) -> str:
    """
    从图片二进制数据中提取文字（用于 Web 上传场景）。

    Args:
        image_bytes: 图片文件的二进制内容

    Returns:
        识别出的文字内容
    """
    ocr = _get_ocr()
    results = ocr.ocr(_bytes_to_ndarray(image_bytes))
    lines = [item["text"] for item in results if item["text"].strip()]
    return "\n".join(lines)


# 支持的图片扩展名
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
