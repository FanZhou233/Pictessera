"""Local-first image content analysis providers.

The application never downloads a model implicitly.  A provider is enabled only
when the user points it at an already installed local model directory.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ContentPrediction:
    label: str
    confidence: float
    source: str = "local-model"


class ContentAnalysisProvider(Protocol):
    name: str

    def analyze(self, image_path: Path) -> Sequence[ContentPrediction]: ...


class SemanticEmbeddingProvider(Protocol):
    name: str

    def embed_image(self, image_path: Path) -> Sequence[float]: ...
    def embed_text(self, text: str) -> Sequence[float]: ...


class TransformersImageClassifierProvider:
    """Image-classification provider backed by a local Hugging Face model."""

    def __init__(self, model_directory: Path | str, *, threshold: float = 0.25, top_k: int = 8):
        self.model_directory = Path(model_directory)
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self.top_k = max(1, int(top_k))
        self.name = self.model_directory.name or "transformers-local"
        self._pipeline = None

    @property
    def cache_key(self) -> str:
        parts = [str(self.model_directory.resolve())]
        for name in ("config.json", "model.safetensors", "pytorch_model.bin"):
            path = self.model_directory / name
            try:
                stat = path.stat()
                parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
            except OSError:
                parts.append(f"{name}:missing")
        return "|".join(parts)

    def _load(self):
        if self._pipeline is None:
            if not self.model_directory.is_dir():
                raise FileNotFoundError(f"本地内容识别模型不存在：{self.model_directory}")
            from transformers import AutoImageProcessor, AutoModelForImageClassification, pipeline

            processor = AutoImageProcessor.from_pretrained(str(self.model_directory), local_files_only=True)
            model = AutoModelForImageClassification.from_pretrained(str(self.model_directory), local_files_only=True)
            self._pipeline = pipeline("image-classification", model=model, image_processor=processor)
        return self._pipeline

    def analyze(self, image_path: Path) -> list[ContentPrediction]:
        results = self._load()(str(image_path), top_k=self.top_k)
        predictions = []
        for result in results or []:
            label = str(result.get("label", "")).strip()
            confidence = float(result.get("score", 0.0) or 0.0)
            if label and confidence >= self.threshold:
                predictions.append(ContentPrediction(label, confidence, self.name))
        return predictions


class StaticContentProvider:
    """Small deterministic provider useful for plugins, tests and manual imports."""

    name = "static"

    def __init__(self, predictions: Sequence[ContentPrediction]):
        self.predictions = list(predictions)

    def analyze(self, image_path: Path) -> list[ContentPrediction]:
        return list(self.predictions)


class LocalCLIPProvider:
    """CLIP embeddings loaded exclusively from a user-selected local directory."""

    def __init__(self, model_directory: Path | str):
        self.model_directory = Path(model_directory)
        self.name = self.model_directory.name or "clip-local"
        self._model = None
        self._processor = None

    def _load(self):
        if self._model is None:
            if not self.model_directory.is_dir():
                raise FileNotFoundError(f"本地语义模型不存在：{self.model_directory}")
            from transformers import AutoProcessor, CLIPModel

            self._processor = AutoProcessor.from_pretrained(str(self.model_directory), local_files_only=True)
            self._model = CLIPModel.from_pretrained(str(self.model_directory), local_files_only=True)
            self._model.eval()
        return self._model, self._processor

    @staticmethod
    def _values(tensor) -> list[float]:
        values = tensor.detach().cpu().float().reshape(-1).tolist()
        norm = math.sqrt(sum(float(value) ** 2 for value in values)) or 1.0
        return [float(value) / norm for value in values]

    def embed_image(self, image_path: Path) -> list[float]:
        from PIL import Image
        import torch

        model, processor = self._load()
        with Image.open(image_path) as image, torch.inference_mode():
            inputs = processor(images=image.convert("RGB"), return_tensors="pt")
            return self._values(model.get_image_features(**inputs))

    def embed_text(self, text: str) -> list[float]:
        import torch

        model, processor = self._load()
        with torch.inference_mode():
            inputs = processor(text=[str(text)], return_tensors="pt", padding=True)
            return self._values(model.get_text_features(**inputs))


class SemanticVectorIndex:
    """Small local cosine index with versioned, atomic JSON persistence."""

    VERSION = 1

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.model = ""
        self.vectors: dict[str, list[float]] = {}

    def upsert(self, stable_key: str, vector: Sequence[float]) -> None:
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        self.vectors[str(stable_key)] = [value / norm for value in values]

    def remove(self, stable_key: str) -> None:
        self.vectors.pop(str(stable_key), None)

    def search(self, query_vector: Sequence[float], *, limit: int = 100, minimum: float = -1.0):
        query = [float(value) for value in query_vector]
        norm = math.sqrt(sum(value * value for value in query)) or 1.0
        query = [value / norm for value in query]
        scored = []
        for key, vector in self.vectors.items():
            if len(vector) != len(query):
                continue
            score = sum(left * right for left, right in zip(vector, query))
            if score >= minimum:
                scored.append((key, score))
        return sorted(scored, key=lambda item: (-item[1], item[0]))[: max(0, int(limit))]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {"version": self.VERSION, "model": self.model, "vectors": self.vectors}
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self) -> bool:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if int(document.get("version", 0)) != self.VERSION:
                return False
            self.model = str(document.get("model", ""))
            self.vectors = {
                str(key): [float(value) for value in vector]
                for key, vector in dict(document.get("vectors", {})).items()
            }
            return True
        except Exception:
            self.vectors = {}
            return False


def normalize_content_label(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def reliable_labels(
    predictions: Sequence[ContentPrediction],
    *,
    threshold: float = 0.35,
) -> list[str]:
    """Deduplicate labels while preserving confidence order."""

    best: dict[str, tuple[str, float]] = {}
    for prediction in predictions:
        label = normalize_content_label(prediction.label)
        confidence = float(prediction.confidence)
        if not label or confidence < threshold:
            continue
        key = label.casefold()
        if key not in best or confidence > best[key][1]:
            best[key] = (label, confidence)
    return [label for label, _score in sorted(best.values(), key=lambda item: (-item[1], item[0].casefold()))]
