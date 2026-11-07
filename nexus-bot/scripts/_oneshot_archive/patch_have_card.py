"""「我有角色 / 我的卡」直接弹卡，不必过模型。"""
import io

NL = chr(10)
P = "core/card_flow.py"
src = io.open(P, encoding="utf-8").read()

anchor = '    if _re.fullmatch(r"(我的卡|档案|卡面|角色卡|sheet)", body):'
add = ('    if _re.fullmatch(r"(?:我(?:有|的)(?:角色|人物|卡)?'
       '|用(?:我)?的?卡|发(?:我)?的?卡)", body):' + NL
       + "        return sheet(group_id, user_id)" + NL)

if add in src:
    print("already")
elif anchor in src:
    src = src.replace(anchor, add + anchor, 1)
    io.open(P, "w", encoding="utf-8", newline=NL).write(src)
    compile(src, P, "exec")
    print("plain 我有角色 ok")
else:
    raise SystemExit("锚点没找到")
