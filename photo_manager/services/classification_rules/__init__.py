"""P0 自动分类规则。"""

from .device_rule import DeviceRule
from .file_rule import FileRule
from .location_rule import LocationRule
from .media_rule import MediaRule
from .plus_rule import PlusAIRule
from .source_rule import SourceRule
from .time_rule import TimeRule

__all__ = [
    "DeviceRule",
    "FileRule",
    "LocationRule",
    "MediaRule",
    "PlusAIRule",
    "SourceRule",
    "TimeRule",
]
