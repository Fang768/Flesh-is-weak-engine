"""统一注意力窗口策略：状态转移不关窗，只有用户显式「退下」才关。"""
import io

NL = chr(10)

# ── 1. intents：加一个显式退出意图 ─────────────────────────────
P = "core/intents.py"
s = io.open(P, encoding="utf-8").read()
if "Intent.EXIT" not in s:
    s = s.replace(
        '    UNKNOWN = "unknown"',
        '    EXIT = "exit"  # 用户明确让机器人别听了\n    UNKNOWN = "unknown"',
        1,
    )
    # EXIT 放最前：优先级最高，「别听了，结束」应该走退出而不是结束
    s = s.replace(
        "_RULES: list[tuple[Intent, tuple[str, ...]]] = [" + NL + "    (",
        "_RULES: list[tuple[Intent, tuple[str, ...]]] = [" + NL
        + "    (" + NL
        + "        Intent.EXIT," + NL
        + '        ("别听了", "退下", "别理我", "不用你了", "结束对话", "收声", "闭嘴"),' + NL
        + "    )," + NL
        + "    (",
        1,
    )
    io.open(P, "w", encoding="utf-8", newline=NL).write(s)
    compile(s, P, "exec")
    print("intents ok")
else:
    print("intents already")

# ── 2. gate_flow：所有状态转移改 set_stage，只 EXIT 关窗 ───────
G = "core/gate_flow.py"
src = io.open(G, encoding="utf-8").read()

# 2.1 确认阶段取消
src = src.replace(
    '''    if choice == "cancel":
        ATTENTION.close(group_id)
        return Step(reply="好，取消。", close_window=True)''',
    '''    if choice == "cancel":
        # 「取消」只是放弃这次开团，窗口留着，接着说话不用重新 @
        ATTENTION.set_stage(group_id, STAGE_CHAT)
        return Step(reply="好，取消。想开团随时说一声。")''',
    1,
)

# 2.2 继续主线
src = src.replace(
    '''    if choice == "resume":
        campaign = GATE.resume(group_id)
        ATTENTION.close(group_id)''',
    '''    if choice == "resume":
        campaign = GATE.resume(group_id)
        ATTENTION.set_stage(group_id, STAGE_CHAT)''',
    1,
)

# 2.3 去掉其余 close_window 标记（网关不看这个字段，留着只会误导）
src = src.replace(", close_window=True", "")
src = src.replace("            close_window=True," + NL, "")

# 2.4 EXIT 分支：唯一允许关窗的地方
if "Intent.EXIT" in src:
    pass
else:
    anchor = '''    if intent is Intent.STATUS:
        return Step(reply=state_text(group_id))'''
    exit_block = '''    if intent is Intent.EXIT:
        ATTENTION.close(group_id)
        return Step(reply="好，我先不接话了。有事 @ 我。")

''' + anchor
    if anchor not in src:
        raise SystemExit("STATUS 分支没找到")
    src = src.replace(anchor, exit_block, 1)

io.open(G, "w", encoding="utf-8", newline=NL).write(src)
compile(src, G, "exec")
print("gate_flow ok")
