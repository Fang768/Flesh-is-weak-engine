"""敌人模板库 + 场景遭遇自动铺场。"""

from core import combat, db, roster, session


def _pc(conn, group_id="g1", qq="1001"):
    tpl = roster.load_templates()[0]
    card = roster.build(template=tpl["name"], name="测试员")
    roster.save(conn, group_id, qq, card)
    return card


def test_preset_from_json():
    p = combat.enemy_preset("小丑")
    assert p is not None
    assert (p.hp, p.sp, p.damage) == (35, 11, "3D6")
    # 内置三档仍然在
    assert combat.enemy_preset("打手").hp == 20
    assert "鲁思文勋爵" in combat.enemy_names()


def test_start_with_library_enemy():
    conn = db.connect(":memory:")
    _pc(conn)
    combat.start(conn, "g1", enemies=(("僧侣", 1),), roller=__import__("cyberpunk.dice", fromlist=["x"]).ScriptedRoller([5, 5, 5]))
    c = combat.status_text(conn, "g1")
    assert "僧侣" in c


def test_start_text_uses_scene_encounter():
    conn = db.connect(":memory:")
    _pc(conn)
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    text = combat.start_text(conn, "g1", "")
    assert "小丑" in text and "僧侣" in text and "爱乐吸血鬼蠢蛋" in text
    assert "先攻队列" in text
    # 玩家数 1 → 吸血鬼 1 只；阵营写进 side
    rows = conn.execute("SELECT name, side FROM combatants WHERE group_id='g1'").fetchall()
    by_name = {r["name"]: r["side"] for r in rows}
    assert by_name["小丑"] == "clown"
    assert by_name["僧侣"] == "monk"
    vamps = [n for n in by_name if n.startswith("爱乐吸血鬼")]
    assert len(vamps) == 1 and by_name[vamps[0]] == "vampire"
    # 吸血鬼只打小丑与僧侣，不打玩家
    assert "〔吸血鬼〕" in text


def test_start_text_explicit_arg_wins():
    conn = db.connect(":memory:")
    _pc(conn)
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    text = combat.start_text(conn, "g1", "打手 2")
    assert "打手×2" in text


def test_vampire_does_not_attack_pc_until_provoked():
    conn = db.connect(":memory:")
    _pc(conn)
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    combat.start_text(conn, "g1", "", )
    current = combat.load(conn, "g1")
    actors = {c.name: c for c in current.combatants}
    vamp = actors["爱乐吸血鬼蠢蛋"]
    assert [c.side for c in combat.foes(current, vamp)] == ["clown", "monk"] or         sorted(c.side for c in combat.foes(current, vamp)) == ["clown", "monk"]
    pc = next(c for c in current.combatants if c.side == "pc")
    combat.provoke(current, pc.side, vamp.side)
    assert vamp.side in current.factions["pc"]


def test_starts_on_pc_turn_even_if_enemy_is_first():
    """开局队列头部是敌人时，先自动结算到轮到玩家，避免卡死。"""
    conn = db.connect(":memory:")
    _pc(conn)
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    combat.start_text(conn, "g1", "")
    cur = combat.load(conn, "g1").current
    assert cur is not None and cur.side == "pc"


def test_faction_foes_follow_declaration():
    conn = db.connect(":memory:")
    _pc(conn)
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    gate.set_scene(camp.id, "a1.opera")
    combat.start_text(conn, "g1", "")
    c = combat.load(conn, "g1")
    people = {x.name: x for x in c.combatants}
    vamp = people["爱乐吸血鬼蠢蛋"]
    assert {x.side for x in combat.foes(c, vamp)} == {"clown", "monk"}
    clown = people["小丑"]
    assert {"pc", "vampire"} <= {x.side for x in combat.foes(c, clown)}
