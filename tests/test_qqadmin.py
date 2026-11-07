# -*- coding: utf-8 -*-
"""QQ 官方群管插件的离线测试。"""

import asyncio
import importlib.util
import json
import logging
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

from admin_core import (  # noqa: E402
    CachedMessage,
    MessageCache,
    format_duration,
    format_status,
    parse_duration,
    parse_recall_count,
)
from official_api import ApiResult, ERROR_HINTS  # noqa: E402
from roster import RosterStore  # noqa: E402


def _ensure_astrbot() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("fake.astrbot")
    api.AstrBotConfig = dict

    event_mod = types.ModuleType("astrbot.api.event")

    class _EventMessageType:
        ALL = "all"

    class _Filter:
        EventMessageType = _EventMessageType

        @staticmethod
        def command(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class AstrMessageEvent:
        pass

    class MessageEventResult:
        pass

    event_mod.filter = _Filter
    event_mod.EventMessageType = _EventMessageType
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageEventResult = MessageEventResult

    star_mod = types.ModuleType("astrbot.api.star")

    class Star:
        def __init__(self, context=None):
            self.context = context

    class Context:
        pass

    star_mod.Star = Star
    star_mod.Context = Context

    web_mod = types.ModuleType("astrbot.api.web")

    class _FakeResponse:
        def __init__(self, payload: dict, status_code: int = 200):
            self.body = json.dumps(payload, ensure_ascii=False)
            self.status_code = status_code

    def _json_response(data=None, *, status_code=200, headers=None):
        return _FakeResponse({} if data is None else data, status_code)

    def _error_response(message, *, status_code=400, data=None, headers=None):
        return _FakeResponse(
            {"status": "error", "message": message, "data": data}, status_code
        )

    web_mod.json_response = _json_response
    web_mod.error_response = _error_response
    web_mod.request = SimpleNamespace(query={}, json=None)

    api.event = event_mod
    api.star = star_mod
    api.web = web_mod
    astrbot.api = api

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.star"] = star_mod
    sys.modules["astrbot.api.web"] = web_mod


_ensure_astrbot()

_spec = importlib.util.spec_from_file_location(
    "qqadmin_plugin_under_test", PLUGIN_DIR / "main.py"
)
plugin_main = importlib.util.module_from_spec(_spec)
sys.modules["qqadmin_plugin_under_test"] = plugin_main
_spec.loader.exec_module(plugin_main)


class FakeAPI:
    def __init__(self, ready=True, result=None, role="owner", role_denied=False):
        self.ready = ready
        self.result = result or ApiResult(ok=True)
        self.role = role
        self.role_denied = role_denied
        self.calls = []

    async def member_info(self, group_openid, member_openid):
        self.calls.append(("member_info", group_openid, member_openid))
        if self.role_denied:
            return ApiResult(
                ok=False, status=400, error_code="11253", message="应用无接口访问权限"
            )
        return ApiResult(ok=True, data={"member": {"role": self.role}})

    async def mute_member(self, group, member, seconds):
        self.calls.append(("mute", group, member, seconds))
        return self.result

    async def unmute_member(self, group, member):
        self.calls.append(("unmute", group, member))
        return self.result

    async def remove_members(self, group, members, blacklist=False):
        self.calls.append(("kick", group, tuple(members)))
        return self.result

    async def recall_message(self, group, message_id):
        self.calls.append(("recall", group, message_id))
        return self.result

    async def join_requests(self, group, cursor=""):
        self.calls.append(("join_requests", group))
        return ApiResult(
            ok=True,
            data={
                "join_requests": [
                    {"member_openid": "J1", "username": "甲"},
                    {"member_openid": "J2", "username": "乙", "apply_comment": "朋友介绍"},
                ]
            },
        )

    async def approve_join(self, group, member_openid, approve=True):
        self.calls.append(("approve_join", group, member_openid, approve))
        return self.result

    async def blacklist_op(self, group, members, op="add"):
        self.calls.append(("blacklist_op", group, tuple(members), op))
        return self.result

    async def query_restrict(self, group):
        self.calls.append(("status", group))
        return ApiResult(ok=True, data={"global_rule": {"mode": "none"}, "members": []})

    async def set_global_mute(self, group, enable):
        self.calls.append(("global", group, enable))
        return self.result

    async def group_info(self, group):
        self.calls.append(("group_info", group))
        return ApiResult(ok=True, data={"group_name": "测试群", "group_member_num": 12})

    async def close(self):
        pass


class FakeEvent:
    def __init__(
        self,
        text,
        *,
        platform="qq_official",
        sender="MEMBER_1",
        group="GROUP_1",
        message_id="MSG_1",
        mentions=None,
        name="小明",
    ):
        raw = SimpleNamespace(
            id=message_id,
            mentions=mentions or [],
        )
        self.message_str = text
        self.message_obj = SimpleNamespace(
            raw_message=raw,
            self_id="BOT_1",
            message=[],
        )
        self.platform_name = platform
        self._sender = sender
        self._group = group
        self._name = name
        self._extra = {}
        self.stopped = False

    def get_platform_name(self):
        return self.platform_name

    def get_group_id(self):
        return self._group

    def get_sender_id(self):
        return self._sender

    def get_sender_name(self):
        return self._name

    def get_extra(self, key, default=None):
        return self._extra.get(key, default)

    def set_extra(self, key, value):
        self._extra[key] = value

    def plain_result(self, text):
        return ("plain", text)

    def stop_event(self):
        self.stopped = True


async def collect_dispatch(plugin, event):
    return [item async for item in plugin._dispatch(event)]


def make_plugin(api=None, **overrides):
    config = {
        "enable": True,
        "app_id": "app",
        "app_secret": "secret",
        "admin_openids": [],
        "cache_messages": True,
        "cache_size": 30,
        "cache_ttl": 600,
        "timeout": 10,
        "auto_unmute": False,
    }
    config.update(overrides)
    plugin = plugin_main.QQGroupAdminPlugin(context=None, config=config)
    plugin.api = api or FakeAPI()

    # 测试不读写真实状态文件，避免用例之间互相污染
    plugin._state_path = lambda: Path(tempfile.mkdtemp()) / "state.json"
    plugin.roster.close()
    plugin.roster = RosterStore(str(Path(tempfile.mkdtemp()) / "roster.db"))
    plugin._roster_touched.clear()
    plugin.groups.clear()
    plugin.daily.clear()
    plugin.records.clear()
    plugin._mutes.clear()
    return plugin


def mention(openid, name="小红", is_you=False):
    return SimpleNamespace(id=openid, username=name, is_you=is_you)


class TestDuration(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(parse_duration("禁言 @某人 10分钟"), 600)

    def test_hours(self):
        self.assertEqual(parse_duration("2小时"), 7200)

    def test_seconds(self):
        self.assertEqual(parse_duration("30秒"), 60)  # 低于 1 分钟按 1 分钟算

    def test_plain_number_is_minutes(self):
        self.assertEqual(parse_duration("30"), 1800)

    def test_default(self):
        self.assertEqual(parse_duration("", default_seconds=600), 600)

    def test_cap_30_days(self):
        self.assertEqual(parse_duration("999天"), 30 * 86400)

    def test_format(self):
        self.assertEqual(format_duration(600), "10 分钟")
        self.assertEqual(format_duration(7200), "2 小时")
        self.assertEqual(format_duration(86400), "1 天")

    def test_recall_count(self):
        self.assertEqual(parse_recall_count("撤回"), 1)
        self.assertEqual(parse_recall_count("撤回 3"), 3)
        self.assertEqual(parse_recall_count("撤回 99"), 10)


class TestCache(unittest.TestCase):
    def test_add_and_recent(self):
        cache = MessageCache(limit=5, ttl=60)
        for index in range(3):
            cache.add(
                CachedMessage(
                    message_id=f"m{index}", group="g", sender="u", name="n", text="t"
                )
            )
        self.assertEqual(len(cache.recent("g", 2)), 2)

    def test_exclude_current(self):
        cache = MessageCache(limit=5, ttl=60)
        cache.add(
            CachedMessage(message_id="m1", group="g", sender="u", name="n", text="t")
        )
        cache.add(
            CachedMessage(message_id="m2", group="g", sender="u", name="n", text="t")
        )
        items = cache.recent("g", 1, exclude_id="m2")
        self.assertEqual(items[0].message_id, "m1")

    def test_ttl(self):
        cache = MessageCache(limit=5, ttl=1)
        cache.add(
            CachedMessage(
                message_id="m1",
                group="g",
                sender="u",
                name="n",
                text="t",
                ts=time.time() - 10,
            )
        )
        self.assertEqual(cache.recent("g", 1), [])

    def test_limit(self):
        cache = MessageCache(limit=2, ttl=60)
        for index in range(5):
            cache.add(
                CachedMessage(
                    message_id=f"m{index}", group="g", sender="u", name="n", text="t"
                )
            )
        self.assertEqual(len(cache._data["g"]), 2)


class TestRoster(unittest.TestCase):
    """官方拿不到成员列表，靠「谁说过话」攒名册。"""

    def test_touch_and_page(self):
        store = RosterStore(str(Path(tempfile.mkdtemp()) / "roster.db"))
        now = time.time()
        store.touch("g", "U1", "玄鱼", now - 60)
        store.touch("g", "U2", "小北", now - 30)
        store.touch("g", "U1", "玄鱼改名", now)

        rows, total = store.page("g")
        self.assertEqual(total, 2)
        self.assertEqual([r["openid"] for r in rows], ["U1", "U2"])
        self.assertEqual(rows[0]["name"], "玄鱼改名", "昵称以最新一次为准")
        store.close()

    def test_keyword_and_scope(self):
        store = RosterStore(str(Path(tempfile.mkdtemp()) / "roster.db"))
        store.touch("g", "AAAA1111", "玄鱼")
        store.touch("g", "BBBB2222", "小北")
        store.touch("other", "CCCC3333", "别人")

        rows, total = store.page("g", keyword="玄")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["openid"], "AAAA1111")

        rows, total = store.page("g", keyword="BBBB")
        self.assertEqual(total, 1)
        self.assertEqual(store.count("g"), 2)
        self.assertEqual(store.count("other"), 1)
        store.close()

    def test_empty_name_keeps_old(self):
        store = RosterStore(str(Path(tempfile.mkdtemp()) / "roster.db"))
        store.touch("g", "U1", "玄鱼")
        store.touch("g", "U1", "")
        self.assertEqual(store.get("g", "U1")["name"], "玄鱼")
        store.close()

    def test_throttle(self):
        plugin = make_plugin()
        plugin._touch_roster("g", "U1", "玄鱼")
        plugin._touch_roster("g", "U1", "玄鱼")
        rows, total = plugin.roster.page("g")
        self.assertEqual(total, 1)
        self.assertGreaterEqual(plugin.roster.get("g", "U1")["last_seen"], 0)


class TestGroupList(unittest.TestCase):
    """官方没有群列表接口，只能从名册和历史里补。"""

    def test_merge_from_roster(self):
        plugin = make_plugin()
        plugin.groups.clear()
        gid = "F" * 31 + "1"
        plugin.roster.touch(gid, "U1", "玄鱼")
        added = plugin._merge_known_groups()
        self.assertEqual(added, 1)
        self.assertIn(gid, plugin.groups)

    def test_numeric_group_ignored(self):
        plugin = make_plugin()
        plugin.groups.clear()
        plugin.roster.touch("1090353201", "U1", "老王")
        self.assertEqual(plugin._merge_known_groups(), 0)
        self.assertNotIn("1090353201", plugin.groups)

    def test_state_keeps_member_count(self):
        plugin = make_plugin()
        state_file = Path(tempfile.mkdtemp()) / "state.json"
        plugin._state_path = lambda: state_file
        gid = "B" * 32
        plugin.groups[gid] = {
            "group_id": gid,
            "name": "测试群",
            "last_seen": 1.0,
            "blocked": 2,
            "member_count": 88,
        }
        plugin._save_state(force=True)
        reloaded = make_plugin()
        reloaded._state_path = lambda: state_file
        reloaded.groups.clear()
        reloaded._load_state()
        self.assertEqual(reloaded.groups[gid]["member_count"], 88)
        self.assertEqual(reloaded.groups[gid]["blocked"], 2)

    def test_fill_group_names(self):
        plugin = make_plugin(api=FakeAPI())
        gid = "A" * 32
        plugin.groups.clear()
        plugin.groups[gid] = {"group_id": gid, "name": "", "last_seen": 0, "blocked": 0}

        done = asyncio.run(plugin._fill_group_names(limit=5))
        self.assertEqual(done, 1)
        self.assertEqual(plugin.groups[gid]["name"], "测试群")
        self.assertEqual(plugin.groups[gid]["member_count"], 12)


class TestSeedTime(unittest.TestCase):
    def test_parse_db_time_is_utc(self):
        import calendar

        ts = plugin_main.QQGroupAdminPlugin._parse_db_time(
            "2026-11-01 06:36:20.784287"
        )
        expected = calendar.timegm((2026, 11, 1, 6, 36, 20, 0, 0, 0))
        self.assertAlmostEqual(ts, expected, delta=1)

    def test_parse_db_time_bad_value(self):
        self.assertEqual(plugin_main.QQGroupAdminPlugin._parse_db_time(""), 0.0)
        self.assertEqual(plugin_main.QQGroupAdminPlugin._parse_db_time(None), 0.0)


class TestMemberAction(unittest.TestCase):
    """面板上的禁言 / 解禁 / 踢人按钮。"""

    def test_mute_then_unmute(self):
        plugin = make_plugin(api=FakeAPI())
        result = asyncio.run(
            plugin._member_action(
                {"group": "g", "openid": "U1", "name": "玄鱼", "action": "mute", "seconds": 600}
            )
        )
        body = json.loads(result.body)
        self.assertTrue(body["ok"])
        self.assertIn("已禁言", body["data"]["message"])

        result = asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "unmute"})
        )
        body = json.loads(result.body)
        self.assertTrue(body["ok"])
        self.assertIn("解除", body["data"]["message"])

    def test_kick(self):
        plugin = make_plugin(api=FakeAPI())
        result = asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "kick"})
        )
        body = json.loads(result.body)
        self.assertTrue(body["ok"])

    def test_action_needs_credentials(self):
        plugin = make_plugin(api=FakeAPI(ready=False))
        result = asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "mute"})
        )
        body = json.loads(result.body)
        self.assertEqual(body["status"], "error")
        self.assertIn("AppID", body["message"])

    def test_grant_and_revoke(self):
        plugin = make_plugin(api=FakeAPI())
        asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "grant"})
        )
        self.assertIn("U1", plugin.group_admins.get("g", []))

        asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "revoke"})
        )
        self.assertNotIn("U1", plugin.group_admins.get("g", []))

    def test_unknown_action(self):
        plugin = make_plugin(api=FakeAPI())
        result = asyncio.run(
            plugin._member_action({"group": "g", "openid": "U1", "action": "explode"})
        )
        body = json.loads(result.body)
        self.assertEqual(body["status"], "error")


