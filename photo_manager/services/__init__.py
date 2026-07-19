"""Reusable application services."""

from .classification import ClassificationService, ClassificationSnapshot
from .i18n import TranslationService
from .plus_analysis import PlusFeatureAnalyzer, clone_items_for_classification
from .content_analysis import (
    ContentAnalysisProvider,
    ContentPrediction,
    LocalCLIPProvider,
    SemanticEmbeddingProvider,
    SemanticVectorIndex,
    StaticContentProvider,
    TransformersImageClassifierProvider,
    reliable_labels,
)
from .settings import SettingsService, detect_lightroom_path, windows_apps_use_light_theme
from .search import searchable_fields_for_item, wildcard_query_matches

__all__ = [
    "ClassificationService",
    "ClassificationSnapshot",
    "PlusFeatureAnalyzer",
    "ContentAnalysisProvider",
    "ContentPrediction",
    "LocalCLIPProvider",
    "SemanticEmbeddingProvider",
    "SemanticVectorIndex",
    "StaticContentProvider",
    "TransformersImageClassifierProvider",
    "reliable_labels",
    "SettingsService",
    "TranslationService",
    "detect_lightroom_path",
    "windows_apps_use_light_theme",
    "clone_items_for_classification",
    "searchable_fields_for_item",
    "wildcard_query_matches",
]
