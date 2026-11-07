import unittest,tempfile,json,types,asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.conversation import Conversation,parse_reply
from astrbot_plugin_nexus_gm.voice import STYLE,guard_decision
class Voice(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.s=SafeStore(self.root/'save');self.k='qq_official:humor';self.s.init(self.k,'admin')
  self.p=types.SimpleNamespace(get_model=lambda:'deepseek-chat',text_chat=AsyncMock(return_value=types.SimpleNamespace(completion_text='{"kind":"chat","reply":"豆腐还分老嫩，这位估计是隔夜的。"}')))
  self.ctx=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=self.p),persona_manager=types.SimpleNamespace(get_persona=AsyncMock(return_value=types.SimpleNamespace(system_prompt='自然简洁。'))));self.chat=Conversation(self.root,self.s,self.ctx,TEMPLATES)
 def event(self,actor,mid):return types.SimpleNamespace(get_sender_id=lambda:actor,message_obj=types.SimpleNamespace(message_id=mid),unified_msg_origin='isolated')
 async def test_public_callback_memory_survives_restart_but_not_other_group(self):
  before=self.s.read(self.k);await self.chat.reply(self.event('a','1'),self.k,'我怀疑这保安脑子里装的是赛博豆腐')
  self.chat=Conversation(self.root,self.s,self.ctx,TEMPLATES);await self.chat.reply(self.event('b','2'),self.k,'刚才那个豆腐梗还记得吗')
  self.assertIn('赛博豆腐',self.p.text_chat.call_args.kwargs['prompt']);self.assertEqual(before,self.s.read(self.k));self.assertEqual(self.chat.public_banter_memory('qq_official:other','a'),[])
 async def test_personal_history_is_not_shared_as_public_banter(self):
  await self.chat.reply(self.event('a','1'),self.k,'PERSONAL_PRIVATE_SENTINEL 我今天很难过')
  await self.chat.reply(self.event('b','2'),self.k,'你好');self.assertNotIn('PERSONAL_PRIVATE_SENTINEL',self.p.text_chat.call_args.kwargs['prompt'])
 def test_private_anatomy_and_team_flavor_do_not_leak(self):
  self.chat.remember_scene(self.k,'a','1','赛博豆腐和私密设定','阴茎资料',public_joke=True);self.assertEqual(self.chat.public_banter_memory(self.k,'a'),[])
  self.chat.remember_scene(self.k,'a','2','A队的行动','TEAM_A_ONLY');self.assertIn('TEAM_A_ONLY',str(self.chat.public_banter_memory(self.k,'a')));self.assertNotIn('TEAM_A_ONLY',str(self.chat.public_banter_memory(self.k,'b')))
 def test_ambiguous_destruction_never_executes(self):
  d=guard_decision('我把车炸了算了',dict(mode='action',operation='talk',node='free',question=''));self.assertEqual(d['mode'],'clarify');self.assertEqual(d['operation'],'')
 def test_real_action_is_not_blocked_by_humor_style(self):
  d=dict(mode='action',operation='unlock',node='locker',question='');self.assertEqual(guard_decision('我认真用细铁丝拨动锁簧',d),d)
 def test_romantic_boast_not_relationship_commit(self):
  for mode in ('action','clarify'):
   d=guard_decision('我要把这个NPC泡到手',dict(mode=mode,operation='talk' if mode=='action' else '',node='contact',question=''));self.assertEqual(d['mode'],'chat')
 def test_style_does_not_relax_dice_or_database_guard(self):
  for raw in ('{"kind":"chat","reply":"1d10=10，检定成功"}','{"kind":"chat","reply":"已经为你保存"}'):
   with self.assertRaises(ValueError):parse_reply(raw)
  self.assertIn('死亡',STYLE);self.assertIn('不替玩家',STYLE)
 async def test_tone_context_and_npc_styles_reach_tool_free_model(self):
  await self.chat.reply(self.event('a','1'),self.k,'我很难受，别开玩笑了',extra_context=dict(confirmed_facts=['严肃场景']))
  call=self.p.text_chat.call_args.kwargs;self.assertNotIn('func_tool',call);self.assertEqual(call['thinking'],{'type':'disabled'});self.assertIn('冷幽默',call['prompt']);self.assertIn('严肃场景',call['prompt'])

class VoiceController(unittest.TestCase):
 def setUp(self):
  import test_dynamic_engine as support
  self.f=support.DynamicEngine();self.f.setUp();self.addCleanup(self.f.tmp.cleanup);self.f.players(1)
  self.p=types.SimpleNamespace(get_model=lambda:'deepseek-chat',text_chat=AsyncMock());self.ctx=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=self.p))
  from astrbot_plugin_nexus_gm.dynamic.controller import DynamicController
  self.c=DynamicController(self.f.e,self.ctx);self.event=types.SimpleNamespace(get_sender_id=lambda:'u01',unified_msg_origin='isolated')
 def test_empty_non_action_node_is_safe_not_customer_error(self):
  self.p.text_chat.return_value=types.SimpleNamespace(completion_text='{"mode":"chat","operation":"","node":"","question":""}')
  d=asyncio.run(self.c.interpret(self.event,self.f.k,'哥们今天必须装一波'));self.assertEqual(d['mode'],'chat');self.assertEqual(self.f.e.read(self.f.k)['events'],[])
 def test_wrong_npc_voice_is_not_sent(self):
  self.p.text_chat.return_value=types.SimpleNamespace(completion_text='{"text":"米拉扫了你一眼，叫你别闹。"}')
  self.assertIsNone(asyncio.run(self.c.narrate(self.event,self.f.k,'我对管理员开个玩笑',dict(check=None))))
  self.p.text_chat.return_value=types.SimpleNamespace(completion_text='{"text":"管理员抬了抬眼：先别跟纸较劲。"}')
  self.assertIsNotNone(asyncio.run(self.c.narrate(self.event,self.f.k,'我对管理员开个玩笑',dict(check=None))))

class ReadOnlyVoice(unittest.TestCase):
 def test_read_only_disclaimers_removed_not_character_facts(self):
  from astrbot_plugin_nexus_gm.voice import human_read_reply
  text='02号虹膜｜已出场。这里只查询资料，没有创建认领申请。'
  self.assertEqual(human_read_reply(text),'02号虹膜｜已出场。')
 def test_confirmation_text_is_preserved(self):
  from astrbot_plugin_nexus_gm.voice import human_read_reply
  self.assertEqual(human_read_reply('请本人核对后回复确认。'),'请本人核对后回复确认。')
