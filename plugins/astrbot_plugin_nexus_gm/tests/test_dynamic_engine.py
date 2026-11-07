import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.dynamic.engine import NarrativeEngine,NarrativeError,adaptation
class DynamicEngine(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=Path(self.tmp.name);self.s=SafeStore(p/'save');self.k='qq_official:isolated';self.s.init(self.k,'admin');self.e=NarrativeEngine(p/'narrative.sqlite3',self.s)
 def commit(self,p):
  for u in p['required']:self.s.consent(self.k,u,p['id'],p['digest'])
  return self.s.approve(self.k,'admin',p['id'],p['digest'],True)
 def propose(self,p):return self.s.propose(self.k,'fixture',self.s.read(self.k)['revision'],p)
 def players(self,n):
  self.commit(self.propose(dict(kind='world',field='scene_status',value='active',reason='隔离安全开场')))
  for i in range(1,n+1):
   cid=f'{i:02}';self.commit(self.propose(dict(kind='initialize',cid=cid,user='u'+cid,nickname='测试角色')));self.commit(self.propose(dict(kind='status',cid=cid,value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='隔离歌舞伎安全入场',team='A'))))
  self.e.prepare(self.k,'admin',True)
  for i in range(1,n+1):self.e.ready(self.k,'u'+f'{i:02}')
 def finish(self,n):
  p=self.e.checkpoint(self.k)
  self.assertEqual(p['status'],'pending')
  for i in range(1,n+1):self.e.consent(self.k,'u'+f'{i:02}')
  self.e.approve(self.k,'admin',True)
 def test_scale_1_2_5_10_no_numeric_inflation(self):
  for n in (1,2,5,10):
   plan=adaptation({str(i):{} for i in range(n)})
   self.assertEqual(plan['participants'],n);self.assertTrue(plan['noncombat_exit_required']);self.assertFalse(plan['change_numeric_stats']);self.assertLessEqual(plan['ordinary_enemy_cap'],4)
 def test_complete_three_player_window_requires_native_consent(self):
  self.players(3);before=self.s.read(self.k)
  for i in range(1,4):self.e.action(self.k,'u'+f'{i:02}',str(i),'先谈清条件','talk')
  self.e.checkpoint(self.k);self.assertEqual(before,self.s.read(self.k))
  with self.assertRaises(Exception):self.e.approve(self.k,'admin',True)
  self.e.consent(self.k,'u01')
  with self.assertRaises(Exception):self.e.consent(self.k,'outsider')
  with self.assertRaises(Exception):self.e.approve(self.k,'admin',True)
  self.e.consent(self.k,'u02');self.e.consent(self.k,'u03');self.e.approve(self.k,'admin',True)
  self.assertEqual(self.e.read(self.k)['round'],2);self.assertEqual(len(self.e.read(self.k)['summaries']),1)
 def test_decline_preserves_other_players_agency_and_world_facts(self):
  self.players(2);facts=copy.deepcopy(self.e.read(self.k)['facts']);self.e.action(self.k,'u01','a','我拒绝自己的委托','decline');self.e.action(self.k,'u02','b','我接受','accept');self.finish(2)
  s=self.e.read(self.k);self.assertIn('拒绝',s['choices']['01']);self.assertIn('接受',s['choices']['02']);self.assertEqual(facts,s['facts'])
 def test_no_advancement_while_someone_waits(self):
  self.players(2);self.e.action(self.k,'u01','a','问情况','talk');out=self.e.checkpoint(self.k);self.assertEqual(out['waiting'],['02']);self.assertEqual(self.e.read(self.k)['clock'],0)
 def test_duplicate_message_does_not_reroll(self):
  self.players(3)
  with patch('astrbot_plugin_neon_save.safe.secrets.randbelow',return_value=7):a=self.e.action(self.k,'u03','same','拿细铁丝挑锁','unlock','locker',True)
  b=self.e.action(self.k,'u03','same','拿细铁丝挑锁','unlock','locker',True);self.assertEqual(a,b)
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_v020_rolls').fetchone()[0],1)
  self.assertEqual(a['check']['total'],7+1+8+3)
 def test_dv_must_be_declared_and_unknown_method_preserves_turn(self):
  self.players(1)
  with self.assertRaises(NarrativeError):self.e.action(self.k,'u01','a','开锁','unlock','locker')
  with self.assertRaises(NarrativeError):self.e.action(self.k,'u01','b','未预设的复杂方案','improvise')
  self.assertEqual(self.e.read(self.k)['done'],[])
 def test_pause_and_crash_never_reroll_or_advance(self):
  self.players(1);self.s.pause_group(self.k,'u01','pause');before=self.e.read(self.k)
  with self.assertRaises(NarrativeError):self.e.action(self.k,'u01','a','问话','talk')
  self.assertEqual(before,self.e.read(self.k))
 def test_hidden_clues_not_in_starting_prompt(self):
  self.players(1);view=json.dumps(self.e.view(self.k,'u01'),ensure_ascii=False)
  for secret in ('03:17','Santo Domingo','伪装成六街帮','ZERO HOUR','家属联盟'):self.assertNotIn(secret,view)
 def test_teams_clue_gating_and_restart(self):
  self.players(3);self.e.change_participation(self.k,'u01',team='B')
  with patch('astrbot_plugin_neon_save.safe.secrets.randbelow',return_value=7):self.e.action(self.k,'u03','lock','细铁丝挑锁','unlock','locker',True)
  self.e.action(self.k,'u01','a','等待','end');self.e.action(self.k,'u02','b','等待','end');self.finish(3)
  self.e.action(self.k,'u03','read','查看芯片','read_chip','locker');self.e.action(self.k,'u01','c','等待','end');self.e.action(self.k,'u02','d','等待','end');self.finish(3)
  self.e=NarrativeEngine(self.e.path,self.s)
  self.assertIn('03:17',json.dumps(self.e.view(self.k,'u03'),ensure_ascii=False));self.assertNotIn('03:17',json.dumps(self.e.view(self.k,'u01'),ensure_ascii=False));self.assertEqual(self.e.read(self.k)['round'],3)
 def test_explicit_absence_and_return_do_not_reset_history(self):
  self.players(2);self.e.change_participation(self.k,'u02',True);self.e.action(self.k,'u01','a','问路','talk');self.finish(2);history=copy.deepcopy(self.e.read(self.k)['summaries']);self.e.change_participation(self.k,'u02',False);self.assertEqual(history,self.e.read(self.k)['summaries']);self.assertIn('02',self.e.view(self.k,'u01')['waiting'])
 def test_unauthorized_open_and_existing_session_not_overwritten(self):
  with self.assertRaises(NarrativeError):self.e.prepare(self.k,'outsider')
  self.players(1);before=self.e.read(self.k)
  with self.assertRaises(NarrativeError):self.e.prepare(self.k,'admin',True)
  self.assertEqual(before,self.e.read(self.k))
 def test_safe_boundary_newcomer_sync(self):
  self.players(1);self.commit(self.propose(dict(kind='initialize',cid='02',user='u02',nickname='新人')));self.commit(self.propose(dict(kind='status',cid='02',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全节点',team='A'))));self.e.reconcile(self.k,'admin',True);self.assertFalse(self.e.read(self.k)['started']);self.assertTrue(self.e.ready(self.k,'u02').startswith('大家'))
 def test_interrupted_roll_marker_stops_retry(self):
  self.players(1)
  with patch.object(self.s,'roll',side_effect=OSError('simulated crash')):
   with self.assertRaises(OSError):self.e.action(self.k,'u01','lost','开锁','unlock','locker',True)
  with patch.object(self.s,'roll') as tool:
   with self.assertRaises(NarrativeError):self.e.action(self.k,'u01','lost','开锁','unlock','locker',True)
   tool.assert_not_called()
 def test_failed_gate_cannot_be_farmed_by_new_message_or_other_player(self):
  self.players(3)
  with patch('astrbot_plugin_neon_save.safe.secrets.randbelow',return_value=0):self.e.action(self.k,'u01','failed','开锁','unlock','locker',True)
  self.e.action(self.k,'u02','a','等待','end');self.e.action(self.k,'u03','b','等待','end');self.finish(3)
  with self.assertRaises(NarrativeError):self.e.action(self.k,'u03','new','同样开锁','unlock','locker',True)
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_v020_rolls').fetchone()[0],1)
 def test_expired_checkpoint_rebuild_preserves_results_requires_new_consent(self):
  self.players(1);self.e.action(self.k,'u01','a','问话','talk');p=self.e.checkpoint(self.k)['proposal'];self.e.consent(self.k,'u01')
  with self.s._db() as c:c.execute('update neon_v020_proposals set expires=0 where id=?',(p['id'],))
  q=self.e.rebuild_checkpoint(self.k,'admin',True)['proposal'];self.assertNotEqual(p['id'],q['id']);self.assertEqual(len(self.e.read(self.k)['events']),1)
  with self.assertRaises(Exception):self.e.approve(self.k,'admin',True)
  self.e.consent(self.k,'u01');self.e.approve(self.k,'admin',True);self.assertEqual(self.e.read(self.k)['round'],2)
 def test_parallel_checkpoints_produce_only_one_native_ticket(self):
  from concurrent.futures import ThreadPoolExecutor
  self.players(1);self.e.action(self.k,'u01','a','问话','talk')
  def f(_):return self.e.checkpoint(self.k)['proposal']['id']
  with ThreadPoolExecutor(max_workers=3) as pool:ids=list(pool.map(f,range(3)))
  self.assertEqual(len(set(ids)),1)
 def test_buy_key_does_not_deduct_before_explicit_approval(self):
  self.players(1);before=self.s.read(self.k)['snapshot']['roles']['01']['cash']
  with patch('astrbot_plugin_neon_save.safe.secrets.randbelow',return_value=9):self.e.action(self.k,'u01','buy','交涉购买钥匙','buy_key','locker',True)
  p=self.e.checkpoint(self.k);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['01']['cash'],before)
  self.e.consent(self.k,'u01');self.e.approve(self.k,'admin',True);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['01']['cash'],before-100)
 def test_native_revision_change_does_not_discard_completed_choices(self):
  self.players(1);self.e.action(self.k,'u01','choice','拒绝委托','decline');events=copy.deepcopy(self.e.read(self.k)['events']);self.commit(self.propose(dict(kind='world',field='location',value='公开修订地点',reason='隔离并发修改')))
  with self.assertRaises(NarrativeError):self.e.checkpoint(self.k)
  self.assertEqual(events,self.e.read(self.k)['events'])
 def test_actual_multiwindow_sessions_1_2_5_10_players(self):
  original=(self.s,self.e)
  for n in (1,2,5,10):
   with self.subTest(players=n),tempfile.TemporaryDirectory() as tmp:
    self.s=SafeStore(Path(tmp)/'save');self.s.init(self.k,'admin');self.e=NarrativeEngine(Path(tmp)/'gm.sqlite3',self.s);self.players(n)
    if n>=2:
     for i in range(n//2+1,n+1):self.e.change_participation(self.k,'u'+f'{i:02}',team='B')
    roles=copy.deepcopy(self.s.read(self.k)['snapshot']['roles'])
    for window in range(3):
     for i in range(1,n+1):self.e.action(self.k,'u'+f'{i:02}',f'{window}:{i}','从自己的角度打听情况','talk')
     self.finish(n)
    self.e=NarrativeEngine(self.e.path,self.s)
    self.assertEqual(self.e.read(self.k)['round'],4);self.assertEqual(len(self.e.read(self.k)['summaries']),3);self.assertEqual(roles,self.s.read(self.k)['snapshot']['roles'])
  self.s,self.e=original
 def test_parallel_confirmed_sync_does_not_advance_twice(self):
  from concurrent.futures import ThreadPoolExecutor
  self.players(1);self.e.action(self.k,'u01','a','观察','observe');p=self.e.checkpoint(self.k)['proposal'];self.s.consent(self.k,'u01',p['id'],p['digest']);self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(lambda _:self.e.sync(self.k),range(3)))
  self.assertEqual(self.e.read(self.k)['round'],2);self.assertEqual(len(self.e.read(self.k)['summaries']),1)
 def test_old_analysis_cannot_execute_after_pause_epoch(self):
  self.players(1);old=self.s.gate_state(self.k)['epoch'];self.s.pause_group(self.k,'u01','pause');self.s.release_group(self.k,'isolated-authorized-resume-fixture',old+1)
  with self.assertRaises(NarrativeError):self.e.action(self.k,'u01','old','观察','observe',expected_round=1,expected_epoch=old)
  self.assertEqual(self.e.read(self.k)['done'],[])
 def test_unplanned_local_destination_can_persist_without_forcing_script(self):
  self.players(1);self.e.action(self.k,'u01','free','我先去附近修理铺问有没有别的活','move','free');self.finish(1)
  self.assertIn('附近修理铺',self.e.read(self.k)['roster']['01']['position']);self.assertFalse(self.e.read(self.k)['chip'])
 def test_unprescribed_camera_access_is_manual_not_free_clue(self):
  self.players(1);before=self.s.read(self.k);self.e.action(self.k,'u01','camera','申请核对监控：管理员已同意展示该时间段的记录','review_camera','camera');self.e.checkpoint(self.k)
  self.assertEqual(before,self.s.read(self.k));self.assertNotIn('伪装成六街帮',json.dumps(self.e.view(self.k,'u01'),ensure_ascii=False))
  self.e.consent(self.k,'u01')
  with self.assertRaises(NarrativeError):self.e.approve(self.k,'admin',True)
  self.e.approve(self.k,'admin',True,manual_ruling=True);self.assertIn('伪装成六街帮',json.dumps(self.e.view(self.k,'u01'),ensure_ascii=False))
