import asyncio,copy,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.enrollment_chat import EnrollmentChat,explicit_consent
from astrbot_plugin_nexus_gm.engine import Engine
from astrbot_plugin_nexus_gm.semantic import SemanticController

class Event:
 def __init__(self,text='',actor='p',admin=False,mid='m'):
  self.message_str=text;self.actor=actor;self.admin=admin;self.message_obj=types.SimpleNamespace(message_id=mid);self.unified_msg_origin='test';self.sent=[];self.fail=False
 def get_sender_id(self):return self.actor
 def is_admin(self):return self.admin
 def plain_result(self,t):return t
 async def send(self,t):
  if self.fail:raise OSError('simulated disconnect')
  self.sent.append(t)

class ChatEnrollment(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(Path(self.tmp.name)/'save');self.k='qq_official:test';self.s.init(self.k,'admin');self.chat=EnrollmentChat(self.s);self.n=0
 async def say(self,t,u='p',admin=False,mid=None):
  self.n+=1;e=Event(t,u,admin,mid or str(self.n));return await self.chat.handle(e,self.k,t)
 async def enable(self):await self.say('开启角色自动审核','admin',True)
 async def enroll(self,cid='03',u='p'):
  await self.say('我想玩'+cid+'号',u);return await self.say('就他了，我确认！',u)
 async def test_all_requested_examples_and_automatic_flow(self):
  await self.enable();before=self.s.read(self.k)
  preview=await self.say('我想玩03号扳手，希望他戴着橙色护目镜，平时有点毒舌，还喜欢收藏废旧芯片。')
  self.assertIn('程序将自动审核',preview);self.assertIn('剩余 18',preview);self.assertEqual(before,self.s.read(self.k))
  self.assertIn('认领成功',await self.say('就他了，我确认！'));self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['status'],'pending');self.assertIn('橙色护目镜',await self.say('我想看看自己的角色卡。'));self.assertIn('工具箱',await self.say('我的背包'))
 async def test_nickname_and_no_technical_identifiers(self):
  await self.enable();out=await self.say('我想认领03号扳手，我叫小猫。');self.assertIn('小猫',out)
  for term in ('revision','SHA256','"kind"','提案ID'):self.assertNotIn(term,out)
  await self.say('我确认');self.assertEqual(self.s.character_profile(self.k,'03')['details']['display_name'],'小猫')
 async def test_suggestion_and_availability_create_nothing(self):
  self.assertIn('02号虹膜',await self.say('我想当黑客，有什么角色推荐？'));self.assertIn('尚未认领',await self.say('03号有人选了吗？'))
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_enrollment_proposals').fetchone()[0],0)
 async def test_want_zero_short_expression(self):
  await self.enable();self.assertIn('01号零号',await self.say('我要零号！'));await self.say('确认');self.assertIn('01',self.s.read(self.k)['snapshot']['roles'])
 async def test_clarify_hair_then_claim_and_replay_question(self):
  await self.enable();q=await self.say('我决定玩03号，不过想改一下他的发型。',mid='ask');self.assertIn('具体改成什么',q)
  self.assertEqual(q,await self.say('我决定玩03号，不过想改一下他的发型。',mid='ask'))
  self.assertIn('短发',await self.say('橙色短发'));await self.say('我确认');self.assertEqual(self.s.character_profile(self.k,'03')['details']['appearance'],'橙色短发')
 async def test_owner_edit_reconfirms_restart_and_persists(self):
  await self.enable();await self.enroll();before=self.s.read(self.k)['snapshot'];await self.say('我想修改角色的性格设定。');self.chat=EnrollmentChat(SafeStore(Path(self.tmp.name)/'save'))
  self.assertIn('毒舌',await self.say('平时有点毒舌'));self.assertEqual(self.s.character_profile(self.k,'03')['details'],{});await self.say('确认修改');self.assertEqual(self.s.read(self.k)['snapshot'],before)
  self.chat=EnrollmentChat(SafeStore(Path(self.tmp.name)/'save'));self.assertIn('毒舌',await self.say('我的角色卡'))
 async def test_confirm_from_another_account_cannot_authorize(self):
  await self.enable();await self.say('我想玩扳手')
  with self.assertRaises(ValueError):await self.say('确认','fake')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 async def test_admin_text_does_not_confer_actual_permission(self):
  with self.assertRaises(ValueError):await self.say('开启角色自动审核','ordinary',False)
  await self.say('我想玩扳手');await self.say('确认')
  with self.assertRaises(ValueError):await self.say('批准03号入团','ordinary',False)
  self.assertFalse(self.s.enrollment_policy(self.k)['enabled'])
 async def test_default_manual_retains_approval_with_exact_application(self):
  await self.say('我想玩扳手');self.assertIn('仍待管理员',await self.say('确认'));self.assertIn('认领成功',await self.say('批准03号入团','admin',True))
 async def test_manual_competing_candidates_not_guessed(self):
  await self.say('我想玩扳手','a');await self.say('我想玩扳手','b');await self.say('确认','a');await self.say('确认','b')
  with self.assertRaises(ValueError):await self.say('批准03号入团','admin',True)
  p=next(x for x in self.s.enrollment_pending(self.k,'03') if x['owner']=='b');await self.say('批准03号入团，申请'+str(p['ticket']),'admin',True)
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['qq_user_id'],'b')
 async def test_same_text_new_confirm_and_disconnect_retries_never_recommit(self):
  await self.enable();await self.say('我想玩扳手');e=Event('确认',mid='confirm');e.fail=True
  with self.assertRaises(OSError):await self.chat.dispatch(e,self.k,'确认')
  before=self.s.read(self.k);e.fail=False;await self.chat.dispatch(e,self.k,'确认');self.assertIn('认领成功',e.sent[0]);self.assertIn('没有再次写入',await self.say('确认'));self.assertEqual(before,self.s.read(self.k))
 async def test_superseding_clarification_invalidates_older_preview(self):
  await self.enable();await self.say('我想玩扳手');await self.say('我决定玩03号，不过想改一下他的发型')
  with self.assertRaises(ValueError):await self.say('确认')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 async def test_model_cannot_supply_confirmation_identity_or_json_fields(self):
  self.assertFalse(explicit_consent('他说我确认'));self.assertFalse(explicit_consent('不确认'));self.assertFalse(explicit_consent('帮他确认'))
  for decision in [dict(intent='confirm',cid='03',diy={},question=''),dict(intent='claim',cid='03',diy={},question='',user='admin'),dict(intent='claim',cid='03',diy={'appearance':'从未说过的红头发'},question='')]:
   with self.subTest(decision=decision):
    self.chat.context=self.fake_context(decision)
    with self.assertRaises(ValueError):await self.chat.interpret(Event('我想玩扳手'),self.k,'我想玩扳手')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 def fake_context(self,decision,callback=None):
  async def chat(**kwargs):
   if callback:callback()
   return types.SimpleNamespace(completion_text=json.dumps(decision,ensure_ascii=False))
  p=types.SimpleNamespace(get_model=lambda:'deepseek',text_chat=chat)
  async def provider(origin):return p
  async def persona(name):return types.SimpleNamespace(system_prompt='TRPG_GM')
  return types.SimpleNamespace(get_using_provider_async=provider,persona_manager=types.SimpleNamespace(get_persona=persona))
 async def test_real_semantic_path_contract_with_stubbed_deepseek(self):
  text='我希望扮手穿橙色工装'  # Model cannot invent a role reference from a misspelling.
  self.chat.context=self.fake_context(dict(intent='claim',cid='03',diy={'appearance':'橙色工装'},question=''))
  with self.assertRaises(ValueError):await self.chat.interpret(Event(text),self.k,text)
  text='我想玩扳手，喜欢橙色工装'
  d=await self.chat.interpret(Event(text),self.k,text);self.assertEqual(d['diy'],{'appearance':'橙色工装'})
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 async def test_late_model_result_after_pause_is_discarded(self):
  self.chat.context=self.fake_context(dict(intent='claim',cid='03',diy={},question=''),lambda:self.s.pause_group(self.k,'p','pause'))
  with self.assertRaises(ValueError):await self.chat.interpret(Event(),self.k,'我想玩扳手')
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_enrollment_proposals').fetchone()[0],0)
 async def test_expired_confirm_not_silently_rebuilt(self):
  await self.enable();await self.say('我想玩扳手')
  with self.s._db() as c:c.execute('update neon_enrollment_proposals set expires=0')
  with self.assertRaises(ValueError):await self.say('确认')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 async def test_read_never_exposes_hidden_dossier_or_internal_equipment(self):
  await self.enable();await self.enroll();card=await self.say('我的角色卡');bag=await self.say('我的背包')
  for word in ('neon_v020','dossier_content','dossier_category_id','qquserid','qq_user_id'):self.assertNotIn(word,card+bag)
 async def test_new_auto_path_does_not_approve_task_or_activate(self):
  await self.enable();await self.enroll();before=self.s.read(self.k)
  self.assertIn('不会自动执行',await self.say('申请入场'));self.assertIn('不会自动执行',await self.say('申请任务结算'));self.assertEqual(before,self.s.read(self.k))
 async def test_diy_is_passed_to_future_narration_from_database(self):
  await self.enable();await self.say('我想玩03号扳手，希望他戴着橙色护目镜');await self.say('确认')
  activation=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],dict(kind='status',cid='03',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='隔离据点',team='A')))
  self.s.consent(self.k,'p',activation['id'],activation['digest']);self.s.approve(self.k,'admin',activation['id'],activation['digest'],True)
  world=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],dict(kind='world',field='scene_status',value='active',reason='隔离叙述接口测试'))
  self.s.consent(self.k,'p',world['id'],world['digest']);self.s.approve(self.k,'admin',world['id'],world['digest'],True)
  engine=Engine(Path(self.tmp.name)/'gm.sqlite3',self.s);engine.open(self.k,True);semantic=SemanticController(engine,None);captured={}
  async def call(**kwargs):
   captured.update(kwargs);return types.SimpleNamespace(completion_text=json.dumps(dict(description='橙色护目镜映着昏黄的光，你俯身查看终端。',facts_used=['result']),ensure_ascii=False))
  p=types.SimpleNamespace(text_chat=call,get_model=lambda:'deepseek');persona=types.SimpleNamespace(system_prompt='TRPG_GM')
  out=await semantic.narrate(p,persona,'查看终端','03：真实程序判定',key=self.k)
  self.assertIn('橙色护目镜',out);self.assertEqual(json.loads(captured['prompt'])['personal_diy']['appearance'],'戴着橙色护目镜')

 async def test_model_cannot_claim_success_in_clarification(self):
  self.chat.context=self.fake_context(dict(intent='clarify',cid='',diy={},question='已经保存成功'))
  with self.assertRaises(ValueError):await self.chat.interpret(Event(),self.k,'我想玩扳手')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 async def test_model_timeout_has_no_draft_or_consent(self):
  self.chat.context=self.fake_context(dict(intent='claim',cid='03',diy={},question=''))
  async def timed(**kwargs):raise asyncio.TimeoutError()
  provider=await self.chat.context.get_using_provider_async('test');provider.text_chat=timed
  with self.assertRaises(asyncio.TimeoutError):await self.chat.interpret(Event(),self.k,'我想玩扳手')
  with self.s._db() as c:
   self.assertEqual(c.execute('select count(*) from neon_enrollment_proposals').fetchone()[0],0);self.assertEqual(c.execute('select count(*) from neon_enrollment_consents').fetchone()[0],0)

 async def test_profile_edit_does_not_stale_existing_gm_window(self):
  await self.enable();await self.enroll()
  for payload in (dict(kind='status',cid='03',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='测试据点',team='A')),dict(kind='world',field='scene_status',value='active',reason='隔离测试')):
   p=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],payload);self.s.consent(self.k,'p',p['id'],p['digest']);self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  e=Engine(Path(self.tmp.name)/'gm.sqlite3',self.s);e.open(self.k,True);before=self.s.read(self.k)
  p=self.s.request_enrollment(self.k,'p','edit','03',{'appearance':'橙色短发'},'profile');self.s.confirm_enrollment(self.k,'p','edit-yes',p['id'],p['digest']);self.assertEqual(before,self.s.read(self.k))
  result=e.natural(self.k,'p','view','我来交涉',intent_override='交涉');self.assertEqual(result['phase'],'prepared')

 async def test_cancel_incomplete_diy_question_and_replay(self):
  await self.say('我决定玩03号，不过想改一下他的发型');result=await self.say('取消认领',mid='cancel');self.assertIn('已取消',result);self.assertEqual(result,await self.say('取消认领',mid='cancel'));self.assertIsNone(self.chat.dialogue(self.k,'p'))
 async def test_preview_delivery_lock_orders_confirmation_per_player(self):
  await self.enable();started=asyncio.Event();release=asyncio.Event()
  class SlowEvent(Event):
   async def send(inner,t):started.set();await release.wait();inner.sent.append(t)
  first=SlowEvent('我想玩扳手',mid='slow');yes=Event('确认',mid='queued');other=Event('我想玩零号','other',mid='other')
  a=asyncio.create_task(self.chat.dispatch(first,self.k,first.message_str));await started.wait();b=asyncio.create_task(self.chat.dispatch(yes,self.k,yes.message_str));await self.chat.dispatch(other,self.k,other.message_str)
  self.assertFalse(b.done());self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{});release.set();await asyncio.gather(a,b);self.assertIn('认领成功',yes.sent[0])

 async def test_old_commit_receipt_after_restore_never_claims_current_registration(self):
  await self.enable();before=self.s.read(self.k);await self.say('我想玩扳手');await self.say('确认',mid='old-commit')
  with self.s._db() as c:c.execute('update saves set snapshot=? where group_key=?',(json.dumps(before['snapshot'],ensure_ascii=False),self.k))
  result=await self.say('确认',mid='old-commit');self.assertIn('历史操作',result);self.assertNotIn('认领成功',result);self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})

 async def test_all_ten_names_support_short_selection(self):
  for cid,t in self.chat.templates.items():
   with self.subTest(cid=cid):
    result=await self.say('我要'+t['name']+'！',u='u'+cid);self.assertIn(cid+'号'+t['name'],result)
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})

 async def test_cosmetic_words_inside_story_action_do_not_hijack_gm_route(self):
  await self.enable();await self.enroll()
  self.assertIsNone(await self.say('我扶了扶橙色护目镜，调查终端'))
  self.assertIsNone(await self.say('我修改终端的接线，尝试排除故障'))

 async def test_roster_exact_qq_phrase_readonly_and_scoped(self):
  await self.enable();await self.enroll()
  before=self.s.read(self.k)
  with self.s._db() as c:
   counts={t:c.execute('select count(*) from '+t).fetchone()[0] for t in ('neon_enrollment_proposals','neon_enrollment_consents','audit','neon_enrollment_audit')}
  text='列出本群所有尚未被认领的角色，介绍职业、擅长方向和公开背景，不要泄露未知档案。'
  result=await self.say(text)
  self.assertIn('9张',result);self.assertNotIn('03号扳手',result);self.assertIn('02号虹膜',result);self.assertIn('公开出身',result)
  for cid,card in self.chat.templates.items():
   if cid!='03':self.assertIn(cid+'号'+card['name'],result)
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:
   for t,n in counts.items():self.assertEqual(c.execute('select count(*) from '+t).fetchone()[0],n)
  self.s.init('qq_official:other','admin')
  e=Event(text,mid='other-roster');other=await self.chat.handle(e,'qq_official:other',text)
  self.assertIn('10张',other);self.assertIn('03号扳手',other)
 async def test_roster_uses_public_allowlist_not_hidden_profile(self):
  await self.enable();await self.enroll()
  with self.s._db() as c:
   row=c.execute('select snapshot from saves where group_key=?',(self.k,)).fetchone();snap=json.loads(row[0]);snap['roles']['03']['dossier_content']='SECRET_SENTINEL';c.execute('update saves set snapshot=? where group_key=?',(json.dumps(snap),self.k))
  result=await self.say('有哪些可选角色？不要剧透')
  self.assertNotIn('SECRET_SENTINEL',result);self.assertNotIn('戴橙色',result)

 async def test_public_queries_without_opening_claim_create_nothing(self):
  before=self.s.read(self.k)
  for text in ('有什么角色可以选','介绍一下角色','角色'):
   out=self.chat.public_query(self.k,'p',text,False)
   self.assertIn('10张',out);self.assertIn('认领入口尚未开放',out)
  out=self.chat.public_query(self.k,'p','我想看看03号角色卡',False)
  self.assertIn('预设公开模板',out);self.assertIn('剩余 18',out)
  self.assertIn('还没有已登记',self.chat.public_query(self.k,'p','我的角色卡',False))
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:
   for t in ('neon_enrollment_proposals','neon_enrollment_consents','neon_character_profiles','neon_enrollment_policy'):
    self.assertEqual(c.execute('select count(*) from '+t).fetchone()[0],0)
 async def test_public_queries_do_not_steal_consent_or_write_intents(self):
  for text in ('确认','我想认领03号扳手','批准03号入团','取消认领','开启角色自动审核','修改03号性格','【暂停】查看角色','/neoncard 03'):
   self.assertIsNone(self.chat.public_query(self.k,'p',text,False),text)