class TestStatus(unittest.TestCase):
    def test_format(self):
        text = format_status(
            {
                "global_rule": {"mode": "always"},
                "members": [{"member_openid": "A", "mute_expire_at": "2026-01-01"}],
            }
        )
        self.assertIn("已开启", text)
        self.assertIn("1 人", text)

    def test_error_hint(self):
        result = ApiResult(ok=False, error_code="40062003", status=403)
        self.assertIn("管理员", result.hint)
        self.assertIn("40062003", ERROR_HINTS)


class TestFlow(unittest.TestCase):
    def test_non_official_platform_rejected(self):
        plugin = make_plugin()
        out = asyncio.run(
            collect_dispatch(
                plugin, FakeEvent("禁言 @小明 10分钟", platform="aiocqhttp")
            )
        )
        self.assertIn("只支持 QQ 官方机器人", out[0][1])

    def test_missing_credentials(self):
        plugin = make_plugin(api=FakeAPI(ready=False))
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("禁言 @小明 10分钟")))
        self.assertIn("AppID", out[0][1])

    def test_whoami(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("我的id")))
        self.assertIn("MEMBER_1", out[0][1])

    def test_admin_restriction(self):
        plugin = make_plugin(api=FakeAPI(role="member"), admin_openids=["OTHER"])
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("禁言 @小明 10分钟")))
        self.assertIn("没有权限", out[0][1])

    def test_admin_allowed(self):
        api = FakeAPI()
        plugin = make_plugin(api=api, admin_openids=["MEMBER_1"])
        out = asyncio.run(
            collect_dispatch(
                plugin, FakeEvent("禁言 @小明 10分钟", mentions=[mention("TARGET_1")])
            )
        )
        self.assertIn("已禁言", out[0][1])
        self.assertEqual(api.calls[-1][0], "mute")
        self.assertEqual(api.calls[-1][2], "TARGET_1")
        self.assertEqual(api.calls[-1][3], 600)

    def test_mute_without_mention(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("禁言 10分钟")))
        self.assertIn("请 @", out[0][1])

    def test_mute_permission_error(self):
        api = FakeAPI(result=ApiResult(ok=False, error_code="40062003", status=403))
        plugin = make_plugin(api=api)
        out = asyncio.run(
            collect_dispatch(
                plugin, FakeEvent("禁言 @小明 10分钟", mentions=[mention("TARGET_1")])
            )
        )
        self.assertIn("管理员", out[0][1])

    def test_unmute(self):
        api = FakeAPI()
        plugin = make_plugin(api=api)
        out = asyncio.run(
            collect_dispatch(
                plugin, FakeEvent("解除禁言 @小明", mentions=[mention("TARGET_1")])
            )
        )
        self.assertIn("已解除", out[0][1])
        self.assertEqual(api.calls[-1][0], "unmute")

    def test_kick(self):
        api = FakeAPI()
        plugin = make_plugin(api=api)
        asyncio.run(
            collect_dispatch(
                plugin, FakeEvent("踢人 @小明", mentions=[mention("TARGET_1")])
            )
        )
        self.assertEqual(api.calls[-1][0], "kick")

    def test_recall_without_cache(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("撤回")))
        self.assertIn("没找到可撤回", out[0][1])

    def test_recall_with_cache(self):
        api = FakeAPI()
        plugin = make_plugin(api=api)
        plugin.cache.add(
            CachedMessage(
                message_id="OLD_1",
                group="GROUP_1",
                sender="X",
                name="广告",
                text="广告",
            )
        )
        out = asyncio.run(
            collect_dispatch(plugin, FakeEvent("撤回", message_id="MSG_NOW"))
        )
        self.assertIn("已撤回 1 条", out[0][1])
        self.assertEqual(api.calls[-1][2], "OLD_1")

    def test_recall_multiple(self):
        api = FakeAPI()
        plugin = make_plugin(api=api)
        for index in range(3):
            plugin.cache.add(
                CachedMessage(
                    message_id=f"OLD_{index}",
                    group="GROUP_1",
                    sender="X",
                    name="n",
                    text="t",
                )
            )
        asyncio.run(collect_dispatch(plugin, FakeEvent("撤回 2", message_id="MSG_NOW")))
        recalled = [call for call in api.calls if call[0] == "recall"]
        self.assertEqual(len(recalled), 2)

    def test_status(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("禁言状态")))
        self.assertIn("全员禁言", out[0][1])

    def test_global_mute(self):
        api = FakeAPI()
        plugin = make_plugin(api=api)
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("全员禁言")))
        self.assertIn("已开启全员禁言", out[0][1])
        self.assertEqual(api.calls[-1][0], "global")

    def test_global_mute_unsupported(self):
        api = FakeAPI(result=ApiResult(ok=False, status=404, message="not found"))
        plugin = make_plugin(api=api)
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("解除全员禁言")))
        self.assertIn("设置失败", out[0][1])

    def test_plain_chat_ignored(self):
        plugin = make_plugin()
        self.assertEqual(
            asyncio.run(collect_dispatch(plugin, FakeEvent("今天真热"))), []
        )

    def test_dedup(self):
        plugin = make_plugin()
        event = FakeEvent("禁言状态")
        first = asyncio.run(collect_dispatch(plugin, event))
        second = asyncio.run(collect_dispatch(plugin, event))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestJoinApproval(unittest.TestCase):
    def test_list_join_requests(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("入群申请")))
        self.assertIn("待审批入群申请", out[0][1])
        self.assertIn("1. 甲", out[0][1])
        self.assertIn("同意 1", out[0][1])

    def test_approve_first(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("同意 1")))
        self.assertIn("已通过 1 条", out[0][1])
        self.assertIn(("approve_join", "GROUP_1", "J1", True), plugin.api.calls)

    def test_reject_all(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("拒绝 全部")))
        self.assertIn("已拒绝 2 条", out[0][1])
        self.assertIn(("approve_join", "GROUP_1", "J2", False), plugin.api.calls)

    def test_index_out_of_range(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("同意 9")))
        self.assertIn("序号超了", out[0][1])


