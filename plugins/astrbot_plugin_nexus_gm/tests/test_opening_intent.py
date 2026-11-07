import unittest,types,asyncio,json,concurrent.futures,sys
from unittest.mock import patch
import test_simple_open as support
from astrbot_plugin_nexus_gm.opening_intent import opening_intent
from astrbot_plugin_nexus_gm.simple_open import INTRO
class Intent(unittest.TestCase):
 def test_positive_language_variations(self):
  for text in ('现在开团','开始第一幕','我们开始吧','好，我们现在开始吧！','现在就正式开团吧','受不了啦，启动！','开始玩','不等人了，开始跑团','请帮忙开始第一幕'):
   self.assertEqual(opening_intent(text),'start',text)
 def test_questions_are_read_only(self):
  for text in ('什么时候开场','啥时候能开始啊，选了几天角色了','非要人齐吗不是说了可以随时开始吗','现在可以开团吗？','我们开始吧？','开团条件缺什么'):
   self.assertEqual(opening_intent(text),'status',text)
 def test_negation_quotation_and_unrelated_action_never_start(self):
  for text in ('不要开始第一幕','先不开始跑团','他说现在开团','如果我们开始吧','“我们开始吧”是什么意思','我开始开锁','开始攻击保安','开始聊天吧','我用开团当暗号，然后偷钱包'):
   self.assertIsNone(opening_intent(text),text)
