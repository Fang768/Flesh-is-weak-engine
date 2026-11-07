import asyncio,json,types,unittest
from unittest.mock import AsyncMock
import test_dynamic_engine as _support
from astrbot_plugin_nexus_gm.dynamic.controller import DynamicController
from astrbot_plugin_nexus_gm.dynamic.engine import NarrativeError
class ControllerTests(unittest.TestCase):
 setUp=_support.DynamicEngine.setUp
 commit=_support.DynamicEngine.commit
 propose=_support.DynamicEngine.propose
 players=_support.DynamicEngine.players
 finish=_support.DynamicEngine.finish
 def fixture(self,n=1):
  self.players(n);p=types.SimpleNamespace(get_model=lambda:'deepseek-v4-pro',text_chat=AsyncMock())
  ctx=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=p));controller=DynamicController(self.e,ctx);event=types.SimpleNamespace(get_sender_id=lambda:'u01',unified_msg_origin='isolated');return controller,p,event
 def test_free_wording_interpreted_without_keywords_and_no_extra_authority(self):
  c,p,e=self.fixture();p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(mode='action',operation='unlock',node='locker',question='')))
  d=asyncio.run(c.interpret(e,self.k,'我把细铁丝弯成钩，试着拨动锁簧'))
  self.assertEqual(d['operation'],'unlock');self.assertIn('_epoch',d)
  prompt=p.text_chat.call_args.kwargs['prompt']
  for hidden in ('03:17','Santo Domingo','ZERO HOUR','家属联盟'):self.assertNotIn(hidden,prompt)
  self.assertNotIn('func_tool',p.text_chat.call_args.kwargs)
 def test_model_supplied_dv_is_rejected(self):
  c,p,e=self.fixture();p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(mode='action',operation='unlock',node='locker',question='',dv=1)))
  with self.assertRaises(NarrativeError):asyncio.run(c.interpret(e,self.k,'开锁'))
  self.assertEqual(self.e.read(self.k)['events'],[])
 def test_pause_during_model_response_drops_action(self):
  c,p,e=self.fixture()
  async def reply(**kwargs):
   self.s.pause_group(self.k,'u01','pause');return types.SimpleNamespace(completion_text=json.dumps(dict(mode='action',operation='talk',node='contact',question='')))
  p.text_chat.side_effect=reply
  with self.assertRaises(NarrativeError):asyncio.run(c.interpret(e,self.k,'问情况'))
  self.assertEqual(self.e.read(self.k)['done'],[])
 def test_unobtained_secret_and_fake_results_not_narrated(self):
  c,p,e=self.fixture()
  for text in ('芯片写着Vesper。','米拉其实受家属联盟资助。','骰点10，你检定成功。','你获得100XP。','预付每人一百，事成再给六百。','你获得一把新装备。'):
   p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(text=text)))
   self.assertIsNone(asyncio.run(c.narrate(e,self.k,'观察现场',dict(check=None))))
 def test_adaptive_scene_plan_is_advisory_not_world_mutation(self):
  c,p,e=self.fixture(5);before=self.s.read(self.k);p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(opening='歌舞伎街口，米拉等着听你们的打算。',opportunities=['可以和米拉谈条件。','也可以先查看储物柜附近。'])))
  self.assertIsNotNone(asyncio.run(c.plan_scene(e,self.k)));self.assertEqual(before,self.s.read(self.k));self.assertIsNotNone(self.e.view(self.k,'u01')['scene_suggestions'])
 def test_plan_cannot_choose_for_players_or_leak_clue(self):
  c,p,e=self.fixture()
  for text in ('你们决定接受委托。','接下来前往Vesper。'):
   p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(opening=text,opportunities=['去查看现场。'])))
   self.assertIsNone(asyncio.run(c.plan_scene(e,self.k)))
 def test_timeout_does_not_roll_or_advance(self):
  c,p,e=self.fixture();p.text_chat.side_effect=asyncio.TimeoutError()
  with self.assertRaises(asyncio.TimeoutError):asyncio.run(c.interpret(e,self.k,'问情况'))
  self.assertEqual(self.e.read(self.k)['events'],[])
 def test_actor_context_and_two_team_summaries_are_isolated(self):
  c,p,e=self.fixture(2);self.e.change_participation(self.k,'u02',team='B')
  self.e.action(self.k,'u01','a','TEAM_A_SENTINEL 讨论线索','talk');self.e.action(self.k,'u02','b','讨论另一件事','talk');self.finish(2)
  self.assertNotIn('TEAM_A_SENTINEL',json.dumps(self.e.view(self.k,'u02'),ensure_ascii=False))

 def test_model_extra_opportunities_are_bounded_without_world_writes(self):
  c,p,e=self.fixture(1);before=self.s.read(self.k)
  p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(opening='街口传来交谈声。',opportunities=['可以问清委托。','可以暂时离开。','可以观察附近。'])))
  self.assertIsNotNone(asyncio.run(c.plan_scene(e,self.k)));self.assertEqual(len(self.e.view(self.k,'u01')['scene_suggestions']['opportunities']),1);self.assertEqual(before,self.s.read(self.k))
