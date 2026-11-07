"""开团完成后不要再关窗口（否则「我有角色」得重新 @）。"""
import io

NL = chr(10)
P = "core/gate_flow.py"
src = io.open(P, encoding="utf-8").read()

old = '''    ATTENTION.close(group_id)
    return Step(
        reply=(
            f"🎬 《{campaign.title}》开始。'''
new = '''    # 开团后窗口继续留着：紧接着说「我有角色」「给我一个角色」不该重新 @
    ATTENTION.set_stage(group_id, STAGE_CHAT)
    return Step(
        reply=(
            f"🎬 《{campaign.title}》开始。'''

if old not in src:
    raise SystemExit("开团分支没匹配上")
src = src.replace(old, new, 1)
io.open(P, "w", encoding="utf-8", newline=NL).write(src)
compile(src, P, "exec")
print("gate_flow ok")

# 「我有角色」「我有卡」「我的角色」也要算系统请求，别被当成戏内行动
CF = "core/card_flow.py"
s = io.open(CF, encoding="utf-8").read()
anchor = '    r"^我的卡",'
add = '    r"^我(?:有|的)(?:角色|人物|卡)",' + NL + '    r"^(?:用|拿|发)(?:我)?的?卡",'
if anchor not in s:
    raise SystemExit("META 锚点没找到")
s = s.replace(anchor, anchor + NL + add, 1)
io.open(CF, "w", encoding="utf-8", newline=NL).write(s)
compile(s, CF, "exec")
print("card_flow META ok")