class FormalStart(unittest.TestCase):
 def setUp(self):
  self.f=support.Opening();self.f.setUp();self.addCleanup(self.f.tmp.cleanup);self.s=self.f.s;self.o=self.f.o;self.k=self.f.k;self.d=self.f.d
 def event(self,text,actor='02',admin=True,mid='alias'):
  ev=types.SimpleNamespace(get_sender_id=lambda:actor,is_admin=lambda:admin,message_obj=types.SimpleNamespace(message_id=mid),stop_event=lambda:None,plain_result=lambda t:t,sent=[])
  async def send(t):ev.sent.append(t)
  ev.send=send;return ev
 def test_alias_executes_real_native_commit_not_only_chat(self):
  ev=self.event('现在开团');before=self.s.read(self.k)
  self.assertTrue(asyncio.run(self.o.handle(ev,self.k,'现在开团')));after=self.s.read(self.k);self.assertEqual(after['revision'],before['revision']+1);self.assertEqual(after['snapshot']['world']['scene_status'],'active');self.assertEqual(after['snapshot']['roles']['02']['status'],'active');self.assertEqual(after['snapshot']['roles']['05']['status'],'pending');self.assertIn('米拉',ev.sent[0])
  with self.s._db() as c:
   p=c.execute("select * from neon_v020_proposals where group_key=? and status='committed' order by rowid desc limit 1",(self.k,)).fetchone();self.assertEqual(p['actor'],'02');self.assertEqual(c.execute('select actor from neon_v020_consents where pid=?',(p['id'],)).fetchall()[0][0],'02');self.assertEqual(c.execute('select count(*) from neon_v020_rolls').fetchone()[0],0)
 def test_unapproved_player_cannot_use_natural_alias_or_fake_admin_text(self):
  (self.o.directory/'simple_open_authorization.json').unlink();before=self.s.read(self.k)
  for text in ('现在开团','开始第一幕','我们开始吧'):
   ev=self.event(text,admin=False);self.assertTrue(asyncio.run(self.o.handle(ev,self.k,text)));self.assertNotIn(INTRO,ev.sent);self.assertIn('授权',ev.sent[0])
  self.assertEqual(before,self.s.read(self.k))
 def test_scoped_preapproved_self_start_remains_supported(self):
  ev=self.event('我们开始吧',admin=False);asyncio.run(self.o.handle(ev,self.k,'我们开始吧'));self.assertEqual(self.s.read(self.k)['snapshot']['world']['scene_status'],'active');self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
 def test_status_question_uses_preflight_not_model_and_writes_nothing(self):
  before=self.s.read(self.k);ev=self.event('什么时候开场');asyncio.run(self.o.handle(ev,self.k,'什么时候开场'));self.assertIn('不用等全员',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))
 def test_paused_condition_is_specific_and_never_unpauses(self):
  self.s.pause_group(self.k,'02','pause');before=self.s.read(self.k);ev=self.event('开始第一幕');asyncio.run(self.o.handle(ev,self.k,'开始第一幕'));self.assertIn('暂停',ev.sent[0]);self.assertIn('恢复',ev.sent[0]);self.assertEqual(before,self.s.read(self.k));self.assertTrue(self.s.gate_state(self.k)['paused'])
 def test_absent_or_downed_actor_reports_specific_condition(self):
  p=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],dict(kind='status',cid='02',value='away'));self.s.consent(self.k,'02',p['id'],p['digest']);self.s.approve(self.k,'admin',p['id'],p['digest'],True);before=self.s.read(self.k)
  with self.assertRaisesRegex(ValueError,'回归'):self.o.start(self.k,'02','away',True)
  self.assertEqual(before,self.s.read(self.k))
 def test_missing_group_permission_or_store_is_explained(self):
  before=self.s.read(self.k);self.d.allow.write_text('[]');ev=self.event('开始第一幕');asyncio.run(self.o.handle(ev,self.k,'开始第一幕'));self.assertIn('主持入口',ev.sent[0]);self.assertEqual(before,self.s.read(self.k));self.assertIn('有效存档',self.o.status_reply('qq_official:no-store','02',True))
 def test_no_fake_intro_if_approval_has_no_database_receipt(self):
  before=self.s.read(self.k)
  with patch.object(self.s,'approve',return_value='AI文字：成功'):
   ev=self.event('现在开团');asyncio.run(self.o.handle(ev,self.k,'现在开团'))
  self.assertNotIn(INTRO,ev.sent);self.assertEqual(before,self.s.read(self.k))
 def test_parallel_aliases_cannot_open_twice(self):
  before=self.s.read(self.k)
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda n:self.o.start(self.k,'02',str(n),True),range(4)))
  self.assertEqual(self.s.read(self.k)['revision'],before['revision']+1)
 def test_multiple_ready_players_join_without_reopening(self):
  self.o.start(self.k,'02','first',True);before=self.d.get().read(self.k);self.o.start(self.k,'05','later',True);after=self.d.get().read(self.k);self.assertEqual(after['round'],before['round']);self.assertEqual(after['facts'],before['facts']);self.assertEqual(set(after['roster']),{'02','05'});self.assertTrue(after['started'])
 def test_single_player_limits_and_noncombat_route_are_program_data(self):
  self.o.start(self.k,'02','solo',True);view=self.d.get().view(self.k,'02');self.assertEqual(view['adaptation']['ordinary_enemy_cap'],1);self.assertTrue(view['adaptation']['noncombat_exit_required'])
  from astrbot_plugin_nexus_gm.dynamic.engine import OUTLINE
  self.assertIn('buy_key',OUTLINE['gates']);self.assertIn('unlock',OUTLINE['gates'])
 def test_old_gateway_message_is_not_promoted_to_new_authorization(self):
  import time
  from datetime import datetime,timezone
  (self.o.directory/'opening_entry_version.json').write_text(json.dumps(dict(activated_at=time.time())))
  ev=self.event('现在开团');ev.message_obj.raw_message=types.SimpleNamespace(timestamp=datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat());before=self.s.read(self.k);asyncio.run(self.o.handle(ev,self.k,'现在开团'));self.assertIn('旧消息',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))
 def test_previously_chatted_message_id_cannot_become_start(self):
  import sqlite3
  with sqlite3.connect(self.o.directory/'conversation.sqlite3') as c:
   c.execute('create table replies(k,actor,mid)');c.execute('insert into replies values(?,?,?)',(self.k,'02','old'))
  ev=self.event('现在开团',mid='old');before=self.s.read(self.k);asyncio.run(self.o.handle(ev,self.k,'现在开团'));self.assertIn('旧消息',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))
 def test_repeated_platform_message_is_checked_against_database_not_reexecuted(self):
  ev=self.event('我们开始吧');asyncio.run(self.o.handle(ev,self.k,'我们开始吧'));rev=self.s.read(self.k)['revision'];again=self.event('我们开始吧');asyncio.run(self.o.handle(again,self.k,'我们开始吧'));self.assertEqual(rev,self.s.read(self.k)['revision']);self.assertIn('不重复执行',again.sent[0])
 def test_one_through_ten_actual_arrivals_without_minimum_group_size(self):
  for count in (1,2,5,10):
   key='qq_official:count-'+str(count);self.s.init(key,'admin');self.d.allow.write_text(json.dumps([key]));(self.o.directory/'simple_open_authorization.json').write_text(json.dumps(dict(enabled=True,groups=[key],scope='voluntary_noncombat_open_and_pending_join')))
   for i in range(1,count+1):
    cid=f'{i:02}';p=self.s.propose(key,'admin',self.s.read(key)['revision'],dict(kind='initialize',cid=cid,user=cid,nickname=cid));self.s.consent(key,cid,p['id'],p['digest']);self.s.approve(key,'admin',p['id'],p['digest'],True)
   self.o.start(key,'01','first',True)
   for i in range(2,count+1):self.o.arrive(key,f'{i:02}',str(i))
   state=self.d.get().read(key);self.assertEqual(len(state['roster']),count);self.assertTrue(state['started']);self.assertEqual(self.d.get().view(key,'01')['adaptation']['participants'],count)
 def test_fresh_qq_original_timestamp_can_start_after_cutover(self):
  import time
  from datetime import datetime,timezone
  (self.o.directory/'opening_entry_version.json').write_text(json.dumps(dict(activated_at=time.time()-2)))
  ev=self.event('现在开团');ev.message_obj.raw_message=types.SimpleNamespace(timestamp=datetime.now(timezone.utc).isoformat());asyncio.run(self.o.handle(ev,self.k,'现在开团'));self.assertIn(INTRO,ev.sent);self.assertEqual(self.s.read(self.k)['snapshot']['world']['scene_status'],'active')
 def test_no_original_qq_time_fails_closed_with_specific_reason(self):
  import time
  (self.o.directory/'opening_entry_version.json').write_text(json.dumps(dict(activated_at=time.time()-2)))
  before=self.s.read(self.k);ev=self.event('现在开团');asyncio.run(self.o.handle(ev,self.k,'现在开团'));self.assertIn('原始消息时间',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))
 def test_incomplete_action_and_downed_role_are_specific(self):
  with self.s._db() as c:
   r=c.execute('select snapshot from saves where group_key=?',(self.k,)).fetchone();state=json.loads(r[0]);state['world']['pending_actions']=[dict(kind='unfinished')];state['roles']['02']['hp']=0;c.execute('update saves set snapshot=? where group_key=?',(json.dumps(state),self.k))
  before=self.s.read(self.k);ev=self.event('现在开团');asyncio.run(self.o.handle(ev,self.k,'现在开团'));self.assertIn('未处理的行动',ev.sent[0]);self.assertIn('倒地',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))
 def test_message_content_collision_cannot_execute_second_operation(self):
  ev=self.event('现在开团');asyncio.run(self.o.handle(ev,self.k,'现在开团'));before=self.s.read(self.k);ev=self.event('开始第一幕');asyncio.run(self.o.handle(ev,self.k,'开始第一幕'));self.assertIn('内容不同',ev.sent[0]);self.assertEqual(before,self.s.read(self.k))

 def test_running_status_does_not_tell_unadmitted_player_to_act(self):
  self.o.start(self.k,'02','first',True);reply=self.o.status_reply(self.k,'05');self.assertIn('你还没入场',reply);self.assertIn('我来了',reply);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
 def test_running_downed_status_does_not_offer_reopen_healing(self):
  self.o.start(self.k,'02','first',True)
  with self.s._db() as c:
   row=c.execute('select snapshot from saves where group_key=?',(self.k,)).fetchone();state=json.loads(row[0]);state['roles']['02']['hp']=0;c.execute('update saves set snapshot=? where group_key=?',(json.dumps(state),self.k))
  self.assertIn('倒地',self.o.status_reply(self.k,'02'));self.assertIn('回血',self.o.status_reply(self.k,'02'))

 def test_pause_epoch_invalidates_pre_pause_queued_arrival(self):
  self.o.start(self.k,'02','first',True);e=self.d.get();e.action(self.k,'02','talk','先交谈','talk');self.o.arrive(self.k,'05','old-arrival')
  self.s.pause_group(self.k,'02','pause');epoch=self.s.gate_state(self.k)['epoch'];self.s.release_group(self.k,'isolated-trusted-restore',epoch)
  e.checkpoint(self.k);e.consent(self.k,'02');e.approve(self.k,'admin',True);self.assertEqual(self.o.flush(self.k),[]);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
