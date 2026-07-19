# Lightweight Windows build

Build the single-file application from the active environment:

```powershell
python -m PyInstaller --clean --noconfirm .\Pictessera.spec
```

Output:

```text
dist/Pictessera.exe
```

The lightweight spec intentionally excludes OpenCV, NumPy, SciPy, Pandas,
ImageIO, PyTorch, Transformers, TensorFlow, Hugging Face, ONNX and related AI
runtimes. The external `models/` directory is not bundled. This keeps the
executable small while preserving photo scanning, HEIC/JPEG/PNG thumbnails,
EXIF metadata, manual tags, cached-label search, selection, moving, deletion
and Live Photo pairing.

Live Photo video preview uses a system `ffmpeg` executable when available.
Without ffmpeg, the still image and Live Photo file-management features continue
to work, but animated preview is unavailable.

## Plus edition

The Plus build bundles ffmpeg, OpenCV, NumPy and ImageIO for full Live Photo
preview support and may include the optional local AI/content-recognition
runtime:

```powershell
python -m PyInstaller --clean --noconfirm .\PictesseraPlus.spec
```

Output:

```text
dist/Pictessera-Plus.exe
```
