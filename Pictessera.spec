# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/photo_manager_icon.ico", "assets"),
        ("assets/icons", "assets/icons"),
    ],
    hiddenimports=[
        # Pillow discovers HEIF support dynamically. Explicit imports ensure the
        # wheel's native extension is collected on clean Windows machines.
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
        "matplotlib",
        "IPython",
        "jupyter",
        "astropy",
        "pytest",
        # Optional fallback decoders are intentionally excluded from the
        # lightweight distribution. LIVE preview remains available when ffmpeg
        # is installed on the target machine; photo management remains complete.
        "cv2",
        "numpy",
        "imageio",
        "scipy",
        "pandas",
        "lxml",
        "pygame",
        "PIL.ImageTk",
        # AI/content-recognition dependencies belong to the Plus edition only.
        # PyInstaller can discover imports inside lazy provider methods, so they
        # must be excluded explicitly. The external models/ directory is not
        # included in datas either.
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
    name="Pictessera",
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
