"""
MinerU 适配器（可选模块）
-----------------------
封装 magic-pdf（MinerU），提供统一的多模态文档解析接口。
MinerU 未安装时自动不可用，上游调用方应检查 is_mineru_available() 并回退。

安装 MinerU（需要 ~3GB 模型下载）:
    pip install magic-pdf
    magic-pdf --help  # 首次运行会下载模型
"""

import os
import logging

logger = logging.getLogger("mineru")

_MINERU_AVAILABLE = False
_mineru_checked = False


def is_mineru_available() -> bool:
    """检测 MinerU (magic-pdf) 是否可用。"""
    global _MINERU_AVAILABLE, _mineru_checked
    if _mineru_checked:
        return _MINERU_AVAILABLE
    _mineru_checked = True
    try:
        import magic_pdf  # noqa: F401
        _MINERU_AVAILABLE = True
        logger.info("MinerU (magic-pdf) 已就绪")
    except ImportError:
        logger.info("MinerU (magic-pdf) 未安装，使用传统解析器")
    return _MINERU_AVAILABLE


class MinerUParser:
    """
    MinerU 文档解析器（单例）。

    使用方式:
        if is_mineru_available():
            parser = MinerUParser()
            result = parser.parse("/path/to/doc.pdf")
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            if not is_mineru_available():
                raise RuntimeError(
                    "MinerU (magic-pdf) 未安装。安装: pip install magic-pdf"
                )
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        logger.info("正在初始化 MinerU 解析器...")
        # MinerU 的模型在首次解析时自动加载，此处无需显式初始化
        # 预留：未来可添加设备选择等配置
        self._ready = True

    def parse(self, file_path: str) -> dict:
        """
        解析文档，返回结构化内容。

        Args:
            file_path: 文档路径（支持 PDF/DOCX/PPTX/HTML/图片等）

        Returns:
            {
                "text": str,           # 正文纯文本
                "tables": [str],       # 表格（Markdown 格式）
                "formulas": [str],     # 公式（LaTeX 格式）
                "images": [str],       # 图片中提取的文字
                "metadata": {...}      # 文档元信息
            }
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        logger.info(f"MinerU 解析: {os.path.basename(file_path)}")

        try:
            from magic_pdf.data.data_reader_writer import (
                FileBasedDataWriter,
                FileBasedDataReader,
            )
            from magic_pdf.data.dataset import PymuDocDataset
            from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

            # 读取文档
            reader = FileBasedDataReader("")
            pdf_bytes = reader.read(file_path)

            # 创建数据集
            ds = PymuDocDataset(pdf_bytes)

            # 根据文档类型选择处理管线
            if ds.classify() == "ocr":
                infer_result = ds.apply(doc_analyze, ocr=True)
                pipe_result = infer_result.pipe_ocr_mode(
                    FileBasedDataWriter("data/mineru_output")
                )
            else:
                infer_result = ds.apply(doc_analyze, ocr=False)
                pipe_result = infer_result.pipe_txt_mode(
                    FileBasedDataWriter("data/mineru_output")
                )

            # 提取结构化内容
            text = pipe_result.get_content()
            tables = pipe_result.get_tables() if hasattr(pipe_result, "get_tables") else []
            formulas = []

            return {
                "text": text or "",
                "tables": tables or [],
                "formulas": formulas,
                "images": [],
                "metadata": {
                    "parser": "mineru",
                    "file_name": os.path.basename(file_path),
                    "classify": ds.classify(),
                },
            }

        except ImportError:
            raise RuntimeError("MinerU 依赖未安装: pip install magic-pdf")
        except Exception as e:
            logger.error(f"MinerU 解析失败 [{file_path}]: {e}")
            raise RuntimeError(f"MinerU 解析失败: {e}")


def try_mineru_parse(file_path: str) -> dict:
    """
    尝试用 MinerU 解析，不可用时返回 None。

    调用方无需处理异常，返回 None 表示应回退到传统解析器。
    """
    if not is_mineru_available():
        return None
    try:
        parser = MinerUParser()
        return parser.parse(file_path)
    except Exception as e:
        logger.warning(f"MinerU 解析失败，将回退传统解析器: {e}")
        return None
