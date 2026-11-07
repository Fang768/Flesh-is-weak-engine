import unittest
from astrbot_plugin_nexus_gm.public_guide import GUIDE,guide_request
class PublicGuide(unittest.TestCase):
 def test_newcomer_requests(self):
  for text in ('介绍一下世界观和基本玩法','我想读规则书','简要把主要世界观的规则读出来','讲讲世界观，不要剧透','这个游戏怎么玩？'):
   self.assertTrue(guide_request(text),text)
 def test_not_an_action_or_authorization(self):
  for text in ('确认','批准03号入团','暂停','我想玩03号扳手','我撬开终端','你好','CAP规则是什么意思','不要介绍世界观','【暂停】请介绍世界观','/neonpropose 介绍世界观'):
   self.assertFalse(guide_request(text),text)
 def test_public_scope_and_real_rules(self):
  for word in ('2077年秋','1d10','1和10都不自动成败','尚未生成','本人决定','暂停','不会开始游戏'):
   self.assertIn(word,GUIDE)
  for forbidden in ('米拉','死去快递员','Relic芯片任务答案','揭晓骰表'):
   self.assertNotIn(forbidden,GUIDE)
