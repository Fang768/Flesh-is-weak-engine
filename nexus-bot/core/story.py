"""剧本：把 JSON 读成可查询的场景图。

结构（见 `story/demo.json`）：

    {id, title, premise, acts:[{id,title,goal,scenes:[{id,name,text,npcs,clues,gates,exits,ending}]}],
     npcs:{key:{name,voice}}, endings:{key:text}}

设计约定：
    * `campaigns.scene` 存的是**场景 id**（如 `act1.dock`），显示时用 :func:`scene_name` 转中文名
    * 场景推进只能走 `gates[*].next` 或 `exits`，不允许 LLM 随便编场景
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

STORY_DIR = Path(__file__).resolve().parent.parent / "story"
DEFAULT_ID = "demo"


class StoryError(ValueError):
    pass


@lru_cache(maxsize=8)
def load(story_id: str = DEFAULT_ID) -> dict:
    path = STORY_DIR / f"{story_id}.json"
    if not path.exists():
        raise StoryError(f"没有剧本 {story_id}（{path}）")
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    # 扁平化场景表，方便按 id 取
    scenes: dict[str, dict] = {}
    for act in data.get("acts", []):
        for scene in act.get("scenes", []):
            scene = dict(scene)
            scene.setdefault("act", act["id"])
            scene.setdefault("act_title", act.get("title", ""))
            scene["gates"] = list(scene.get("gates") or [])
            scene["clues"] = list(scene.get("clues") or [])
            scenes[scene["id"]] = scene
    data["scenes"] = scenes
    data.setdefault("npcs", {})
    data.setdefault("endings", {})
    data.setdefault("acts", [])
    return data


def list_stories() -> list[str]:
    return sorted(p.stem for p in STORY_DIR.glob("*.json"))


def first_scene(story_id: str = DEFAULT_ID) -> str:
    data = load(story_id)
    for act in data.get("acts", []):
        for scene in act.get("scenes", []):
            return scene["id"]
    raise StoryError("剧本里没有任何场景")


def scene(story_id: str, scene_id: str) -> dict | None:
    return load(story_id).get("scenes", {}).get(scene_id)


def scene_name(story_id: str, scene_id: str) -> str:
    sc = scene(story_id, scene_id)
    return sc["name"] if sc else (scene_id or "未知地点")


def gate_skills(gate: dict) -> list[str]:
    """一道闸门认哪些技能。

    支持两种写法（向后兼容）：

    * ``"skill": "说服"``            —— 单一技能（旧剧本全这么写）
    * ``"skills": ["说服", "交谈"]`` —— 同一件事的多种合规做法

    「交谈」「说服」在 RED 里都是正式技能（``skills.json:58/61``），
    玩家做同一件事可以合理地走其中任何一个；剧本作者本来就该能表达
    「这几样都算」，而不是逼模型猜中唯一那一个。
    """
    many = [str(s) for s in (gate.get("skills") or []) if str(s)]
    if many:
        return many
    one = str(gate.get("skill") or "")
    return [one] if one else []


def exits_of(scene: dict | None) -> list[dict]:
    """场景的出口，统一成 ``[{"to": 场景id, "label": 提示}]``。

    剧本里两种写法都认：

    * ``"exits": ["a2.noodles"]``                          —— 纯 id 列表
    * ``"exits": [{"to": "a2.office", "label": "去看看"}]`` —— 带提示（官方模组用这种）

    以前 ``turn.propose`` 直接把元素塞进 f-string，遇到 dict 会把整个
    `{'to': ...}` 打进提示词，模型读到的是一坨 JSON。
    """
    out: list[dict] = []
    for item in (scene or {}).get("exits") or []:
        if isinstance(item, dict):
            to = str(item.get("to") or "").strip()
            label = str(item.get("label") or "")
        else:
            to = str(item or "").strip()
            label = ""
        if to:
            out.append({"to": to, "label": label})
    return out


def exit_target(text: str, story_id: str, scene_id: str) -> str | None:
    """玩家这句话想去哪？返回目标场景 id 或 None。

    只认**本场声明过的**出口，所以模型/玩家都编不出新场景。
    匹配顺序：场景 id → 场景名 → 出口提示里的关键词。
    """
    body = str(text or "").strip()
    if not body:
        return None
    sc = scene(story_id, scene_id) or {}
    for exit_ in exits_of(sc):
        to = exit_["to"]
        target = scene(story_id, to) or {}
        name = str(target.get("name") or "")
        label = exit_["label"]
        if to and to in body:
            return to
        if name and name in body:
            return to
        # 提示语里的关键词（「去社科楼四楼看看那间办公室」→ 社科楼）
        for token in _label_tokens(label):
            if token in body:
                return to
    return None


#: 出口提示里过短的词不拿来匹配，免得「去」「看」误伤
_LABEL_STOP = {"直接", "可选", "支线", "看看", "那间", "那里", "我们", "然后",
               "入场", "排队", "带走", "一起"}


def _label_tokens(label: str) -> list[str]:
    """把出口提示拆成可用于匹配的词（≥2 字）。

    先按标点切，再对切片做「去/到/往」之类的动词剥离——否则
    「去社科楼四楼看看那间办公室」整串永远匹配不上玩家说的「社科楼」。
    """
    import re as _re

    raw = str(label or "")
    parts = _re.split(r"[，,、（(）)「」\s]+", raw)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 剥掉开头的方向动词
        stripped = _re.sub(r"^(?:直接|先|就|再|去|到|往|走)+", "", part)
        for token in (stripped, part):
            if len(token) >= 2 and token not in _LABEL_STOP and token not in out:
                out.append(token)
        # 再按「看看/那间/的」这类连接词切成更小的片段
        for piece in _re.split(r"(?:看看|那间|那里|的|和|与)", stripped):
            if len(piece) >= 2 and piece not in _LABEL_STOP and piece not in out:
                out.append(piece)
        # 最后按楼层/数字再切（「社科楼四楼」→「社科楼」），
        # 玩家通常只会说地点名，不会带楼层。
        for piece in _re.split(r"(?:[一二三四五六七八九十百\d]+楼|[0-9])", stripped):
            if len(piece) >= 2 and piece not in _LABEL_STOP and piece not in out:
                out.append(piece)
    return out


def gate_for(story_id: str, scene_id: str, skill: str) -> dict | None:
    """在场景的闸门里找认 ``skill`` 的那一道（含 ``skills`` 多技能写法）。"""
    sc = scene(story_id, scene_id)
    if not sc or not skill:
        return None
    for gate in sc.get("gates") or []:
        if skill in gate_skills(gate):
            return gate
    return None


def best_gate(story_id: str, scene_id: str, skills: list[str]) -> dict | None:
    """给一串候选技能，返回第一道能匹配的闸门（用于模型没给准技能时兜底）。"""
    for skill in skills:
        gate = gate_for(story_id, scene_id, skill)
        if gate:
            return gate
    return None


def next_scene(gate: dict, success: bool) -> str | None:
    return gate.get("next") if success else gate.get("fail")


def ending_text(story_id: str, key: str) -> str:
    return load(story_id).get("endings", {}).get(key, "")


def npc_line(story_id: str, key: str) -> str:
    npc = load(story_id).get("npcs", {}).get(key)
    return f"{npc['name']}（{npc['voice']}）" if npc else key


def match_scenario(text: str) -> str | None:
    """把玩家起的名字对到剧本：先比 id，再比标题，最后比标题片段。"""
    body = (text or "").strip()
    if not body:
        return None
    for sid in list_stories():
        if body == sid or body.lower() == sid.lower():
            return sid
    for sid in list_stories():
        try:
            title = load(sid).get("title", "")
        except StoryError:
            continue
        if title and (body == title or title in body or body in title):
            return sid
    return None
