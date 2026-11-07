# -*- coding: utf-8 -*-
"""群管的纯逻辑：时长解析、消息缓存、结果格式化。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

#: 禁言时长表达式
DURATION_RE = re.compile(
    r"(\d{1,4})\s*(秒|s|分钟|分钟|min|m|小时|h|天|d)", re.IGNORECASE
)

UNIT_SECONDS = {
    "秒": 1,
    "s": 1,
    "分钟": 60,
    "min": 60,
    "m": 60,
    "小时": 3600,
    "h": 3600,
    "天": 86400,
    "d": 86400,
}

#: 官方上限 30 天
MAX_MUTE_SECONDS = 30 * 86400
MIN_MUTE_SECONDS = 60

#: 一次最多撤回几条
MAX_RECALL = 10

#: 预置规则词库（分类 -> 词）
DEFAULT_RULES: dict[str, list[str]] = {
    "广告引流": [
        "加微信",
        "加vx",
        "加VX",
        "加V信",
        "私聊我",
        "招代理",
        "刷单",
        "兼职日结",
        "推广赚钱",
        "引流",
        "返利群",
        "优惠券群",
        "扫码进群",
        "免费领取",
        "日入过千",
    ],
    "诈骗": [
        "安全账户",
        "验证码发我",
        "解冻费",
        "中奖了",
        "点击链接领取",
        "转账截图",
        "银行卡号发我",
    ],
    "辱骂": ["傻逼", "草泥马", "尼玛", "滚蛋", "废物", "智障"],
    "低俗": ["约炮", "裸聊", "福利视频"],
}

#: 词 -> 分类
RULE_CATEGORY: dict[str, str] = {
    word: category for category, words in DEFAULT_RULES.items() for word in words
}

ALL_DEFAULT_RULES: list[str] = [
    word for words in DEFAULT_RULES.values() for word in words
]


@dataclass
class CachedMessage:
    message_id: str
    group: str
    sender: str
    name: str
    text: str
    ts: float = 0.0


class MessageCache:
    """按群缓存最近收到的消息，用于「撤回」定位目标。"""

    def __init__(self, limit: int = 30, ttl: int = 600) -> None:
        self.limit = max(1, limit)
        self.ttl = max(1, ttl)
        self._data: dict[str, list[CachedMessage]] = {}

    def add(self, message: CachedMessage) -> None:
        if not message.message_id:
            return
        if not message.ts:
            message.ts = time.time()

        bucket = self._data.setdefault(message.group, [])
        if bucket and bucket[-1].message_id == message.message_id:
            return
        bucket.append(message)
        if len(bucket) > self.limit:
            del bucket[: len(bucket) - self.limit]

    def recent(
        self, group: str, count: int = 1, exclude_id: str = ""
    ) -> list[CachedMessage]:
        now = time.time()
        bucket = [
            item
            for item in self._data.get(group, [])
            if now - item.ts <= self.ttl and item.message_id != exclude_id
        ]
        if count <= 0:
            return []
        return bucket[-count:]

    def clear_group(self, group: str) -> None:
        self._data.pop(group, None)


def parse_duration(text: str, default_seconds: int = 600) -> int:
    """把「10 分钟」「2h」「600」解析成秒。

    纯数字按分钟算（更符合群聊直觉），结果夹在 1 分钟到 30 天之间。
    """
    raw = text or ""
    match = DURATION_RE.search(raw)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        seconds = value * UNIT_SECONDS.get(unit, 60)
    else:
        number = re.search(r"(\d{1,4})", raw)
        seconds = int(number.group(1)) * 60 if number else default_seconds

    return max(MIN_MUTE_SECONDS, min(seconds, MAX_MUTE_SECONDS))


def parse_recall_count(text: str) -> int:
    match = re.search(r"(\d{1,2})", text or "")
    if not match:
        return 1
    return max(1, min(int(match.group(1)), MAX_RECALL))


def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400} 天"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def format_usage() -> str:
    return (
        "QQ 官方群管用法：\n"
        "禁言 @某人 10分钟\n"
        "解除禁言 @某人\n"
        "踢人 @某人\n"
        "撤回 / 撤回 3\n"
        "禁言状态\n"
        "全员禁言 / 解除全员禁言\n"
        "入群申请 / 同意 1 / 拒绝 1 / 同意 全部\n"
        "拉黑 @某人 / 解除拉黑 @某人\n"
        "初始化管理员（首次使用，把自己加进白名单）\n"
        "我的id（查自己的 openid，用来配管理员名单）"
    )


def format_mute_ok(name: str, seconds: int) -> str:
    who = name or "该成员"
    return f"已禁言 {who}，时长 {format_duration(seconds)}。"


def format_unmute_ok(name: str) -> str:
    return f"已解除 {name or '该成员'} 的禁言。"


def format_kick_ok(name: str) -> str:
    return f"已移出群聊：{name or '该成员'}。"


def format_recall_ok(count: int) -> str:
    return f"已撤回 {count} 条消息。"


def format_status(data: dict) -> str:
    global_rule = data.get("global_rule") or {}
    mode = str(global_rule.get("mode") or "none")
    mode_text = {"none": "未开启", "always": "已开启", "schedule": "定时禁言中"}.get(
        mode, mode
    )

    members = data.get("members") or []
    lines = ["群禁言状态", "", f"全员禁言：{mode_text}", f"正在禁言：{len(members)} 人"]
    for index, item in enumerate(members[:10], 1):
        if not isinstance(item, dict):
            continue
        openid = str(item.get("member_openid") or "")
        expire = str(item.get("mute_expire_at") or "")
        lines.append(f"{index}. {openid[:8]}… 到 {expire or '未设置'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 群信息 / 成员 / 黑名单 / 入群申请
# --------------------------------------------------------------------------

ROLE_LABEL = {"owner": "群主", "admin": "管理员", "member": "成员"}


def format_group_info(data: dict) -> str:
    if not isinstance(data, dict) or not data:
        return "没有拿到群信息。"

    mapping = (
        ("group_name", "群名称"),
        ("group_openid", "群 openid"),
        ("member_count", "成员数"),
        ("max_member_count", "人数上限"),
        ("create_time", "创建时间"),
    )
    lines = ["群信息", ""]
    for key, label in mapping:
        value = data.get(key)
        if value not in (None, ""):
            lines.append(f"{label}：{value}")

    if len(lines) == 2:
        for key, value in list(data.items())[:8]:
            lines.append(f"{key}：{value}")
    return "\n".join(lines)


def format_members(data: dict, limit: int = 20) -> str:
    members = (data or {}).get("members") or []
    if not members:
        return "没有拿到成员列表（可能需要平台开通「群成员列表」权限）。"

    lines = [f"群成员（本页 {len(members)} 人）", ""]
    for index, item in enumerate(members[:limit], 1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("username") or "")
        openid = str(item.get("member_openid") or "")
        role = ROLE_LABEL.get(str(item.get("member_role") or ""), "成员")
        flag = " 🤖" if item.get("bot") else ""
        lines.append(f"{index}. {name or openid[:8]}{flag}  [{role}]")

    if (data or {}).get("next_cursor"):
        lines.append("")
        lines.append("还有更多成员，这里只显示第一页（最多 30 人）。")
    return "\n".join(lines)


def format_member_detail(data: dict) -> str:
    if not isinstance(data, dict) or not data:
        return "没有拿到成员信息。"
    lines = ["成员信息", ""]
    lines.append(f"昵称：{data.get('username') or '-'}")
    lines.append(f"角色：{ROLE_LABEL.get(str(data.get('member_role') or ''), '成员')}")
    lines.append(f"openid：{data.get('member_openid') or '-'}")
    if data.get("joined_at"):
        lines.append(f"入群时间：{data.get('joined_at')}")
    if data.get("bot"):
        lines.append("身份：机器人")
    return "\n".join(lines)


def format_blacklist(data: dict) -> str:
    items = (data or {}).get("blacklist") or (data or {}).get("members") or []
    if not items:
        return "群黑名单是空的。"

    lines = [f"群黑名单（本页 {len(items)} 人）", ""]
    for index, item in enumerate(items[:20], 1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("username") or "")
        openid = str(item.get("member_openid") or "")
        lines.append(f"{index}. {name or openid[:10]}")
    return "\n".join(lines)


def format_join_requests(data: dict) -> str:
    items = (data or {}).get("join_requests") or (data or {}).get("requests") or []
    if not items:
        return "没有待审批的入群申请。"

    lines = [f"入群申请（{len(items)} 条）", ""]
    for index, item in enumerate(items[:10], 1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("username") or item.get("member_openid") or "")
        answer = str(item.get("apply_comment") or item.get("comment") or "")
        lines.append(f"{index}. {name}" + (f"：{answer[:30]}" if answer else ""))
    lines.append("")
    lines.append("审批接口需要群管理员权限，可在管理页操作。")
    return "\n".join(lines)