class TestBlacklistChange(unittest.TestCase):
    def test_add_blacklist(self):
        plugin = make_plugin()
        event = FakeEvent("拉黑", mentions=[{"id": "TARGET_1", "username": "小红"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("加入黑名单", out[0][1])
        self.assertIn(("blacklist_op", "GROUP_1", ("TARGET_1",), "add"), plugin.api.calls)

    def test_remove_blacklist(self):
        plugin = make_plugin()
        event = FakeEvent("解除拉黑", mentions=[{"id": "TARGET_1"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("移出黑名单", out[0][1])
        self.assertIn(("blacklist_op", "GROUP_1", ("TARGET_1",), "del"), plugin.api.calls)


class TestAutoUnmute(unittest.TestCase):
    def test_schedule_records_deadline(self):
        plugin = make_plugin()
        plugin.auto_unmute = True
        plugin._ensure_unmute_task = lambda: None
        plugin._schedule_unmute("GROUP_1", "U1", 60)
        self.assertIn("GROUP_1:U1", plugin._mutes)

    def test_disabled_does_nothing(self):
        plugin = make_plugin()
        plugin.auto_unmute = False
        plugin._schedule_unmute("GROUP_1", "U1", 60)
        self.assertEqual(plugin._mutes, {})

class TestClaimAdmin(unittest.TestCase):
    def test_claim_when_empty(self):
        plugin = make_plugin()
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("/初始化管理员")))
        self.assertIn("已把你设为管理员", out[0][1])
        self.assertEqual(plugin.admin_openids, {"MEMBER_1"})

    def test_claim_refused_when_taken(self):
        plugin = make_plugin(admin_openids=["OTHER"])
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("/初始化管理员")))
        self.assertIn("已经有内容", out[0][1])
        self.assertEqual(plugin.admin_openids, {"OTHER"})

    def test_permission_hint_when_api_denied(self):
        plugin = make_plugin(api=FakeAPI(role_denied=True))
        event = FakeEvent("/禁言", mentions=[{"id": "TARGET_1"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("初始化管理员", out[0][1])
        self.assertIn("11253", out[0][1])

class TestRecallParsing(unittest.TestCase):
    def test_count_with_trailing_words(self):
        # 用户可能把说明一起粘进来：「/撤回 3 撤回最近几条消息」
        plugin = make_plugin(admin_openids=["MEMBER_1"])
        out = asyncio.run(
            collect_dispatch(plugin, FakeEvent("/撤回 3 撤回最近几条消息"))
        )
        self.assertTrue(out, "带说明的撤回命令也要有回应")
        self.assertIn("撤回", out[0][1])

    def test_plain_recall(self):
        plugin = make_plugin(admin_openids=["MEMBER_1"])
        out = asyncio.run(collect_dispatch(plugin, FakeEvent("/撤回")))
        self.assertTrue(out)

class TestGroupAdmins(unittest.TestCase):
    def test_parse_from_string(self):
        parsed = plugin_main.QQGroupAdminPlugin._parse_group_admins(
            '{"G1": ["A", "B"], "G2": "C,D"}'
        )
        self.assertEqual(parsed["G1"], ["A", "B"])
        self.assertEqual(parsed["G2"], ["C", "D"])

    def test_group_admin_can_mute(self):
        plugin = make_plugin(admin_openids=["OTHER"])
        plugin.group_admins = {"GROUP_1": ["MEMBER_1"]}
        event = FakeEvent("/禁言", mentions=[{"id": "TARGET_1", "username": "目标"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("已禁言", out[0][1])
        self.assertIn(("mute", "GROUP_1", "TARGET_1", 600), plugin.api.calls)

    def test_other_group_admin_cannot(self):
        plugin = make_plugin(admin_openids=["OTHER"])
        plugin.group_admins = {"GROUP_2": ["MEMBER_1"]}
        event = FakeEvent("/禁言", mentions=[{"id": "TARGET_1"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("没有这个群的群管权限", out[0][1])

    def test_add_group_admin(self):
        plugin = make_plugin(admin_openids=["MEMBER_1"])
        plugin.group_admins = {}
        event = FakeEvent("/添加群管", mentions=[{"id": "TARGET_1", "username": "小红"}])
        out = asyncio.run(collect_dispatch(plugin, event))
        self.assertIn("设为群管", out[0][1])
        self.assertEqual(plugin.group_admins["GROUP_1"], ["TARGET_1"])
