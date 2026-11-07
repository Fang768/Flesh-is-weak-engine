"""修好 core/card_flow.py 的 templates_text（heredoc 会吃掉反斜杠，改用脚本文件）。"""
import io

P = "core/card_flow.py"
NL = chr(10)
BS = chr(92)          # 反斜杠
NL_ESC = BS + "n"     # 源码里的 \n 两个字符

src = io.open(P, encoding="utf-8").read()
lines = src.split(NL)

start = next(i for i, l in enumerate(lines) if l.startswith("def templates_text"))
end = next(i for i, l in enumerate(lines) if l.startswith("def _plain_command"))

width = len(str(end))
print(f"replacing lines {start + 1}-{end} ({end - start} lines)")

block = [
    "def templates_text() -> str:",
    "    return (",
    "        roster.template_menu()",
    '        + "' + NL_ESC + NL_ESC + '直接说「给我一个角色，独狼」就按模板建；'
    '说「模板」可看两套清单。"',
    '        + "' + NL_ESC + NL_ESC + '"',
    "        + easy.format_list()",
    '        + "' + NL_ESC + NL_ESC + '简易版卡比核心书少一半规则，'
    '适合随便玩一局；领取说「认领 火炬」。"',
    "    )",
    "",
    "",
]

new_lines = lines[:start] + block + lines[end:]
out = NL.join(new_lines)
io.open(P, "w", encoding="utf-8", newline=NL).write(out)
compile(out, P, "exec")
print("ok")
