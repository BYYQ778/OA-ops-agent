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
]

# ---- OCR 相关包的数据文件（yaml/onnx/字典）与动态库、子模块 ----
for _pkg in ("cnocr", "cnstd", "rapidocr"):
    datas += collect_data_files(_pkg)
    datas += collect_dynamic_libs(_pkg)
    hiddenimports += collect_submodules(_pkg)

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

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "jupyter_client",
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