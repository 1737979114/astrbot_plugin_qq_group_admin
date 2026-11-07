# -*- coding: utf-8 -*-
"""分类词库加载与匹配。

词库来自参考插件导出的 ``lexicon.json``，结构::

    {"categories": {"ad": "广告推广"}, "keywords": {"ad": [...]}, "counts": {...}}

45623 条词逐条 ``in`` 判断太慢，这里按**首字**建索引：只检查消息里出现过的
首字对应的词，实际每次比较几十条，够快。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "ad": "广告",
    "porn": "色情",
    "violent_terror": "暴恐",
    "weapons": "涉枪涉爆",
    "corruption": "贪腐",
    "illegal_url": "违规网址",
    "political": "政治敏感",
    "reactionary": "反动",
    "livelihood": "民生敏感",
    "supplement": "补充词库",
    "tencent_ban": "腾讯封禁",
    "other": "其他",
}

#: 默认启用的分类：误杀率低的先开，政治/网址/民生默认不开
DEFAULT_CATEGORIES = (
    "ad",
    "porn",
    "violent_terror",
    "weapons",
    "tencent_ban",
    "other",
    "supplement",
)


class Lexicon:
    """分类词库。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.categories: dict[str, str] = {}
        self.words: dict[str, list[str]] = {}
        self.counts: dict[str, int] = {}
        self.enabled: set[str] = set()
        self._index: dict[str, list[tuple[str, str]]] = {}

    # ---------------------------------------------------------------- 加载

    def load(self) -> bool:
        if not self.path.is_file():
            logger.debug(f"[qqadmin] 词库文件不存在：{self.path}")
            return False

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[qqadmin] 词库读取失败：{exc}")
            return False

        self.categories = data.get("categories") or {}
        self.words = {
            str(key): [
                str(word)
                for word in value
                if str(word).strip() and not str(word).startswith("#")
            ]
            for key, value in (data.get("keywords") or {}).items()
            if isinstance(value, list)
        }
        self.counts = {
            str(key): int(value) for key, value in (data.get("counts") or {}).items()
        }
        return True

    def set_enabled(self, names) -> None:
        wanted = [str(name) for name in names or ()]
        self.enabled = {name for name in wanted if name in self.words}

        index: dict[str, list[tuple[str, str]]] = {}
        for category in self.enabled:
            for word in self.words.get(category, ()):
                if not word:
                    continue
                index.setdefault(word[0], []).append((word, category))
        self._index = index

    # ---------------------------------------------------------------- 匹配

    def match(self, text: str) -> tuple[str, str] | None:
        if not text or not self._index:
            return None

        for char in set(text):
            for word, category in self._index.get(char, ()):
                if word in text:
                    return word, category
        return None

    @staticmethod
    def label(category: str) -> str:
        return CATEGORY_LABELS.get(category, category)

    @property
    def total_enabled(self) -> int:
        return sum(len(self.words.get(name, ())) for name in self.enabled)

    def stats(self) -> list[dict]:
        return [
            {
                "name": name,
                "label": self.label(name),
                "count": len(words),
                "enabled": name in self.enabled,
            }
            for name, words in sorted(
                self.words.items(), key=lambda item: len(item[1]), reverse=True
            )
        ]
