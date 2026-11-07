import io, sys
sys.stdout.reconfigure(encoding="utf-8")
from core import combat as C, roster
from core.db import connect
from cyberpunk import dice

conn = connect(":memory:")
card = roster.build(template="独狼", name="阿飞", skill_overrides={"手枪": 6})
roster.save(conn, "qq:g1", "1001", card)
got = roster.get(conn, "qq:g1", "1001")
print("skills:", got["skills"])
print("stats REF/DEX:", got["stats"]["REF"], got["stats"]["DEX"])

c = C.start(conn, "qq:g1", enemies=(("打手", 1),), roller=dice.ScriptedRoller([9, 2]))
print("init:", [(x.name, x.initiative, x.ref, x.dex, x.dodge, x.skill) for x in c.combatants])
print("current:", c.current.name)

r = dice.ScriptedRoller([10, 3, 3, 3, 3, 3, 3], loop=False)
try:
    t = C.attack(conn, "qq:g1", "1001", "打手", skill="手枪", aim="头", roller=r)
    print("text:", t)
except AssertionError as e:
    print("roller exhausted after", r._i, "rolls:", e)
