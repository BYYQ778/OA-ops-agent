# -*- mode: python ; coding: utf-8 -*-
"""OA运维Agent 桌面版 PyInstaller 打包配置
用法:  env_new\Scripts\python.exe -m PyInstaller oa_agent.spec --noconfirm --clean
产物:  dist\OA运维Agent\OA运维Agent.exe（绿色版，免装 Python）
"""
import os
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, collect_dynamic_libs, collect_all,
)

ROOT = os.path.abspath(SPECPATH)
HF_HUB = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")

datas = []
binaries = []
hiddenimports = []

# ---- 前端资源 / 默认配置 / 图标 ----
datas += [
    ("ui/templates", "ui/templates"),
    ("ui/static", "ui/static"),
    ("config.yaml", "."),
    ("oa_agent.ico", "."),
    (".env.example", "."),
    ("使用说明.txt", "."),
]

# ---- OCR 相关包的数据文件（yaml/onnx/字典）与动态库、子模块 ----
for _pkg in ("cnocr", "cnstd", "rapidocr"):
    datas += collect_data_files(_pkg)
    datas += collect_dynamic_libs(_pkg)
    hiddenimports += collect_submodules(_pkg)

# ---- OCR 内置中文字体（rapidocr 可视化需要，离线可用）----
_FONT_SRC = r"C:\Windows\Fonts\msyh.ttc"
if os.path.isfile(_FONT_SRC):
    datas.append((_FONT_SRC, os.path.join("models", "fonts", "msyh.ttc")))
else:
    print("[spec] 警告: 系统字体不存在，OCR 将回退系统字体:", _FONT_SRC)
# ---- matplotlib（cnstd.yolov7.plots 模块级依赖，OCR 必需；不能排除）----
_mpl_datas, _mpl_binaries, _mpl_hidden = collect_all("matplotlib")
datas += _mpl_datas
binaries += _mpl_binaries
hiddenimports += _mpl_hidden
hiddenimports += ["matplotlib.backends.backend_agg", "contourpy", "cycler", "kiwisolver", "pyparsing"]
# ---- chromadb 动态导入（telemetry 等），必须全量收集 ----
_chroma_datas, _chroma_binaries, _chroma_hidden = collect_all("chromadb")
datas += _chroma_datas
binaries += _chroma_binaries
hiddenimports += _chroma_hidden

# ---- 离线模型（HF hub 缓存 -> models/hf/hub，桌面版启动时 HF_HOME 指向这里）----
for _m in (
    "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
    "models--breezedeus--cnstd-ppocr-ch_PP-OCRv5_det",
    "models--sentence-transformers--all-MiniLM-L6-v2",
):
    _src = os.path.join(HF_HUB, _m)
    if os.path.isdir(_src):
        datas.append((_src, os.path.join("models", "hf", "hub", _m)))
    else:
        print("[spec] 警告: 模型目录不存在:", _src)

# ---- 隐藏导入 ----
hiddenimports += [
    "webview", "webview.platforms.winforms", "webview.platforms.edgechromium",
    "clr_loader", "clr_loader.pythonnet", "pythonnet", "bottle",
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "langchain_huggingface", "sentence_transformers",
    "networkx", "paramiko", "apscheduler", "yaml", "tiktoken", "pptx", "bs4", "lxml",
]
hiddenimports = list(dict.fromkeys(hiddenimports))

excludes = [# matplotlib 必须保留: cnstd.yolov7.plots 模块级 import matplotlib, 排除会导致冻结版 OCR 报 No module named 'matplotlib'
    
    "tkinter", "IPython", "jupyter", "jupyter_client",
    "pytest", "PyQt5", "PySide2", "magic_pdf", "gradio", "notebook",
    "streamlit", "modelscope",
]

a = Analysis(
    ["desktop_app.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OA运维Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="oa_agent.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OA运维Agent",
)