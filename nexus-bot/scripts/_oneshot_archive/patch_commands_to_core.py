"""把「开打/攻击/战斗/收队/线索」挪到 core，网关只做转发；顺带支持免斜杠。"""
import io

NL = chr(10)


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline=NL).write(s)
    compile(s, p, "exec")


# ── 1. core/combat.py 加 *_text 便捷函数 ───────────────────────
P = "core/combat.py"
s = read(P)
if "def start_text(" not in s:
    s += '''

# ---------- 命令层便捷函数（网关与模拟器共用） ----------

def start_text(conn, group_id: str, arg: str, *, campaign_id=None, scene: str = "") -> str:
    name, count = parse_enemy_arg(arg or "")
    try:
        start(conn, group_id, campaign_id=campaign_id, scene=scene,
              enemies=((name, count),))
    except CombatError as exc:
        return str(exc)
    return status_text(conn, group_id)


def attack_text(conn, group_id: str, user_id: str, arg: str) -> str:
    target, skill = parse_attack_arg(arg or "")
    try:
        return attack(conn, group_id, user_id, target, skill=skill)
    except CombatError as exc:
        return str(exc)


def stop_text(conn, group_id: str) -> str:
    fight = load(conn, group_id)
    if fight is None:
        return "现在没在打。"
    end(conn, group_id)
    return "🕊 收队，脱离战斗。"
'''
    write(P, s)
    print("combat helpers ok")

# ── 2. card_flow：免斜杠接战斗/线索 ───────────────────────────
P = "core/card_flow.py"
s = read(P)
if "开打" not in s:
    anchor = '''    if _re.fullmatch(r"(模板|模板列表|templates)", body):'''
    block = '''    m = _re.match(r"^(?:开打|fight)\\s*(.*)$", body)
    if m:
        from cyberpunk import names as _n  # noqa: F401
        from core import combat as _cbt

        st = GATE.state(group_id)
        camp = st.campaign or {}
        return _cbt.start_text(
            CONN,
            group_id,
            m.group(1),
            campaign_id=camp.get("id") if camp else None,
            scene=camp.get("scene", "") if camp else "",
        )
    m = _re.match(r"^(?:攻击|attack)\\s*(.*)$", body)
    if m:
        from core import combat as _cbt

        return _cbt.attack_text(CONN, group_id, user_id, m.group(1))
    if _re.fullmatch(r"(?:战斗|battle|战况)", body):
        from core import combat as _cbt

        return _cbt.status_text(CONN, group_id)
    if _re.fullmatch(r"(?:收队|stop|撤退)", body):
        from core import combat as _cbt

        return _cbt.stop_text(CONN, group_id)
    if _re.fullmatch(r"(?:线索|clues)", body):
        from core import clues as _cl

        return _cl.text_of(CONN, group_id)
'''.replace("\\\\", "\\")
    if anchor not in s:
        raise SystemExit("card_flow 锚点没找到")
    s = s.replace(anchor, block + anchor, 1)
    # META：这些词在进行中也要算系统指令
    s = s.replace(
        '    r"^提升",',
        '    r"^提升",' + NL
        + '    r"^(?:开打|攻击|战斗|战况|收队|撤退|线索|fight|attack|battle|stop|clues)",',
        1,
    )
    write(P, s)
    print("card_flow combat/clue ok")
else:
    print("card_flow already")

# ── 3. 网关改为转发（保持行为一致，去掉重复实现） ──────────────
P = "plugins/qq_gateway/__init__.py"
s = read(P)

old_fight = '''    from core import combat

    gid = group_key(event)
    state = GATE.state(gid)
    name, count = combat.parse_enemy_arg(str(arg))
    try:
        combat.start(
            CONN,
            gid,
            campaign_id=state.campaign["id"] if state.campaign else None,
            scene=state.campaign["scene"] if state.campaign else "",
            enemies=((name, count),),
        )
    except combat.CombatError as exc:
        await _send(bot, event, str(exc))
        return
    await _send(bot, event, combat.status_text(CONN, gid))'''
new_fight = '''    from core import combat

    gid = group_key(event)
    state = GATE.state(gid)
    await _send(
        bot,
        event,
        combat.start_text(
            CONN,
            gid,
            str(arg),
            campaign_id=state.campaign["id"] if state.campaign else None,
            scene=state.campaign["scene"] if state.campaign else "",
        ),
    )'''

if old_fight in s:
    s = s.replace(old_fight, new_fight, 1)
    print("gateway fight ok")
else:
    print("gateway fight 未匹配（可能已改）")

write(P, s)
