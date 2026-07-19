"""GPS 位置分类规则。"""

from __future__ import annotations

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem


class LocationRule:
    rule_version = 1
    root_id = "location"

    def base_categories(self) -> list[AutoCategory]:
        return [
            AutoCategory(self.root_id, CategoryType.LOCATION, "位置", sort_key="50"),
            AutoCategory("location:with-gps", CategoryType.LOCATION, "有位置信息", self.root_id, "10"),
            AutoCategory("location:without-gps", CategoryType.LOCATION, "无位置信息", self.root_id, "20"),
        ]

    def classify(self, item: PhotoItem, now) -> list[AutoCategory]:
        del now
        lat, lon = item.gps_latitude, item.gps_longitude
        if lat is None or lon is None:
            return [
                AutoCategory(
                    "location:without-gps",
                    CategoryType.LOCATION,
                    "无位置信息",
                    self.root_id,
                    "20",
                )
            ]
        lat_group = round(float(lat), 1)
        lon_group = round(float(lon), 1)
        return [
            AutoCategory("location:with-gps", CategoryType.LOCATION, "有位置信息", self.root_id, "10"),
            AutoCategory(
                f"location:grid:{lat_group:+.1f}:{lon_group:+.1f}",
                CategoryType.LOCATION,
                f"{lat_group:.1f}, {lon_group:.1f}",
                "location:with-gps",
                f"{lat_group:+07.1f}:{lon_group:+08.1f}",
            ),
        ]
