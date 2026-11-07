# -*- coding: utf-8 -*-
"""QQ 开放平台（官方机器人）群管理接口封装。

只用官方文档里公开的 HTTP 接口，不依赖 botpy / AstrBot 内部对象：

- 换 token：``POST https://bots.qq.com/app/getAppAccessToken``
- 撤回：``DELETE /v2/groups/{group_openid}/messages/{message_id}``
- 禁言 / 解禁：``POST /v2/groups/{group_openid}/restrict_chat_setting``
- 查询禁言：``GET  /v2/groups/{group_openid}/restrict_chat_setting``
- 踢人：``POST /v2/groups/{group_openid}/batch_remove_members``

注意权限前提：机器人在群里必须是**管理员**，且 open 平台侧要开通对应能力。
踢人属于内邀接口，没开通会返回 11253。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"

#: 官方错误码 -> 人话
ERROR_HINTS = {
    "11253": "该接口需要白名单权限，当前应用没开通（踢人属于内邀能力）",
    "40061001": "参数不合法，检查 openid 或禁言时长",
    "40062003": "没有操作权限：机器人得是群管理员，且只能操作普通成员",
    "40064004": "消息超过 2 分钟，按平台规则不能撤回了",
    "50065001": "撤回失败，稍后再试",
}


class ApiError(Exception):
    """网络或鉴权层面的失败。"""


@dataclass
class ApiResult:
    ok: bool = False
    status: int = 0
    error_code: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)

    @property
    def hint(self) -> str:
        if self.ok:
            return ""
        if self.error_code in ERROR_HINTS:
            return ERROR_HINTS[self.error_code]
        return self.message or f"HTTP {self.status}"


class QQOfficialAPI:
    """官方接口客户端。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        timeout: int = 10,
        verify_ssl: bool = True,
    ) -> None:
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.timeout = max(5, int(timeout))
        self.verify = verify_ssl
        self._token = ""
        self._expire_at = 0.0
        self._http: Any = None

    @property
    def ready(self) -> bool:
        return bool(self.app_id and self.app_secret)

    # ---------------------------------------------------------------- 基础

    def _client(self) -> Any:
        if httpx is None:
            raise ApiError("缺少 httpx 依赖")
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout, verify=self.verify)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            finally:
                self._http = None

    async def _token_value(self) -> str:
        if not self.ready:
            raise ApiError("没配置 AppID / AppSecret")
        if self._token and time.time() < self._expire_at - 60:
            return self._token

        try:
            response = await self._client().post(
                TOKEN_URL,
                json={"appId": self.app_id, "clientSecret": self.app_secret},
            )
        except Exception as exc:
            raise ApiError(f"获取 token 失败（{type(exc).__name__}）") from exc

        if response.status_code != 200:
            raise ApiError(f"获取 token 失败：HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError("token 接口返回了非 JSON") from exc

        token = str(data.get("access_token") or "").strip()
        if not token:
            raise ApiError(f"token 接口没有返回 access_token：{str(data)[:120]}")

        try:
            expires = int(data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires = 7200

        self._token = token
        self._expire_at = time.time() + max(300, expires)
        return token

    async def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> ApiResult:
        token = await self._token_value()
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        url = f"{API_BASE}{path}"

        try:
            response = await self._client().request(
                method, url, headers=headers, json=payload
            )
        except Exception as exc:
            raise ApiError(f"请求失败（{type(exc).__name__}）") from exc

        data: dict = {}
        if response.content:
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError:
                data = {}

        if response.status_code in (200, 201, 204):
            return ApiResult(ok=True, status=response.status_code, data=data)

        error_code = str(data.get("code") or data.get("err_code") or "")
        message = str(data.get("message") or data.get("msg") or "")
        return ApiResult(
            ok=False,
            status=response.status_code,
            error_code=error_code,
            message=message,
            data=data,
        )

    # ---------------------------------------------------------------- 能力

    async def recall_message(self, group_openid: str, message_id: str) -> ApiResult:
        return await self._request(
            "DELETE",
            f"/v2/groups/{group_openid}/messages/{message_id}",
        )

    async def mute_member(
        self, group_openid: str, member_openid: str, seconds: int
    ) -> ApiResult:
        expire = time.strftime(
            "%Y-%m-%dT%H:%M:%S+08:00", time.localtime(time.time() + max(1, seconds))
        )
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/restrict_chat_setting",
            {
                "members": [
                    {
                        "op": "add",
                        "member_openid": member_openid,
                        "mute_expire_at": expire,
                    }
                ]
            },
        )

    async def unmute_member(self, group_openid: str, member_openid: str) -> ApiResult:
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/restrict_chat_setting",
            {
                "members": [
                    {"op": "del", "member_openid": member_openid, "mute_expire_at": ""}
                ]
            },
        )

    async def query_restrict(self, group_openid: str) -> ApiResult:
        return await self._request(
            "GET", f"/v2/groups/{group_openid}/restrict_chat_setting"
        )

    async def remove_members(
        self, group_openid: str, member_openids: list[str], blacklist: bool = False
    ) -> ApiResult:
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/batch_remove_members",
            {
                "member_openids": member_openids[:20],
                "add_to_member_blacklist": bool(blacklist),
            },
        )

    async def set_global_mute(self, group_openid: str, enable: bool) -> ApiResult:
        """全员禁言。

        官方文档只公开了查询用的 ``global_rule``，设置接口没有正式文档；
        这里按同一路径尝试提交，失败时由上层提示「平台未开放」。
        """
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/restrict_chat_setting",
            {"global_rule": {"mode": "always" if enable else "none"}},
        )

    # ---------------------------------------------------------------- 群与成员

    async def group_info(self, group_openid: str) -> ApiResult:
        return await self._request("GET", f"/v2/groups/{group_openid}/info")

    async def group_members(self, group_openid: str, cursor: str = "") -> ApiResult:
        path = f"/v2/groups/{group_openid}/members"
        if cursor:
            path += f"?cursor={cursor}"
        return await self._request("GET", path)

    async def member_info(self, group_openid: str, member_openid: str) -> ApiResult:
        return await self._request(
            "GET", f"/v2/groups/{group_openid}/members/{member_openid}"
        )

    async def bot_state(self, group_openid: str) -> ApiResult:
        return await self._request("GET", f"/v2/groups/{group_openid}/bot_state")

    async def blacklist(self, group_openid: str, cursor: str = "") -> ApiResult:
        path = f"/v2/groups/{group_openid}/member_blacklist"
        if cursor:
            path += f"?cursor={cursor}"
        return await self._request("GET", path)

    async def blacklist_op(
        self, group_openid: str, member_openids: list[str], op: str = "add"
    ) -> ApiResult:
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/member_blacklist",
            {"op": op, "member_openids": member_openids[:20]},
        )

    async def join_requests(self, group_openid: str, cursor: str = "") -> ApiResult:
        path = f"/v2/groups/{group_openid}/join_request_list"
        if cursor:
            path += f"?cursor={cursor}"
        return await self._request("GET", path)

    async def approve_join(
        self, group_openid: str, member_openid: str, approve: bool = True
    ) -> ApiResult:
        return await self._request(
            "POST",
            f"/v2/groups/{group_openid}/approval_join_request/{member_openid}",
            {"approve": bool(approve)},
        )
