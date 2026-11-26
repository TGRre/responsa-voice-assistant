# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Responsa Voice Assistant (Windows desktop app).
Run from repository root:
    pyinstaller --clean --noconfirm installer/responsa_voice.spec
"""

from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH).resolve().parent
VOICE_DIR = ROOT / "responsa_voice"
ENGINE_DIR = ROOT / "responsa_voice_engine"
ICON_PATH = ROOT / "installer" / "app_icon.ico"


def _safe_collect_data(pkg: str):
    try:
        return collect_data_files(pkg)
    except Exception:
        return []


def _safe_collect_libs(pkg: str):
    try:
        return collect_dynamic_libs(pkg)
    except Exception:
        return []


def _safe_collect_submodules(pkg: str):
    try:
        return collect_submodules(pkg)
    except Exception:
        return []


datas = [
    (str(VOICE_DIR / "books_data.json"), "."),
    (str(ENGINE_DIR / "data"), "responsa_voice_engine/data"),
    (str(ENGINE_DIR / "src"), "responsa_voice_engine/src"),
]
for _pkg in ("faster_whisper", "sounddevice", "_sounddevice_data", "ctranslate2", "onnxruntime", "tokenizers"):
    datas += _safe_collect_data(_pkg)

binaries = []
for _pkg in ("ctranslate2", "onnxruntime", "sounddevice", "_sounddevice_data", "av", "webrtcvad"):
    binaries += _safe_collect_libs(_pkg)

hiddenimports = [
    "app_settings",
    "ai_verifier",
    "book_catalog",
    "config",
    "engine_bridge",
    "intent_router",
    "overlay",
    "responsa_controller",
    "voice_pipeline",
    "src",
    "src.fallback_llm",
    "src.formatter",
    "src.hebrew_utils",
    "src.matcher",
    "src.models",
    "src.schema_loader",
    "src.whisper_config",
    "win32com",
    "win32com.client",
    "win32gui",
    "win32con",
    "win32process",
    "win32api",
    "pythoncom",
    "pywintypes",
    "webrtcvad",
    "_webrtcvad",
    "sounddevice",
    "_sounddevice_data",
    "keyboard",
    "pyperclip",
    "psutil",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
]
for _pkg in ("rapidfuzz", "pydantic", "faster_whisper", "ctranslate2", "openai"):
    hiddenimports += _safe_collect_submodules(_pkg)

a = Analysis(
    [str(VOICE_DIR / "app_main.py")],
    pathex=[str(VOICE_DIR), str(ENGINE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "IPython", "matplotlib", "scipy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ResponsaVoice",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON_PATH) if ICON_PATH.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ResponsaVoice",
)
