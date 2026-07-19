"""拍摄时间分类规则。"""

from __future__ import annotations

from datetime import datetime, timedelta

from photo_manager.domain import AutoCategory, CategoryType, PhotoItem


class TimeRule:
    rule_version = 1
    root_id = "time"

    def base_categories(self) -> list[AutoCategory]:
        return [
            AutoCategory(self.root_id, CategoryType.TIME, "时间", sort_key="10"),
            AutoCategory(
                "time:recent:7d",
                CategoryType.TIME,
                "最近 7 天",
                self.root_id,
                "10",
                self.rule_version,
            ),
            AutoCategory(
                "time:recent:30d",
                CategoryType.TIME,
                "最近 30 天",
                self.root_id,
                "20",
                self.rule_version,
            ),
            AutoCategory(
                "time:unknown",
                CategoryType.TIME,
                "时间未知",
                self.root_id,
                "99",
                self.rule_version,
            ),
        ]

    def classify(self, item: PhotoItem, now: datetime) -> list[AutoCategory]:
        shot_time = item.shot_time
        if shot_time == datetime.min:
            return [
                AutoCategory(
                    "time:unknown",
                    CategoryType.TIME,
                    "时间未知",
                    self.root_id,
                    "99",
                    self.rule_version,
                )
            ]

        year_id = f"time:year:{shot_time.year:04d}"
        month_id = f"time:month:{shot_time.year:04d}-{shot_time.month:02d}"
        categories = [
            AutoCategory(
                year_id,
                CategoryType.TIME,
                f"{shot_time.year:04d} 年",
                self.root_id,
                f"30:{9999 - shot_time.year:04d}",
                self.rule_version,
            ),
            AutoCategory(
                month_id,
                CategoryType.TIME,
                f"{shot_time.year:04d} 年 {shot_time.month:02d} 月",
                year_id,
                f"{shot_time.month:02d}",
                self.rule_version,
            ),
        ]
        age = now - shot_time
        if timedelta(0) <= age <= timedelta(days=7):
            categories.append(
                AutoCategory(
                    "time:recent:7d",
                    CategoryType.TIME,
                    "最近 7 天",
                    self.root_id,
                    "10",
                    self.rule_version,
                )
            )
        if timedelta(0) <= age <= timedelta(days=30):
            categories.append(
                AutoCategory(
                    "time:recent:30d",
                    CategoryType.TIME,
                    "最近 30 天",
                    self.root_id,
                    "20",
                    self.rule_version,
                )
            )
        return categories
