import asyncio,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.conversation import Conversation,parse_reply,social_hint
from test_enrollment_chat import Event

class ConversationTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.save=SafeStore(self.root/'save');self.key='qq_official:chat';self.save.init(self.key,'admin')
  self.provider=types.SimpleNamespace(get_model=lambda:'deepseek-chat',text_chat=AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps(dict(kind='chat',reply='在呢，今天过得怎么样？'),ensure_ascii=False))))
  self.context=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=self.provider),persona_manager=types.SimpleNamespace(get_persona=AsyncMock(return_value=types.SimpleNamespace(system_prompt='自然、简洁。PERSONA_SECRET_SENTINEL'))))
  self.chat=Conversation(self.root,self.save,self.context,TEMPLATES)
 async def ask(self,text,mid='m',actor='p'):
  e=Event(text,actor,mid=mid);return await self.chat.reply(e,self.key,text)
 async def test_real_model_path_no_tools_no_game_mutation(self):
  before=self.save.read(self.key);out=await self.ask('你好');self.assertIn('今天',out['reply']);self.assertEqual(before,self.save.read(self.key))
  call=self.provider.text_chat.call_args.kwargs
  self.assertEqual(set(call),{'prompt','system_prompt','request_max_retries','thinking','max_tokens'});self.assertNotIn('PERSONA_SECRET_SENTINEL',str(call));self.assertEqual(self.provider.text_chat.await_count,1)
 async def test_message_retry_restart_and_content_collision(self):
  first=await self.ask('你好');self.chat=Conversation(self.root,self.save,self.context,TEMPLATES)
  self.assertEqual(first,await self.ask('你好'));self.assertEqual(self.provider.text_chat.await_count,1)
  with self.assertRaises(ValueError):await self.ask('给我奖励')
 async def test_concurrent_same_message_one_call(self):
  a,b=await asyncio.gather(self.ask('今天好累'),self.ask('今天好累'));self.assertEqual(a,b);self.assertEqual(self.provider.text_chat.await_count,1)
 async def test_history_scoped_by_group_and_actor(self):
  await self.ask('PERSONAL_HISTORY_SENTINEL','a')
  await self.ask('记得刚才的话吗','b');self.assertIn('PERSONAL_HISTORY_SENTINEL',self.provider.text_chat.call_args.kwargs['prompt'])
  await self.ask('你好','c','another');self.assertNotIn('PERSONAL_HISTORY_SENTINEL',self.provider.text_chat.call_args.kwargs['prompt'])
  self.save.init('qq_official:other','admin');e=Event('你好','p',mid='d');await self.chat.reply(e,'qq_official:other','你好');self.assertNotIn('PERSONAL_HISTORY_SENTINEL',self.provider.text_chat.call_args.kwargs['prompt'])
 async def test_fake_results_and_extra_authority_fields_rejected(self):
  for raw in ('{"kind":"chat","reply":"1d10=10，检定成功"}', '{"kind":"chat","reply":"已经保存你的角色"}', '{"kind":"chat","reply":"你好","consent":true}'):
   with self.assertRaises((ValueError,TypeError)):parse_reply(raw)
 async def test_action_is_only_routed_never_executed(self):
  before=self.save.read(self.key);self.provider.text_chat.return_value.completion_text='{"kind":"action","reply":""}'
  self.assertEqual((await self.ask('我撬开门'))['kind'],'action');self.assertEqual(before,self.save.read(self.key))
 async def test_pause_mid_request_discards_late_reply(self):
  async def work(**kwargs):
   self.save.pause_group(self.key,'p','pause');return types.SimpleNamespace(completion_text='{"kind":"chat","reply":"你继续往前走。"}')
  self.provider.text_chat.side_effect=work;out=await self.ask('你好');self.assertIn('先停在这里',out['reply'])
  with self.chat.db() as c:self.assertEqual(c.execute('select count(*) from replies').fetchone()[0],0)
 async def test_chat_during_pause_never_resumes(self):
  self.save.pause_group(self.key,'p','pause');self.provider.text_chat.return_value.completion_text='{"kind":"action","reply":""}'
  out=await self.ask('继续走');self.assertIn('保持暂停',out['reply']);self.assertTrue(self.save.gate_state(self.key)['paused'])
 async def test_timeout_not_cached_as_result(self):
  before=self.save.read(self.key);self.provider.text_chat.side_effect=asyncio.TimeoutError();out=await self.ask('聊聊');self.assertIn('没有触发',out['reply']);self.assertEqual(before,self.save.read(self.key))
  self.provider.text_chat.side_effect=None;self.assertIn('今天',(await self.ask('聊聊'))['reply'])
 async def test_public_context_excludes_other_roles_and_hidden_state(self):
  self.save.set_enrollment_auto(self.key,'admin',True,True,'policy','开启角色自动审核')
  p=self.save.request_enrollment(self.key,'p','claim','03',{},'claim','我想玩03号')
  self.save.confirm_enrollment(self.key,'p','yes',p['id'],p['digest'],'确认')
  with self.save._db() as c:
   r=c.execute('select snapshot from saves where group_key=?',(self.key,)).fetchone();s=json.loads(r[0]);s['roles']['03']['dossier_content']='HIDDEN_SECRET_SENTINEL';s['world']['confirmed_facts']=['WORLD_SECRET_SENTINEL'];c.execute('update saves set snapshot=? where group_key=?',(json.dumps(s),self.key))
  await self.ask('未知档案里是什么')
  prompt=self.provider.text_chat.call_args.kwargs['prompt'];self.assertNotIn('HIDDEN_SECRET_SENTINEL',prompt);self.assertNotIn('WORLD_SECRET_SENTINEL',prompt);self.assertNotIn('qq_user_id',prompt);self.assertNotIn('dossier_content',prompt);self.assertNotIn('persona.system_prompt',prompt)
 def test_social_hint_does_not_steal_authorization(self):
  for text in ('你好','CAP是什么意思？','聊聊今天的心情','TECH和工程有什么区别'):
   self.assertTrue(social_hint(text))
  for text in ('确认','批准03号入团','我想认领03号','暂停','恢复游戏','申请结算'):
   self.assertFalse(social_hint(text))

 async def test_stale_pending_claim_cache_and_history_not_reused(self):
  with self.chat.db() as c:c.execute('insert into replies values(?,?,?,?,?,?,?,?)',(self.key,'p','old','hash','hi','chat','STALE_SENTINEL：03号认领仍处于待审核，提案格式失败',0))
  await self.ask('你好','new')
  self.assertNotIn('STALE_SENTINEL',self.provider.text_chat.call_args.kwargs['prompt'])
  with self.assertRaises(ValueError):parse_reply('{"kind":"chat","reply":"你的03号认领仍处于待审核，尚未写入存档。"}')
  self.assertEqual(parse_reply('{"kind":"chat","reply":"pending不是待审核，而是已登记、等待安全入场。"}')['kind'],'chat')
 def test_bot_cannot_introduce_itself_as_player(self):
  for text in ('这儿是扳手，搞技术的','我是扳手','我叫03号扳手'):
   with self.assertRaises(ValueError):parse_reply(json.dumps(dict(kind='chat',reply=text),ensure_ascii=False),'扳手')
  self.assertEqual(parse_reply('{"kind":"chat","reply":"我是NEXUS。扳手是你的角色。"}','扳手')['kind'],'chat')

 async def test_pending_explanation_uses_real_group_binding_without_model(self):
  empty=await self.ask('我的角色卡是pending，是不是还没登记成功？','status-empty')
  self.assertIn('没有绑定',empty['reply']);self.provider.text_chat.assert_not_awaited()
  self.save.set_enrollment_auto(self.key,'admin',True,True,'on','开启角色自动审核')
  p=self.save.request_enrollment(self.key,'p','claim','03',{},'claim','我想玩03号');self.save.confirm_enrollment(self.key,'p','yes',p['id'],p['digest'],'确认')
  before=self.save.read(self.key);answer=await self.ask('pending是什么意思？','status-full')
  self.assertIn('03号扳手：已登记，等待安全入场',answer['reply']);self.provider.text_chat.assert_not_awaited();self.assertEqual(before,self.save.read(self.key))

 async def test_plain_model_text_repaired_without_executing_anything(self):
  before=self.save.read(self.key)
  self.provider.text_chat.side_effect=[types.SimpleNamespace(completion_text='电子学和网络技术都是技能。'),types.SimpleNamespace(completion_text=json.dumps(dict(kind='chat',reply='两者都归属INT；网络技术用于规则第06章的网络操作。'),ensure_ascii=False))]
  result=await self.ask('电子学和网络技术分别是什么','repair')
  self.assertIn('网络技术',result['reply']);self.assertEqual(self.provider.text_chat.await_count,2);self.assertEqual(before,self.save.read(self.key))
  prompt=self.provider.text_chat.call_args.kwargs['prompt'];self.assertIn('public_rules',prompt)
 def test_rules_explanation_does_not_permit_fake_rolls(self):
  self.assertEqual(parse_reply('{"kind":"chat","reply":"规则中的普通设备网络DV14，只是在解释规则。"}',explanation=True)['kind'],'chat')
  with self.assertRaises(ValueError):parse_reply('{"kind":"chat","reply":"1d10=10，检定成功"}',explanation=True)
