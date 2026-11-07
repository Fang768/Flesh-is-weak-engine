"""剧本《歌剧院之夜》：加载、场景指针、按名字选剧本。"""

from core import db, session, story


def test_opera_loads_and_chains():
    data = story.load("opera")
    scenes = data["scenes"]
    assert len(scenes) >= 8
    assert story.first_scene("opera") == "a1.hook"
    # 每个 gate 的 next/fail 与 exits 都必须指向存在的场景
    for sid, sc in scenes.items():
        for g in sc["gates"]:
            for key in ("next", "fail"):
                tgt = g.get(key)
                assert not tgt or tgt in scenes, (sid, key, tgt)
        for e in sc.get("exits") or []:
            assert e["to"] in scenes, (sid, e["to"])
    assert "opera_saved" in data["endings"]


def test_match_scenario_by_id_and_title():
    assert story.match_scenario("opera") == "opera"
    assert story.match_scenario("歌剧院之夜") == "opera"
    assert story.match_scenario("霓虹血债") == "demo"
    assert story.match_scenario("随便起个名字") is None


def test_start_new_uses_scenario_scene():
    conn = db.connect(":memory:")
    gate = session.Gate(conn)
    camp = gate.start_new("g1", "歌剧院之夜", scenario="opera")
    assert camp.scenario == "opera"
    assert camp.scene == "a1.hook"
