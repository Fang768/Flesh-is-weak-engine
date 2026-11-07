import unittest
from astrbot_plugin_nexus_gm.presentation import player_text
class Presentation(unittest.TestCase):
 def test_trusted_result_numbers_and_outcome_preserved(self):
  source='03：1d10=1 + TECH8 + 工程4 + 修正(0) = 13 / DV14，失败；本轮不免费重试，可在下一窗口改用其他方法或撤离。'
  out=player_text(source,{'03':'扳手'})
  self.assertIn('扳手：1d10=1 + TECH8 + 工程4 + 修正(0) = 13 / DV14，失败',out)
  self.assertNotIn('下一窗口',out)
 def test_declaration_keeps_formula_and_difficulty(self):
  out=player_text('03声明工程，DV14，1d10+TECH+工程，无1/10自动成败。程序已登记，将自动掷骰并回传判定。')
  self.assertIn('1d10+TECH+工程',out);self.assertIn('难度14',out);self.assertNotIn('程序',out)
 def test_authorization_and_unknown_errors_not_hidden(self):
  for text in ('尚缺相关玩家本人愿意继续的明确确认，保持暂停','SHA256摘要不匹配','未授权恢复','规则冲突，请管理员核对'):
   self.assertEqual(player_text(text),text)
 def test_no_invented_success_in_scene(self):
  out=player_text('抵达无人值守的旧维修终端。可以工程、调查、潜行，或撤离。')
  self.assertIn('无人值守',out);self.assertNotIn('记录已取回',out)
