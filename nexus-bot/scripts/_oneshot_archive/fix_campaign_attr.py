"""修 card_flow/gateway 的 Campaign 属性访问 + 校正模拟器断言。"""
import io

NL = chr(10)


def read(p):
    return io.open(p, encoding="utf-8").read()


def write(p, s):
    io.open(p, "w", encoding="utf-8", newline=NL).write(s)
    compile(s, p, "exec")


# 1) card_flow: Campaign 是 dataclass，不是 dict
P = "core/card_flow.py"
s = read(P)
s = s.replace(
    '        camp = st.campaign or {}\n',
    "        camp = st.campaign\n",
)
s = s.replace(
    '            campaign_id=camp.get("id") if camp else None,\n'
    '            scene=camp.get("scene", "") if camp else "",\n',
    "            campaign_id=camp.id if camp else None,\n"
    '            scene=(camp.scene or "") if camp else "",\n',
)
write(P, s)
print("card_flow campaign attr ok")

# 2) gateway: 同一处 bug
P = "plugins/qq_gateway/__init__.py"
s = read(P)
s = s.replace(
    '            campaign_id=state.campaign["id"] if state.campaign else None,\n'
    '            scene=state.campaign["scene"] if state.campaign else "",\n',
    "            campaign_id=state.campaign.id if state.campaign else None,\n"
    '            scene=(state.campaign.scene or "") if state.campaign else "",\n',
)
write(P, s)
print("gateway campaign attr ok")

# 3) 模拟器断言/流程校正
P = "scripts/simulate.py"
s = read(P)
s = s.replace(
    'expect("还没开团" in r or "空闲" in r or "没有" in r, "空闲态能报状态")',
    'expect("没开团" in r or "空闲" in r, "空闲态能报状态")',
)
s = s.replace(
    'expect("新开" in r or "继续" in r, "群主可以开团")',
    'show("u1", "开团", r)\n'
    '    expect("确认" in r or "新开" in r or "继续" in r, "群主可以开团")',
)
s = s.replace(
    '    r = await s.say("u1", "继续")\n'
    '    show("u1", "继续", r)\n'
    '    expect("接着跑" in r or "继续" in r, "可以继续")\n'
    '    r = await s.say("u1", "我看看四周")   # 继续之后立刻说话，检验窗口还开着',
    '    r = await s.say("u1", "继续")\n'
    '    show("u1", "继续", r)\n'
    '    expect("继续上次" in r or "继续" in r, "可以继续（先给选项）")\n'
    '    r = await s.say("u1", "继续")   # 二次确认才真正恢复\n'
    '    show("u1", "继续（确认）", r)\n'
    '    r = await s.say("u1", "我看看四周")   # 恢复后立刻说话，检验窗口还开着',
)
write(P, s)
print("simulate asserts ok")
