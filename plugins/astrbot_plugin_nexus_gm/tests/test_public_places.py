import unittest
from astrbot_plugin_nexus_gm.public_places import places_query,PLACES
from astrbot_plugin_neon_save.safe import TEMPLATES
class Places(unittest.TestCase):
 def test_exact_question_and_qualified_question(self):
  for t in ('介绍每个出身','出身的地址相关的背景介绍','讲讲所有出身地的背景'):
   out=places_query(t,TEMPLATES)
   for place in ('沃森','太平洲','圣多明戈','日本街','海伍德','恶土','阿德卡多','公司广场'):self.assertIn(place,out)
 def test_single_place_and_character(self):
  self.assertIn('工厂、电站',places_query('圣多明戈是什么地方',TEMPLATES))
  self.assertIn('圣多明戈',places_query('介绍扳手出身地',TEMPLATES));self.assertNotIn('地下数据掮客',places_query('介绍03号出身地',TEMPLATES))
  self.assertIn('不是一个城区',places_query('阿德卡多背景介绍',TEMPLATES))
 def test_not_authorization_or_rewrite(self):
  for t in ('确认','我想认领03号','修改扳手出身地址','【暂停】介绍每个出身','/neonpropose 介绍出身','你好','我的角色卡'):
   self.assertIsNone(places_query(t,TEMPLATES),t)
 def test_no_plot_sources_or_effects(self):
  out=places_query('介绍每个出身',TEMPLATES)
  for x in ('米拉','死去的快递员','当前任务完成','骰点=','XP+'):self.assertNotIn(x,out)
  self.assertIn('不改变角色卡',out)
