"""Legacy AstrBot adapter: writes here retain the original consent/admin checks.

The paired NEXUS enrollment adapter has a separate, default-off, transactional
auto-review path exclusively for confirmed character claims and cosmetic profiles.
No enrollment consent or auto-review method is exposed as an LLM tool here.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .store import SaveError
from .safe import SafeStore, TEMPLATES


def _value(raw: str):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _short(s: object, max_len: int = 2400) -> str:
    text = json.dumps(s, ensure_ascii=False, sort_keys=True, indent=2)
    return text if len(text) <= max_len else text[:max_len] + "\n…内容较长，请使用更窄的查询。"


class NeonSavePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # AstrBot's persistent *data* directory, never data/plugins/<plugin>.
        data_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_neon_save"
        self.store = SafeStore(data_dir)

    @staticmethod
    def _group(event: AstrMessageEvent) -> str:
        # Fail closed. QQ official group IDs may be opaque OpenIDs, not numeric QQ IDs.
        platform = str(event.get_platform_name())
        gid = event.get_group_id()
        if platform not in {"qq_official", "aiocqhttp"} or not gid:
            raise SaveError("只允许在QQ群中使用本存档；不支持私聊或其他平台")
        return f"{platform}:{gid}"

    @staticmethod
    def _actor(event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    @staticmethod
    def _admin_group(event: AstrMessageEvent) -> str:
        # Defense in depth in addition to @filter.permission_type(ADMIN).
        if not event.is_admin():
            raise SaveError("只有AstrBot管理员可以修改或导出存档")
        return NeonSavePlugin._group(event)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neoninit")
    async def neon_init(self, event: AstrMessageEvent):
        """管理员在当前QQ群创建第一幕初始存档，重复初始化被拒绝。"""
        try:
            rev = self.store.init(self._admin_group(event), self._actor(event))
            yield event.plain_result(f"霓虹余烬存档创建成功：revision={rev}，第一幕未开始，角色列表为空。")
        except (SaveError, OSError, ValueError) as ex:
            yield event.plain_result(f"未初始化：{ex}")

    @filter.command("neonstatus")
    async def neon_status(self, event: AstrMessageEvent):
        """查询本群存档修订号和世界概况（不写入）。"""
        try:
            result = self.store.read(self._group(event))
            w = result["snapshot"]["world"]
            roles = result["snapshot"]["roles"]
            active = sum(r["status"] != "retired" for r in roles.values())
            yield event.plain_result(
                f"霓虹余烬 revision={result['revision']} | 更新：{result['updated_at']}\n"
                f"安全暂停：{'是' if self.store.gate_state(self._group(event))['paused'] else '否'}\n"
                f"幕：{w['act']} | 场景：{w['scene_status']} | 地点：{w['location'] or '待确认'}\n"
                f"当前任务：{w['current_task_id'] or '无'} | 警戒：{w['alert_level']}\n"
                f"团队现金：{w['team_cash'] if w['team_cash'] is not None else '未确认'}€$\n"
                f"在册：{active}/10，含退团历史共{len(roles)}条 | " + (", ".join(roles.keys()) if roles else "未登记")
            )
        except (SaveError, OSError, ValueError) as ex:
            yield event.plain_result(f"查询失败：{ex}")

    @filter.command("neonwhoami")
    async def neon_whoami(self, event: AstrMessageEvent):
        """显示平台记录的当前用户ID，供管理员绑定角色。"""
        try:
            self._group(event)
            yield event.plain_result(f"你在当前QQ群中的平台用户ID：{self._actor(event)}")
        except SaveError as ex:
            yield event.plain_result(str(ex))

    @filter.command("neoncard")
    async def neon_card(self, event: AstrMessageEvent, character_id: str):
        """查询当前群指定角色的公开存档。"""
        try:
            r = self.store.read(self._group(event))["snapshot"]["roles"].get(character_id)
            if r is None:
                raise SaveError("未找到该角色")
            yield event.plain_result(_short(r))
        except (SaveError, OSError, ValueError) as ex:
            yield event.plain_result(f"查询失败：{ex}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonjoin")
    async def neon_join(self, event: AstrMessageEvent, revision: int, qq_id: str, character_id: str, nickname: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonset")
    async def neon_set(self, event: AstrMessageEvent, revision: int, target: str, field: str, value: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonpatch")
    async def neon_patch(self, event: AstrMessageEvent, revision: int, payload_b64: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neondossier")
    async def neon_dossier(self, event: AstrMessageEvent, revision: int, character_id: str, task_id: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonreveal")
    async def neon_reveal(self, event: AstrMessageEvent, revision: int, character_id: str, category: int, fact: str, reward: str, evidence: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonhistory")
    async def neon_history(self, event: AstrMessageEvent):
        """仅管理员查询本群最近的持久化修改审计。"""
        try:
            rows = self.store.history(self._admin_group(event))
            yield event.plain_result("最近审计记录：\n" + "\n".join(
                f"v{x['revision']} {x['timestamp']} {x['action']} by {x['actor']}：{x['reason']}" for x in rows))
        except (SaveError, OSError, ValueError) as ex:
            yield event.plain_result(f"查询失败：{ex}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonexport")
    async def neon_export(self, event: AstrMessageEvent):
        """将完整本群存档导出为数据目录中的JSON备份，不直接向QQ群发送敏感全文。"""
        try:
            backup_id, _ = self.store.export(self._admin_group(event), self._actor(event))
            yield event.plain_result(f"备份已写入AstrBot本地插件数据目录，备份ID：{backup_id}。完整JSON不在群里公开，详见README。")
        except (SaveError, OSError, ValueError) as ex:
            yield event.plain_result(f"导出失败：{ex}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonrestore")
    async def neon_restore(self, event: AstrMessageEvent, revision: int, backup_id: str):
        """旧版直接写入入口已停用，改用审核提案。"""
        yield event.plain_result("旧写入命令已停用；请使用 /neonpropose → /neonconsent → /neonapprove。数据库未修改。")

    @filter.llm_tool(name="neon_read_save")
    async def neon_read_save(self, event: AstrMessageEvent, section: str):
        """读取当前QQ群霓虹余烬数据库的真实公开状态。绝不能声称此工具会写入数据。

        Args:
            section(string): 查询部分。只能填summary、world、roles或role:角色号，例如role:03。
        """
        try:
            result = self.store.read(self._group(event))
            snapshot = result["snapshot"]
            if section == "summary":
                w = snapshot["world"]
                content = {"act": w["act"], "scene_status": w["scene_status"],
                           "location": w["location"], "current_task_id": w["current_task_id"],
                           "team_cash": w["team_cash"], "role_ids": list(snapshot["roles"])}
            elif section == "world":
                content = snapshot["world"]
            elif section == "roles":
                content = {cid: {"nickname": r["nickname"], "status": r["status"], "hp": r["hp"],
                                  "ram": r["ram"], "xp": r["xp"], "cash": r["cash"],
                                  "dossier_progress": r["dossier_progress"], "dossier_stage": r["dossier_stage"]}
                           for cid, r in snapshot["roles"].items()}
            elif section.startswith("role:") and section[5:] in snapshot["roles"]:
                content = snapshot["roles"][section[5:]]
            else:
                raise SaveError("仅支持summary、world、roles或已存在的role:角色号")
            return _short({"revision": result["revision"], "section": section, "data": content}, 12000)
        except (SaveError, OSError, ValueError) as ex:
            return f"读取数据库失败：{ex}。不得猜测缺失数值或宣称已保存。"

    @filter.command("neontemplate")
    async def neon_template(self, event: AstrMessageEvent, cid: str):
        try:
            self._group(event)
            if cid not in TEMPLATES: raise SaveError("模板编号为01—10")
            yield event.plain_result(_short(TEMPLATES[cid], 6000))
        except (SaveError, ValueError) as ex:
            yield event.plain_result(str(ex))

    @filter.command("neonclaim")
    async def neon_claim(self, event: AstrMessageEvent, revision: int, cid: str, nickname: str):
        try:
            p=self.store.propose(self._group(event),self._actor(event),revision,
                {"kind":"initialize","cid":cid,"user":self._actor(event),"nickname":nickname})
            yield event.plain_result(self._preview(p))
        except (SaveError, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as ex:
            yield event.plain_result(f"未创建角色：{ex}")

    @staticmethod
    def _preview(p):
        cid=p['payload'].get('cid')
        result=p['result']['roles'].get(cid) if cid else p['result']['world']
        if p['payload']['kind']=='batch': result='批次逐项核对：/neonview 提案ID 角色号（或world）'
        return (f"待审核提案 {p['id']} | revision={p['revision']}\n摘要 {p['digest']}\n"
                +_short({"proposal":p['payload'],"result":result}, 10000)
                +f"\n本人确认：/neonconsent {p['id']} {p['digest']}\n管理员提交：/neonapprove {p['id']} {p['digest']}")

    @filter.command("neonpropose")
    async def neon_propose(self, event: AstrMessageEvent, revision: int, payload_b64: str):
        try:
            if len(payload_b64)>16000: raise SaveError("提案过大")
            payload=json.loads(base64.b64decode(payload_b64+"="*(-len(payload_b64)%4),altchars=b"-_",validate=True))
            p=self.store.propose(self._group(event),self._actor(event),revision,payload)
            yield event.plain_result(self._preview(p))
        except (SaveError, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as ex:
            yield event.plain_result(f"提案失败：{ex}")

    @filter.command("neonconsent")
    async def neon_consent(self, event: AstrMessageEvent, pid: str, digest: str):
        try:
            self.store.consent(self._group(event),self._actor(event),pid,digest)
            yield event.plain_result("已记录本人对该版本提案的同意；尚未修改游戏存档。")
        except (SaveError, ValueError) as ex:
            yield event.plain_result(f"未确认：{ex}")

    @filter.command("neonreject")
    async def neon_reject(self, event: AstrMessageEvent, pid: str, digest: str):
        try:
            self.store.consent(self._group(event),self._actor(event),pid,digest,False)
            yield event.plain_result("提案已拒绝；游戏存档未改变。")
        except (SaveError, ValueError) as ex:
            yield event.plain_result(str(ex))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonapprove")
    async def neon_approve(self, event: AstrMessageEvent, pid: str, digest: str):
        try:
            key=self._admin_group(event)
            rev=self.store.approve(key,self._actor(event),pid,digest,is_admin=event.is_admin())
            yield event.plain_result(f"已原子提交全部变更并保存审计，revision={rev}")
        except (SaveError, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as ex:
            yield event.plain_result(f"未保存：{ex}")

    @filter.command("neonroll")
    async def neon_roll(self, event: AstrMessageEvent, cid: str, purpose: str, count: int, sides: int):
        try:
            key=self._group(event)
            r=self.store.read(key)['snapshot']['roles'].get(cid)
            if not r or (r['qq_user_id']!=self._actor(event) and not event.is_admin()): raise SaveError("只能由本人或管理员为该角色掷骰")
            result=self.store.roll(key,self._actor(event),cid,purpose,count,sides)
            yield event.plain_result("公开骰点 "+_short(result)+"；用途="+purpose+"；角色="+cid)
        except (SaveError, ValueError) as ex:
            yield event.plain_result(f"掷骰失败：{ex}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonbackup")
    async def neon_backup(self, event: AstrMessageEvent):
        try:
            self._admin_group(event)
            yield event.plain_result("完整数据库备份已生成："+self.store.backup_database())
        except (SaveError, OSError) as ex:
            yield event.plain_result(str(ex))

    @filter.llm_tool(name="neon_propose_change")
    async def neon_propose_change(self, event: AstrMessageEvent, revision: int, proposal_json: str):
        """提出待审核变更，不能提交游戏存档，也不能代替玩家同意。

        Args:
            revision(int): 当前数据库修订号。
            proposal_json(string): JSON提案，格式见README；不能包含授权或伪造骰点。
        """
        try:
            if len(proposal_json)>12000: raise SaveError("提案过大")
            payload=json.loads(proposal_json)
            if not isinstance(payload,dict):raise SaveError('提案必须是对象')
            if payload.get('kind')=='initialize' or any(k in payload for k in ('type','role_id','character_id')):
                return '未创建提案：角色认领必须由本人在已开放群发送自然语言申请，由程序读取真实账号和模板；模型不能代填认领JSON或代替同意。未修改游戏存档。'
            p=self.store.propose(self._group(event),self._actor(event),revision,payload)
            return self._preview(p)
        except (SaveError, ValueError, TypeError, KeyError, sqlite3.Error, OSError) as ex:
            return f"未创建提案：{ex}；未修改游戏存档。"

    @filter.command("neonview")
    async def neon_view(self, event: AstrMessageEvent, pid: str, section: str):
        try:
            key=self._group(event)
            with self.store._db() as db:
                p=self.store.proposal(db,key,pid)
                snapshot=json.loads(p['preview'])
                content=snapshot['world'] if section=='world' else snapshot['roles'].get(section)
                yield event.plain_result(f"提案 {pid} | 摘要 {p['digest']}\n"+_short({"payload":json.loads(p['payload']),"result":content},12000))
        except (SaveError, ValueError, OSError, sqlite3.Error) as ex:
            yield event.plain_result(str(ex))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("neonaudit")
    async def neon_audit(self, event: AstrMessageEvent):
        try:
            key=self._admin_group(event)
            with self.store._db() as db:
                rows=db.execute("SELECT actor,action,detail,timestamp FROM neon_v020_events WHERE group_key=? AND action!='preimage' ORDER BY id DESC LIMIT 10",(key,)).fetchall()
                yield event.plain_result(_short([dict(r) for r in rows],12000))
        except (SaveError, ValueError, OSError, sqlite3.Error) as ex:
            yield event.plain_result(str(ex))
