"""拍摄设备分类规则。"""

from __future__ import annotations

import hashlib

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem


def _stable_part(value: str) -> str:
    return hashlib.sha1(value.strip().casefold().encode("utf-8")).hexdigest()[:12]


class DeviceRule:
    rule_version = 1
    root_id = "device"

    def base_categories(self) -> list[AutoCategory]:
        return [
            AutoCategory(self.root_id, CategoryType.DEVICE, "拍摄设备", sort_key="40"),
            AutoCategory("device:unknown", CategoryType.DEVICE, "未知设备", self.root_id, "99"),
        ]

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        make = (item.camera_make or "").strip()
        model = (item.camera_model or "").strip()
        if not make and not model:
            return [AutoCategory("device:unknown", CategoryType.DEVICE, "未知设备", self.root_id, "99")]
        categories: list[AutoCategory] = []
        parent_id = self.root_id
        if make:
            parent_id = f"device:make:{_stable_part(make)}"
            categories.append(
                AutoCategory(parent_id, CategoryType.DEVICE, make, self.root_id, f"10:{make.casefold()}")
            )
        if model:
            label = model if not make or model.casefold().startswith(make.casefold()) else f"{make} {model}"
            categories.append(
                AutoCategory(
                    f"device:model:{_stable_part(make + '|' + model)}",
                    CategoryType.DEVICE,
                    label,
                    parent_id,
                    f"20:{model.casefold()}",
                )
            )
        return categories
