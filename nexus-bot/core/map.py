"""数据驱动网格地图（赛博朋克红 / 核心书口径）。

规则依据（书内页）：
  · 173 页  单发射击距离 DV 表、全自动距离 DV 表、霰弹 3 格锥形、爆炸 5×5 格
  · 182 页  掩体耐久（示例：厚混凝土路障 25 点），打爆后不再提供保护
  · 167 页  网格移动：每轮最多 MOVE 格（1 格 = 2 米），可走对角线

只做数据与几何，不做规则裁决；裁决在 core/combat.py。
"""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

MAP_DIR = Path(__file__).resolve().parents[1] / "maps"
CELL_METERS = 2

# 地形
WALL = "#"
FLOOR = "."
WATER = "~"
RUBBLE = "%"
DOOR = "+"

TERRAIN_COST = {FLOOR: 1, DOOR: 1, WATER: 2, RUBBLE: 2}
WALKABLE = tuple(TERRAIN_COST)

# 距离档（米），最后一列 401–800
BANDS = ((6, 0), (12, 1), (25, 2), (50, 3), (100, 4), (200, 5), (400, 6), (800, 7))
BAND_LABEL = ("0-6", "7-12", "13-25", "26-50", "51-100", "101-200", "201-400", "401-800")

# 书内页 173：单发射击 DV（None = N/A，打不到）
SINGLE_DV: dict[str, tuple[int | None, ...]] = {
    "手枪": (13, 15, 20, 25, 30, 30, None, None),
    "冲锋枪": (15, 13, 15, 20, 25, 25, 30, None),
    "霰弹枪": (13, 15, 20, 25, 30, 35, None, None),
    "突击步枪": (17, 16, 15, 13, 15, 20, 25, 30),
    "狙击步枪": (30, 25, 25, 20, 15, 16, 17, 20),
    "弓": (15, 13, 15, 17, 20, 22, None, None),
    "榴弹发射器": (16, 15, 15, 17, 20, 22, 25, None),
    "火箭筒": (17, 16, 15, 15, 20, 20, 25, 30),
}

# 书内页 173：全自动距离 DV（只到 100 米）
AUTOFIRE_DV: dict[str, tuple[int, ...]] = {
    "冲锋枪": (20, 17, 20, 25, 30),
    "突击步枪": (22, 20, 17, 20, 25),
}

# 全自动命中数上限：伤害骰 × 命中数（书内页 173）
AUTOFIRE_MAX_HITS = {"冲锋枪": 3, "突击步枪": 4}


class MapError(Exception):
    """地图文件缺字段 / 越界 / 非法地形。"""


@dataclass(frozen=True, slots=True)
class Cover:
    id: str
    name: str
    x: int
    y: int
    hp: int = 20
    blocks_los: bool = True

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def line(self) -> str:
        return f"{self.name}（耐久 {self.hp}）" if self.alive else f"{self.name}（已打烂）"


@dataclass(frozen=True, slots=True)
class Exit:
    x: int
    y: int
    to: str
    label: str = "出口"


