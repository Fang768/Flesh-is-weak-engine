import unittest,tempfile,sqlite3,json,concurrent.futures,threading,types,asyncio
from pathlib import Path
from unittest.mock import patch,AsyncMock
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_neon_save.store import SaveError
from astrbot_plugin_nexus_gm.engine import Engine,GMError
from astrbot_plugin_nexus_gm.semantic import SemanticController
class Control(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.save=SafeStore(self.root/'save');self.k='qq_official:test';self.save.init(self.k,'admin')
  def commit(op):
   p=self.save.propose(self.k,'admin',self.save.read(self.k)['revision'],op)
   for u in p['required']:self.save.consent(self.k,u,p['id'],p['digest'])
   self.save.approve(self.k,'admin',p['id'],p['digest'],True)
  self.commit=commit
  for cid in ('01','03'):
   commit(dict(kind='initialize',cid=cid,user='u'+cid,nickname='测试'))
   commit(dict(kind='status',cid=cid,value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全据点',team='A')))
  commit(dict(kind='world',field='scene_status',value='active',reason='隔离测试'))
  self.e=Engine(self.root/'gm.db',self.save,rand=lambda n:4);self.e.open(self.k,True);self.before=self.save.read(self.k)
 def pause(self,actor='u01',mid='pause1'):
  return self.e.natural(self.k,actor,mid,'【暂停】',intent_override='pause')
 def request(self):return self.e.request_resume(self.k,'u01')
 def consent_all(self,r):
  for actor in ('u01','u03'):self.e.consent_resume(self.k,actor,r['id'],r['digest'])
 def approve(self,r):return self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True)
 def test_pause_persists_and_requires_all_consents_plus_admin(self):
  self.pause();r=self.request()
  self.e=Engine(self.e.path,self.save)
  self.assertTrue(self.e.is_paused(self.k));self.assertEqual(r['id'],self.request()['id'])
  with self.assertRaises(GMError):self.approve(r)
  self.e.consent_resume(self.k,'u01',r['id'],r['digest'])
  with self.assertRaises(GMError):self.approve(r)
  with self.assertRaises(GMError):self.e.consent_resume(self.k,'admin',r['id'],r['digest'])
  with self.assertRaises(GMError):self.e.consent_resume(self.k,'u03',r['id'],'wrong')
  self.e.consent_resume(self.k,'u03',r['id'],r['digest'])
  with self.assertRaises(GMError):self.e.approve_resume(self.k,'u01',r['id'],r['digest'],False)
  self.approve(r);self.assertFalse(self.e.is_paused(self.k));self.assertEqual(self.save.read(self.k),self.before)
 def test_shared_gate_blocks_existing_neon_tools_and_old_approvals(self):
  p=self.save.propose(self.k,'ai',self.before['revision'],dict(kind='resource',cid='03',field='cash',delta=5,reason='核对'))
  self.save.consent(self.k,'u03',p['id'],p['digest']);self.pause()
  for fn in (lambda:self.save.roll(self.k,'u03','03','check',1,10),lambda:self.save.propose(self.k,'ai',self.before['revision'],dict(kind='resource',cid='03',field='cash',delta=5,reason='核对')),lambda:self.save.consent(self.k,'u03',p['id'],p['digest']),lambda:self.save.approve(self.k,'admin',p['id'],p['digest'],True)):
   with self.assertRaises(SaveError):fn()
  r=self.request();self.consent_all(r);self.approve(r)
  with self.assertRaisesRegex(SaveError,'旧提案失效'):self.save.approve(self.k,'admin',p['id'],p['digest'],True)
  self.assertEqual(self.save.read(self.k),self.before)
 def test_reject_expiry_and_new_pause_invalidate_consents(self):
  self.pause();r=self.request();self.consent_all(r)
  self.pause('u03','pause2')
  with self.assertRaises(GMError):self.approve(r)
  r2=self.request();self.assertNotEqual(r['id'],r2['id'])
  self.e.consent_resume(self.k,'u03',r2['id'],r2['digest'],False)
  with self.assertRaises(GMError):self.approve(r2)
  r3=self.request();self.consent_all(r3);self.e.clock=lambda:r3['expires']+1
  with self.assertRaises(GMError):self.approve(r3)
  self.assertTrue(self.e.is_paused(self.k))
 def test_simultaneous_duplicate_approval_releases_once(self):
  self.pause();r=self.request();self.consent_all(r);old=self.e.epoch(self.k)
  with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.approve(r),[1,2]))
  self.assertEqual(self.e.epoch(self.k),old+1);self.assertFalse(self.e.is_paused(self.k));self.assertEqual(self.save.read(self.k),self.before)
 def test_pause_racing_resume_always_wins(self):
  self.pause();r=self.request();self.consent_all(r);original=self.save.release_group
  def race(*args):self.pause('u03','new-pause');return original(*args)
  with patch.object(self.save,'release_group',side_effect=race):
   with self.assertRaises(Exception):self.approve(r)
  self.assertTrue(self.e.is_paused(self.k));self.assertEqual(self.save.read(self.k),self.before)
 def test_crash_before_gate_release_restart_retry_safe(self):
  self.pause();r=self.request();self.consent_all(r)
  with patch.object(self.save,'release_group',side_effect=RuntimeError('模拟崩溃')):
   with self.assertRaises(RuntimeError):self.approve(r)
  self.e=Engine(self.e.path,self.save);self.assertTrue(self.e.is_paused(self.k));self.approve(r)
  self.assertFalse(self.e.is_paused(self.k));self.assertEqual(self.save.read(self.k),self.before)
 def test_preserve_prepared_dice_restart_and_epoch_fence(self):
  epoch=self.e.epoch(self.k);p=self.e.natural(self.k,'u03','declaration','询问负责人',intent_override='交涉',expected_epoch=epoch)
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  with self.assertRaises(GMError):self.e.natural(self.k,'u03',p['mid'],'询问负责人',True,expected_epoch=epoch)
  self.e=Engine(self.e.path,self.save,rand=lambda n:4)
  result=self.e.natural(self.k,'u03',p['mid'],'询问负责人',True,expected_epoch=self.e.epoch(self.k))
  replay=self.e.natural(self.k,'u03',p['mid'],'询问负责人',True,expected_epoch=self.e.epoch(self.k))
  self.assertEqual(result['response'],replay['response'])
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1)
 def test_new_pause_same_text_not_deduped_after_resume(self):
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  self.pause(mid='fresh-pause');self.assertTrue(self.e.is_paused(self.k))
 def test_model_cancel_no_retry_or_dice(self):
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  controller=SemanticController(self.e,None)
  async def scenario():
   started=asyncio.Event()
   async def slow(**kwargs):started.set();await asyncio.Event().wait()
   provider=types.SimpleNamespace(get_model=lambda:'deepseek-test',text_chat=slow)
   work=asyncio.create_task(controller.model_call(provider,'公开动作','规则',key=self.k))
   await started.wait();self.pause(mid='cancel');controller.cancel_group(self.k)
   with self.assertRaises(asyncio.CancelledError):await work
  asyncio.run(scenario())
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),0)
 def test_unbound_cannot_consent_and_other_group_does_not_unpause(self):
  self.pause();r=self.request()
  with self.assertRaises(GMError):self.e.consent_resume(self.k,'intruder',r['id'],r['digest'])
  with self.assertRaises(GMError):self.e.request_resume('qq_official:other','u01')
  self.assertTrue(self.e.is_paused(self.k))
 def test_neon_startup_adopts_legacy_pause_without_snapshot_rewrite(self):
  self.pause();target=self.root/'astrbot_plugin_nexus_gm';target.mkdir()
  with sqlite3.connect(self.e.path) as source,sqlite3.connect(target/'rehearsal.sqlite3') as dest:source.backup(dest)
  with self.save._db() as c:c.execute('DELETE FROM neon_safety_gate')
  upgraded=SafeStore(self.save.data_dir)
  self.assertTrue(upgraded.gate_state(self.k)['paused']);self.assertEqual(upgraded.read(self.k),self.before)
 def test_recorded_result_not_rerolled_after_resume(self):
  p=self.e.natural(self.k,'u03','check','询问',intent_override='交涉');result=self.e.natural(self.k,'u03',p['mid'],'询问',True)
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  replay=self.e.cached(self.k,'u03',p['mid'],'询问')
  self.assertEqual(replay['response'],result['response'])
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1);self.assertEqual(s['done'],['03'])
 def test_gate_released_but_finalize_interrupted_can_restart(self):
  self.pause();r=self.request();self.consent_all(r);original=self.save.release_group
  def crash_after_gate(*args):original(*args);raise RuntimeError('模拟闸门释放后通信中断')
  # The recovery path recognizes the already released authorized epoch.
  with patch.object(self.save,'release_group',side_effect=crash_after_gate):self.approve(r)
  self.e=Engine(self.e.path,self.save);self.assertFalse(self.e.is_paused(self.k));self.approve(r)
  self.assertEqual(self.save.read(self.k),self.before)
 def test_registered_pending_player_can_pause_and_must_consent(self):
  self.commit(dict(kind='initialize',cid='02',user='u02',nickname='待入场玩家'))
  self.pause('u02','pending-player-pause')
  # A real revision change is not silently merged into a frozen window.
  with self.assertRaisesRegex(GMError,'真实存档已变化'):self.e.request_resume(self.k,'u02')
  self.assertTrue(self.e.is_paused(self.k))
 def test_resume_request_receipts_survive_new_pause(self):
  self.pause();r=self.request();self.e.consent_resume(self.k,'u01',r['id'],r['digest']);self.pause('u03','pause-again')
  with self.e.db() as c:
   stored=json.loads(c.execute('SELECT body FROM resume_requests WHERE k=? AND rid=?',(self.k,r['id'])).fetchone()[0]);self.assertEqual(stored['digest'],r['digest'])
   self.assertEqual(c.execute('SELECT count(*) FROM resume_consents WHERE rid=?',(r['id'],)).fetchone()[0],1)
  with self.assertRaises(GMError):self.e.consent_resume(self.k,'u03',r['id'],r['digest'])
 def test_shared_gate_pause_vs_approval_has_serial_order(self):
  p=self.save.propose(self.k,'ai',self.before['revision'],dict(kind='resource',cid='03',field='cash',delta=5,reason='核对'))
  self.save.consent(self.k,'u03',p['id'],p['digest']);barrier=threading.Barrier(2)
  def approve():
   barrier.wait()
   try:return self.save.approve(self.k,'admin',p['id'],p['digest'],True)
   except SaveError:return 'blocked'
  def pause():barrier.wait();return self.pause()
  with concurrent.futures.ThreadPoolExecutor(2) as pool:a=pool.submit(approve);b=pool.submit(pause);outcome=a.result();b.result()
  self.assertTrue(self.e.is_paused(self.k))
  current=self.save.read(self.k)
  self.assertIn(current['revision'],(self.before['revision'],self.before['revision']+1))
  if outcome=='blocked':self.assertEqual(current,self.before)
  else:self.assertEqual(current['snapshot']['roles']['03']['cash'],self.before['snapshot']['roles']['03']['cash']+5)

 def test_player_refusal_racing_authorization_keeps_gate_closed(self):
  self.pause();r=self.request();self.consent_all(r);original=self.save.release_group
  def refuse(*args):
   self.e.consent_resume(self.k,'u03',r['id'],r['digest'],False)
   return original(*args)
  with patch.object(self.save,'release_group',side_effect=refuse):
   with self.assertRaises(Exception):self.approve(r)
  self.assertTrue(self.e.is_paused(self.k));self.assertTrue(self.save.gate_state(self.k)['paused']);self.assertEqual(self.save.read(self.k),self.before)
 def test_gate_survives_pause_journal_failure(self):
  with patch.object(self.e,'write',side_effect=sqlite3.OperationalError('模拟日志写失败')):
   with self.assertRaises(sqlite3.OperationalError):self.pause()
  self.e=Engine(self.e.path,self.save);self.assertTrue(self.e.is_paused(self.k))
  with self.assertRaises(SaveError):self.save.roll(self.k,'u03','03','check',1,10)
  r=self.request();self.consent_all(r);self.approve(r);self.assertFalse(self.e.is_paused(self.k))
 def test_simultaneous_pause_and_consents(self):
  def pause(actor):return self.pause(actor,'simultaneous-'+actor)
  with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(pause,['u01','u03']))
  r=self.request();self.assertEqual(set(r['required']),{'u01','u03'})
  with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(lambda actor:self.e.consent_resume(self.k,actor,r['id'],r['digest']),['u01','u03']))
  self.assertTrue(self.e.is_paused(self.k));self.approve(r);self.assertFalse(self.e.is_paused(self.k))
 def test_duplicate_pause_platform_message_does_not_advance_epoch(self):
  self.pause();epoch=self.e.epoch(self.k);self.pause();self.assertEqual(self.e.epoch(self.k),epoch)

 def test_old_pause_redelivery_after_resume_does_not_claim_new_pause(self):
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  result=self.pause();self.assertIn('当前已按授权恢复',result['response']);self.assertFalse(self.e.is_paused(self.k))
 def test_prepared_action_recovered_after_long_pause_new_message(self):
  self.e.clock=lambda:1000
  p=self.e.natural(self.k,'u03','prepared','询问负责人',intent_override='交涉')
  self.pause();r=self.request();self.consent_all(r);self.approve(r)
  self.e.clock=lambda:2000
  pending=self.e.cached(self.k,'u03','new-id','询问负责人');self.assertEqual(pending['mid'],p['mid']);self.assertEqual(pending['phase'],'prepared')
  self.e.natural(self.k,'u03',pending['mid'],'询问负责人',True)
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1)

 def test_completed_resume_receipt_does_not_reset_later_actions(self):
  self.pause();r=self.request();self.consent_all(r);self.approve(r);epoch=self.e.epoch(self.k)
  self.e.natural(self.k,'u03','after-resume','结束本轮',intent_override='end')
  self.e.clock=lambda:r['expires']+1
  self.approve(r);self.assertEqual(self.e.epoch(self.k),epoch)
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(s['done'],['03']);self.assertFalse(self.e.is_paused(self.k))

 def test_resume_request_payload_tamper_cannot_remove_player_or_extend_expiry(self):
  self.pause();r=self.request()
  with self.e.db() as c:
   s=self.e.load(c,self.k);s['resume_request']['required']=['u01'];s['resume_request']['expires']+=3600;self.e.write(c,self.k,s,{'test_fault':'tamper'})
  with self.assertRaisesRegex(GMError,'完整性校验失败'):self.approve(r)
  self.assertTrue(self.e.is_paused(self.k));self.assertEqual(self.save.read(self.k),self.before)
