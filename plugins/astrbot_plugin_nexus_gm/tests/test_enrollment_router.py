import asyncio,importlib,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from test_enrollment_chat import Event

class ActualRouter(unittest.IsolatedAsyncioTestCase):
 async def test_initialized_plugin_routes_claim_and_auto_policy_without_llm(self):
  modules={n:types.ModuleType(n) for n in ('astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path')}
  class Filters:
   class EventMessageType:GROUP_MESSAGE=1
   def command(self,*a,**k):return lambda f:f
   def event_message_type(self,*a,**k):return lambda f:f
  class Star:
   def __init__(self,context):self.context=context
  modules['astrbot.api.event'].AstrMessageEvent=object;modules['astrbot.api.event'].filter=Filters();modules['astrbot.api.star'].Star=Star;modules['astrbot.api.star'].Context=object
  modules['data.plugins.astrbot_plugin_neon_save.safe']=types.SimpleNamespace(SafeStore=SafeStore,TEMPLATES=TEMPLATES)
  with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,modules):
   modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:tmp
   sys.modules.pop('astrbot_plugin_nexus_gm.main',None);mod=importlib.import_module('astrbot_plugin_nexus_gm.main');plugin=mod.NexusGM(object());key='qq_official:test';home='qq_official:HOME';plugin.allowfile.write_text(json.dumps([key]));plugin.enrollfile.write_text(json.dumps([key]));plugin.engine.save.init(key,'a');plugin.engine.save.init(home,'a');baseline=plugin.engine.save.read(home)
   async def send(text,u='p',admin=False,mid='m',group='test'):
    e=Event(text,u,admin,mid);e.stopped=False;e.get_platform_name=lambda:'qq_official';e.get_group_id=lambda:group;e.stop_event=lambda:setattr(e,'stopped',True)
    await plugin.natural_action(e);self.assertTrue(e.stopped);return e
   await send('开启角色自动审核','admin',True,'on');out=await send('我想玩03号扳手，我叫小猫','p',False,'claim');self.assertIn('小猫',out.sent[0]);self.assertEqual(plugin.engine.save.read(key)['revision'],1)
   withhold=await send('确认','other',False,'fake');self.assertIn('没有',withhold.sent[0]);out=await send('就他了，我确认！','p',False,'yes');self.assertIn('认领成功',out.sent[0]);self.assertIsNone(plugin.semantic)
   self.assertEqual(plugin.engine.save.read(key)['snapshot']['roles']['03']['status'],'pending');self.assertEqual(plugin.engine.save.character_profile(key,'03')['details']['display_name'],'小猫')
   home_reply=await send('我想玩03号扳手','p',False,'home',group='HOME');self.assertIn('尚未开放',home_reply.sent[0]);self.assertEqual(baseline,plugin.engine.save.read(home));self.assertFalse(plugin.engine.save.enrollment_policy(home)['enabled'])
   # HOME public browsing must work even while enrollment is explicitly closed.
   for n,words in enumerate(('有什么角色可以选','介绍一下角色','看看03号角色卡')):
    query=await send(words,'p',False,'home-readonly-'+str(n),group='HOME')
    self.assertNotIn('本群尚未开放程序化角色认领',query.sent[0]);self.assertIn('认领入口尚未开放',query.sent[0]);self.assertEqual(baseline,plugin.engine.save.read(home))
   self.assertFalse(plugin.engine.save.enrollment_policy(home)['enabled'])
   # Explicit isolated opt-in proves onboarding does not grant story controls.
   plugin.enrollfile.write_text(json.dumps([key,home]))
   await send('开启角色自动审核','admin',True,'home-on',group='HOME');await send('我想玩扳手','p',False,'home-claim',group='HOME');await send('确认','p',False,'home-confirm',group='HOME')
   self.assertEqual(plugin.engine.save.read(home)['snapshot']['roles']['03']['status'],'pending')
   with plugin.engine.db() as c:self.assertIsNone(c.execute('select 1 from sessions where k=?',(home,)).fetchone())
   before_greeting=plugin.engine.save.read(home)
   for n,words in enumerate(('你好','我去维修站问问情况')):
    reply=await send(words,'p',False,'greet-'+str(n),group='HOME')
    self.assertIn('没有触发游戏行动',reply.sent[0]);self.assertNotIn('白名单',reply.sent[0]);self.assertEqual(before_greeting,plugin.engine.save.read(home))
   self.assertIsNone(plugin.semantic)
   with plugin.engine.db() as c:self.assertIsNone(c.execute('select 1 from sessions where k=?',(home,)).fetchone())
   from unittest.mock import AsyncMock
   provider=types.SimpleNamespace(get_model=lambda:'deepseek-chat',text_chat=AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps({'kind':'chat','reply':'在呢，今天想聊点什么？'},ensure_ascii=False))))
   plugin.conversation.context=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=provider),persona_manager=types.SimpleNamespace(get_persona=AsyncMock(return_value=types.SimpleNamespace(system_prompt='自然对话'))))
   chat_before=plugin.engine.save.read(home)
   reply=await send('你好，我今天有点累','p',False,'real-chat-route',group='HOME')
   self.assertIn('今天想聊点什么',reply.sent[0]);self.assertEqual(chat_before,plugin.engine.save.read(home));self.assertIsNone(plugin.semantic)
   self.assertNotIn('func_tool',provider.text_chat.call_args.kwargs)
   provider.text_chat.return_value.completion_text=json.dumps({'kind':'action','reply':''})
   reply=await send('我掀起机壳检查线路','p',False,'prestart-action',group='HOME')
   self.assertIn('还没开场',reply.sent[0]);self.assertEqual(chat_before,plugin.engine.save.read(home))
   # Existing HOME chat uses the isolated read-only lane, never the stale default history.
   plugin.enrollfile.write_text(json.dumps([key]))
   provider.text_chat.return_value.completion_text=json.dumps({'kind':'chat','reply':'你好，我是NEXUS。'})
   closed_before=plugin.engine.save.read(home)
   reply=await send('hi','p',False,'home-readonly-chat',group='HOME')
   self.assertIn('我是NEXUS',reply.sent[0]);self.assertEqual(closed_before,plugin.engine.save.read(home))
   reply=await send('这个角色的性格很有趣吧','p',False,'home-role-discussion',group='HOME')
   self.assertIn('我是NEXUS',reply.sent[0]);self.assertEqual(closed_before,plugin.engine.save.read(home))
   self.assertNotIn(home,json.loads(plugin.enrollfile.read_text()));self.assertNotIn(home,json.loads(plugin.allowfile.read_text()))
   blocked=await send('我想认领02号','p',False,'home-still-closed',group='HOME')
   self.assertIn('尚未开放',blocked.sent[0]);self.assertEqual(closed_before,plugin.engine.save.read(home))
   count_before=provider.text_chat.await_count
   before_guide=plugin.engine.save.read(home)
   guide=await send('介绍一下世界观和基本玩法','p',False,'home-public-guide',group='HOME')
   self.assertIn('2077年秋',guide.sent[0]);self.assertIn('不会开始游戏',guide.sent[0])
   self.assertEqual(provider.text_chat.await_count,count_before);self.assertEqual(before_guide,plugin.engine.save.read(home))
   with plugin.engine.db() as c:self.assertIsNone(c.execute('select 1 from sessions where k=?',(home,)).fetchone())
   count_before=provider.text_chat.await_count
   origin_before=plugin.engine.save.read(home)
   for n,words in enumerate(('介绍每个出身','出身的地址相关的背景介绍','圣多明戈是什么地方')):
    reply=await send(words,'p',False,'home-public-origin-'+str(n),group='HOME')
    self.assertIn('圣多明戈',reply.sent[0]);self.assertNotIn('认领尚未开放',reply.sent[0])
   self.assertEqual(origin_before,plugin.engine.save.read(home));self.assertEqual(count_before,provider.text_chat.await_count)
   count_before=provider.text_chat.await_count;flow_before=plugin.engine.save.read(home)
   instructions=await send('请告诉我游戏流程','p',False,'home-player-flow',group='HOME')
   self.assertIn('不用先背完整个世界观',instructions.sent[0]);self.assertIn('已经登记成功',instructions.sent[0])
   self.assertEqual(flow_before,plugin.engine.save.read(home));self.assertEqual(count_before,provider.text_chat.await_count)
   # The actual initialized top-level route must launch aliases before any LLM.
   plugin.dynamic.allow.write_text(json.dumps([home]));count_before=provider.text_chat.await_count;other_before=plugin.engine.save.read(key)
   launched=await send('我们开始吧','p',True,'actual-main-start',group='HOME')
   self.assertIn('米拉',launched.sent[0]);self.assertEqual(plugin.engine.save.read(home)['snapshot']['world']['scene_status'],'active');self.assertEqual(plugin.engine.save.read(home)['snapshot']['roles']['03']['status'],'active');self.assertEqual(plugin.engine.save.read(key),other_before);self.assertEqual(provider.text_chat.await_count,count_before)
   revision=plugin.engine.save.read(home)['revision'];again=await send('现在开团','p',True,'actual-main-repeat',group='HOME');self.assertIn('已经在进行',again.sent[0]);self.assertEqual(plugin.engine.save.read(home)['revision'],revision)
   sys.modules.pop('astrbot_plugin_nexus_gm.main',None)