@dataclass(slots=True)
class GameMap:
    id: str
    name: str
    grid: list[str]
    cell: int = CELL_METERS
    legend: dict[str, str] = field(default_factory=dict)
    covers: list[Cover] = field(default_factory=list)
    spawns: dict[str, tuple[int, int]] = field(default_factory=dict)
    exits: list[Exit] = field(default_factory=list)

    # ── 基础 ────────────────────────────────────────────────
    @property
    def width(self) -> int:
        return len(self.grid[0])

    @property
    def height(self) -> int:
        return len(self.grid)

    def inside(self, pos: tuple[int, int]) -> bool:
        x, y = pos
        return 0 <= y < self.height and 0 <= x < self.width

    def at(self, pos: tuple[int, int]) -> str:
        if not self.inside(pos):
            return WALL
        return self.grid[pos[1]][pos[0]]

    def terrain_name(self, pos: tuple[int, int]) -> str:
        return self.legend.get(self.at(pos), self.at(pos))

    def walkable(self, pos: tuple[int, int]) -> bool:
        return self.inside(pos) and self.at(pos) in WALKABLE

    def cost(self, pos: tuple[int, int]) -> int:
        return TERRAIN_COST.get(self.at(pos), 99)

    def cover_at(self, pos: tuple[int, int]) -> Cover | None:
        for c in self.covers:
            if (c.x, c.y) == pos and c.alive:
                return c
        return None

    # ── 距离 / DV ───────────────────────────────────────────
    def meters(self, a: tuple[int, int], b: tuple[int, int]) -> int:
        """网格距离 → 米。对角线可走，按切比雪夫距离算格子数。"""
        return self.cell * max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    def band(self, meters: int) -> int | None:
        for top, idx in BANDS:
            if meters <= top:
                return idx
        return None

    def dv(self, a: tuple[int, int], b: tuple[int, int], weapon: str, *, autofire: bool = False) -> int | None:
        """返回该武器在此距离下的 DV；None = 超出武器射程（N/A）。"""
        m = self.meters(a, b)
        idx = self.band(m)
        if idx is None:
            return None
        if autofire:
            table = AUTOFIRE_DV.get(weapon)
            return None if table is None or idx >= len(table) else table[idx]
        table = SINGLE_DV.get(weapon)
        if table is None:
            raise MapError(f"没有这件武器的距离表：{weapon}")
        return table[idx]

    # ── 视线 ────────────────────────────────────────────────
    def blockers(self, a: tuple[int, int], b: tuple[int, int]) -> list[str]:
        """返回挡在两人之间的东西（墙 / 还活着的掩体），空列表 = 通视。"""
        out: list[str] = []
        for pos in line_cells(a, b)[1:-1]:
            if self.at(pos) == WALL:
                out.append(f"墙{pos}")
                break
            cover = self.cover_at(pos)
            if cover and cover.blocks_los:
                out.append(cover.name)
                break
        return out

    def visible(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        return not self.blockers(a, b)

    def cover_on_line(self, a: tuple[int, int], b: tuple[int, int]) -> Cover | None:
        for pos in line_cells(a, b)[1:-1]:
            c = self.cover_at(pos)
            if c:
                return c
        return None

    # ── 移动 ────────────────────────────────────────────────
    def path(self, start: tuple[int, int], goal: tuple[int, int]) -> tuple[list[tuple[int, int]], int]:
        """Dijkstra 最短路。返回 (路径含起点终点, 消耗格数)；不可达返回 ([], 0)。"""
        if not self.walkable(start) or not self.walkable(goal):
            return [], 0
        dist = {start: 0}
        prev: dict[tuple[int, int], tuple[int, int]] = {}
        heap = [(0, start)]
        while heap:
            d, cur = heapq.heappop(heap)
            if cur == goal:
                break
            if d > dist.get(cur, 1 << 30):
                continue
            for nxt in self.neighbors(cur):
                nd = d + self.cost(nxt)
                if nd < dist.get(nxt, 1 << 30):
                    dist[nxt] = nd
                    prev[nxt] = cur
                    heapq.heappush(heap, (nd, nxt))
        if goal not in dist:
            return [], 0
        route = [goal]
        while route[-1] != start:
            route.append(prev[route[-1]])
        route.reverse()
        return route, dist[goal]

    def neighbors(self, pos: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = pos
        out = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nxt = (x + dx, y + dy)
            if not self.walkable(nxt):
                continue
            if dx and dy:  # 不允许贴角穿过两堵墙
                if not self.walkable((x + dx, y)) or not self.walkable((x, y + dy)):
                    continue
            out.append(nxt)
        return out

    def move(self, start: tuple[int, int], goal: tuple[int, int], budget: int) -> tuple[list[tuple[int, int]], int, bool]:
        """按移动力走。返回 (实际路径, 实际消耗, 是否到达)。走不到就停在能到的最远处。"""
        route, cost = self.path(start, goal)
        if not route or cost <= budget:
            return route, cost, bool(route)
        walked = [start]
        used = 0
        for pos in route[1:]:
            step = self.cost(pos)
            if used + step > budget:
                break
            walked.append(pos)
            used += step
        return walked, used, False

    # ── 渲染 ────────────────────────────────────────────────
    def render(self, tokens: dict[tuple[int, int], str] | None = None) -> str:
        tokens = tokens or {}
        head = f"{self.name} {self.width}×{self.height}（每格 {self.cell} 米）"
        cols = "".join(str(i % 10) for i in range(self.width))
        lines = [head, f"  {cols}"]
        for y, row in enumerate(self.grid):
            cells = []
            for x, ch in enumerate(row):
                tok = tokens.get((x, y))
                if tok:
                    cells.append(tok)
                    continue
                cover = self.cover_at((x, y))
                cells.append("o" if cover else ch)
            lines.append(f"{y % 10} {''.join(cells)}")
        lines.append("图例：@ 你  E 敌人  A 队友  o 掩体  # 墙  ~ 水  % 废墟  + 门")
        return chr(10).join(lines)

    def describe(self, pos: tuple[int, int]) -> str:
        bits = [self.terrain_name(pos)]
        cover = self.cover_at(pos)
        if cover:
            bits.append(f"有{cover.name}")
        door = [e for e in self.exits if (e.x, e.y) == pos]
        if door:
            bits.append("通向" + "、".join(e.label for e in door))
        if self.at(pos) == WATER:
            bits.append("移动消耗翻倍")
        if self.at(pos) == RUBBLE:
            bits.append("难行，移动消耗翻倍")
        return "，".join(bits)

    def bearing(self, a: tuple[int, int], b: tuple[int, int]) -> str:
        """粗略方位：用八方向中文。"""
        dx, dy = b[0] - a[0], b[1] - a[1]
        ns = "北" if dy < 0 else ("南" if dy > 0 else "")
        ew = "东" if dx > 0 else ("西" if dx < 0 else "")
        if not ns and not ew:
            return "就在跟前"
        if abs(dx) >= 2 * max(abs(dy), 1):
            return ew + "边"
        if abs(dy) >= 2 * max(abs(dx), 1):
            return ns + "边"
        return ns + ew


def line_cells(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Bresenham：从 a 到 b 经过的格子（含两端）。"""
    x0, y0 = a
    x1, y1 = b
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    out = [(x0, y0)]
    while (x0, y0) != (x1, y1):
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy
        out.append((x0, y0))
    return out


# ── 加载 ────────────────────────────────────────────────────


def _parse(data: dict, source: str) -> GameMap:
    missing = [k for k in ("id", "name", "terrain") if not data.get(k)]
    if missing:
        raise MapError(f"{source} 缺字段：{', '.join(missing)}")
    grid = list(data["terrain"])
    if not grid:
        raise MapError(f"{source} 地形为空")
    width = len(grid[0])
    for i, row in enumerate(grid):
        if len(row) != width:
            raise MapError(f"{source} 第 {i} 行宽度 {len(row)} ≠ {width}")
    legend = data.get("legend") or {}
    legal = set(WALKABLE) | {WALL}
    for row in grid:
        for ch in set(row):
            if ch not in legal:
                raise MapError(f"{source} 出现未定义地形字符 {ch!r}（可用：# . ~ % +）")
    covers = []
    for obj in data.get("objects") or []:
        if obj.get("kind") != "cover":
            continue
        covers.append(
            Cover(
                id=str(obj.get("id") or f"cover{len(covers)}"),
                name=str(obj.get("name") or "掩体"),
                x=int(obj["x"]),
                y=int(obj["y"]),
                hp=int(obj.get("hp", 20)),
                blocks_los=bool(obj.get("blocks_los", True)),
            )
        )
    spawns = {}
    for kind, pos in (data.get("spawns") or {}).items():
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            spawns[str(kind)] = (int(pos[0]), int(pos[1]))
    exits = [
        Exit(x=int(e["x"]), y=int(e["y"]), to=str(e.get("to", "")), label=str(e.get("label", "出口")))
        for e in (data.get("exits") or [])
    ]
    gm = GameMap(
        id=str(data["id"]),
        name=str(data["name"]),
        grid=grid,
        cell=int(data.get("cell", CELL_METERS)),
        legend=legend,
        covers=covers,
        spawns=spawns,
        exits=exits,
    )
    for c in gm.covers:
        if not gm.inside((c.x, c.y)):
            raise MapError(f"{source} 掩体 {c.name} 越界 ({c.x},{c.y})")
    for k, p in gm.spawns.items():
        if not gm.inside(p):
            raise MapError(f"{source} 出生点 {k} 越界 {p}")
    return gm


@lru_cache(maxsize=32)
def load(map_id: str) -> GameMap:
    path = MAP_DIR / f"{map_id}.json"
    if not path.exists():
        raise MapError(f"找不到地图文件：{path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MapError(f"地图 JSON 解析失败：{exc}") from exc
    return _parse(data, path.name)


def exists(map_id: str) -> bool:
    return (MAP_DIR / f"{map_id}.json").exists()


def ids() -> list[str]:
    if not MAP_DIR.exists():
        return []
    return sorted(p.stem for p in MAP_DIR.glob("*.json"))


def dv_for_meters(weapon: str, meters: int, *, autofire: bool = False) -> int | None:
    """没有地图时按米数直接查距离 DV；None = 超出这件武器的射程。"""
    table = (AUTOFIRE_DV if autofire else SINGLE_DV).get(weapon)
    if not table:
        return None
    idx = next((i for top, i in BANDS if int(meters) <= top), None)
    if idx is None or idx >= len(table):
        return None
    return table[idx]


def dv_table(weapon: str, *, autofire: bool = False) -> str:
    """给玩家看的距离表（一行）。"""
    table = (AUTOFIRE_DV if autofire else SINGLE_DV).get(weapon)
    if not table:
        return f"没有 {weapon} 的距离表"
    cells = [("N/A" if v is None else str(v)) for v in table]
    return " | ".join(f"{BAND_LABEL[i]}m:{cells[i]}" for i in range(len(table)))
