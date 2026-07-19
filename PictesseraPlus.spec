# -*- mode: python ; coding: utf-8 -*-

import sys

from PyInstaller.utils.hooks import collect_data_files


# PyInstaller recursively walks the large OpenCV/NumPy/ImageIO graph in the
# user's Conda base environment. The default limit of 1000 is too small for
# that analysis even though the application itself has no recursive call bug.
sys.setrecursionlimit(sys.getrecursionlimit() * 5)


# Plus edition: bundle the preferred HEVC-capable ffmpeg backend as well as the
# OpenCV/ImageIO fallback chain. The lightweight Pictessera.spec stays small.
ffmpeg_data = collect_data_files("imageio_ffmpeg", includes=["binaries/*"])

a = Analysis(
    ["main.py"],
    pathex=[],
    # PyInstaller's official cv2 hook collects the required OpenCV binaries
    # when the hidden import below is analyzed.
    binaries=[],
    datas=[
        ("assets/photo_manager_icon.ico", "assets"),
        ("assets/icons", "assets/icons"),
        *ffmpeg_data,
    ],
    hiddenimports=[
        "cv2",
        "numpy",
        "imageio",
        "imageio.v3",
        "imageio_ffmpeg",
        "imageio_ffmpeg.binaries",
        "pillow_heif",
        "_pillow_heif",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "tkinter",
        "IPython",
        "jupyter",
        "pytest",
        # Optional plugin ecosystems discovered from the broad Conda base
        # environment. None are used by this desktop application or its video
        # decoding chain, and excluding them keeps the module graph finite.
        "astropy",
        "matplotlib",
        "pandas",
        "scipy",
        "lxml",
        "pygame",
        "notebook",
        "sphinx",
        "PIL.ImageTk",
        # Plus adds media decoding only. Local AI classification remains an
        # optional source-install feature and must not pull in Torch,
        # Transformers, or their large Conda dependency graph for releases.
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "tensorflow",
        "tensorflow_intel",
        "keras",
        "huggingface_hub",
        "safetensors",
        "tokenizers",
        "accelerate",
        "onnx",
        "onnxruntime",
        "open_clip",
        "sentence_transformers",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Pictessera-Plus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets/photo_manager_icon.ico"],
)
