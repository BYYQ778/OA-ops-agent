"""
OCR 工具模块（共享）
--------------
封装 CnOCR，提供图片文字提取能力。
首次调用自动下载模型，后续使用缓存。
CnOCR 模型走 HuggingFace，通过 hf-mirror.com 国内直连。
"""

import os
import logging

logger = logging.getLogger("ocr")

_ocr = None  # 全局单例，延迟加载


def _get_ocr():
    """懒加载 CnOCR 实例，全局复用避免反复初始化。"""
    global _ocr
    if _ocr is None:
        from cnocr import CnOcr
        logger.info("正在加载 OCR 模型（首次约需下载模型文件）...")
        _ocr = CnOcr()
        logger.info("OCR 模型加载完成")
    return _ocr


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
    results = ocr.ocr(image_bytes)
    lines = [item["text"] for item in results if item["text"].strip()]
    return "\n".join(lines)


# 支持的图片扩展名
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
