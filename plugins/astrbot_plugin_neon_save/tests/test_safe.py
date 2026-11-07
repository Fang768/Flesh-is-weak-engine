import concurrent.futures
import copy
import json
import sqlite3
import tempfile
import unittest
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_neon_save.store import NeonStore,SaveError,canonical

class SafeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=SafeStore(self.tmp.name);self.k='qq_official:g';self.s.init(self.k,'admin')
 def tearDown(self):self.tmp.cleanup()
 def rev(self):return self.s.read(self.k)['revision']
 def propose(self,p):
  if p.get('kind')=='status' and p.get('value')=='active':p=dict(p,entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全据点',team='A'))
  if p.get('kind')=='mission':p=dict(p,completed=True,survived=True,actually_present=True,completion_evidence='公开任务完成记录')
  return self.s.propose(self.k,'ai',self.rev(),p)
 def commit(self,p):
  for u in p['required']:self.s.consent(self.k,u,p['id'],p['digest'])
  return self.s.approve(self.k,'admin',p['id'],p['digest'],True)
 def setup_role(self,cid='03'):
  if self.s.read(self.k)['snapshot']['world']['scene_status']!='active':self.commit(self.propose(dict(kind='world',field='scene_status',value='active',reason='隔离开场')))

  self.commit(self.propose(dict(kind='initialize',cid=cid,user='u'+cid,nickname='玩家')))
  self.commit(self.propose(dict(kind='status',cid=cid,value='active')))
 def test_ten_templates(self):
  for cid,t in TEMPLATES.items():
   self.commit(self.propose(dict(kind='initialize',cid=cid,user='u'+cid,nickname=t['name'])))
   r=self.s.read(self.k)['snapshot']['roles'][cid]
   self.assertEqual(r['hp'],20+4*t['attributes']['BODY']);self.assertEqual(r['ram'],14 if cid=='02' else 0)
   self.assertTrue(sum(c['capacity'] for c in t['cyberware'])<=t['cap']);self.assertEqual(r['dossier_content'],'尚未生成')
  self.assertIsNone(self.s.read(self.k)['snapshot']['world']['team_cash'])
 def test_authorization_and_immutable_proposal(self):
  p=self.propose(dict(kind='initialize',cid='03',user='u03',nickname='n'))
  for args in [('admin',p['digest']),('u03','wrong')]:
   with self.assertRaises(SaveError):self.s.consent(self.k,args[0],p['id'],args[1])
  with self.assertRaises(SaveError):self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  self.s.consent(self.k,'u03',p['id'],p['digest'])
  with self.assertRaises(SaveError):self.s.approve(self.k,'u03',p['id'],p['digest'])
  self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  with self.assertRaises(SaveError):self.s.approve(self.k,'admin',p['id'],p['digest'],True)
 def test_concurrency_conflict(self):
  a=self.propose(dict(kind='initialize',cid='01',user='u1',nickname='a'));b=self.propose(dict(kind='initialize',cid='02',user='u2',nickname='b'))
  for p in (a,b):self.s.consent(self.k,p['required'][0],p['id'],p['digest'])
  def run(p):
   try:return self.s.approve(self.k,'admin',p['id'],p['digest'],True)
   except SaveError:return 'conflict'
  with concurrent.futures.ThreadPoolExecutor(2) as pool: out=list(pool.map(run,[a,b]))
  self.assertEqual(out.count('conflict'),1);self.assertEqual(len(self.s.read(self.k)['snapshot']['roles']),1)
 def test_roll_damage_replay_cross_group(self):
  self.setup_role();roll=self.s.roll(self.k,'u03','03','damage',2,6)
  p=dict(kind='damage',cid='03',roll=roll['id'],bonus=2,arm=0,penetrating=False,reason='已确认命中')
  self.commit(self.propose(p));r=self.s.read(self.k)['snapshot']['roles']['03'];self.assertEqual(r['hp'],40-sum(roll['dice'])-2)
  with self.assertRaises(SaveError):self.propose(p)
  with self.assertRaises(SaveError):self.s.consent('qq_official:other','u03','none','none')
 def test_dossier_and_mission(self):
  self.setup_role()
  with self.assertRaises(SaveError):self.s.roll(self.k,'u03','03','reveal',1,10)
  for i in range(3):
   self.commit(self.propose(dict(kind='world',field='current_task_id',value='t'+str(i),reason='新任务')))
   p=dict(kind='mission',cid='03',task='t'+str(i),meaningful_action='公开完成一次工程检定')
   self.commit(self.propose(p))
   with self.assertRaises(SaveError):self.propose(p)
  r=self.s.read(self.k)['snapshot']['roles']['03'];self.assertEqual(r['xp'],9);self.assertEqual(r['cumulative_xp'],9);self.assertEqual(r['dossier_progress'],3)
  self.commit(self.propose(dict(kind='omen',cid='03',omen='接口显示一帧旧地图；无数值效果')))
  roll=self.s.roll(self.k,'u03','03','reveal',1,10);again=self.s.roll(self.k,'u03','03','reveal',1,10);self.assertEqual(roll['id'],again['id'])
  self.commit(self.propose(dict(kind='reveal',cid='03',roll=roll['id'],fact='出现待核实账单',clue='公开终端记录',reward='线索共鸣')))
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['dossier_category_id'],roll['dice'][0])
 def test_atomic_audit_failure(self):
  p=self.propose(dict(kind='initialize',cid='03',user='u03',nickname='n'));self.s.consent(self.k,'u03',p['id'],p['digest'])
  with self.s._db() as db:db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'forced'); END")
  with self.assertRaises(sqlite3.IntegrityError):self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  self.assertEqual(self.rev(),1);self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
  with self.s._db() as db:self.assertEqual(db.execute('SELECT status FROM neon_v020_proposals').fetchone()[0],'pending')
 def test_legacy_revision6_upgrade_and_rollback(self):
  with tempfile.TemporaryDirectory() as d:
   old=NeonStore(d);old.init(self.k,'admin')
   for i in range(5):old.patch_many(self.k,'admin',i+1,[dict(target='world',field='recap',value='test'+str(i))],'fixture')
   before=old.read(self.k);history=old.history(self.k)
   new=SafeStore(d);self.assertEqual(new.read(self.k),before);self.assertEqual(new.history(self.k),history);self.assertEqual(before['revision'],6)
   self.assertEqual(NeonStore(d).read(self.k),before)
   filename=new.backup_database()
   with sqlite3.connect(str(new.data_dir/'database_backups'/filename)) as db:self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
 def test_restore_role_and_away(self):
  self.setup_role(); source=self.rev(); self.commit(self.propose(dict(kind='status',cid='03',value='away')))
  with self.assertRaises(SaveError):self.propose(dict(kind='resource',cid='03',field='cash',delta=-1,reason='x'))
  self.commit(self.propose(dict(kind='restore_role',cid='03',source_revision=source)))
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['status'],'active')
 def test_rejection_expiry_invalid_values(self):
  p=self.propose(dict(kind='initialize',cid='03',user='u03',nickname='n'));self.s.consent(self.k,'u03',p['id'],p['digest'],False)
  with self.assertRaises(SaveError):self.commit(p)
  self.setup_role()
  for f,v in [('cash',-999999),('stress',7),('ram',1)]:
   with self.assertRaises(SaveError):self.propose(dict(kind='resource',cid='03',field=f,delta=v,reason='x'))
  p=self.propose(dict(kind='resource',cid='03',field='cash',delta=1,reason='x'))
  with self.s._db() as db:db.execute('UPDATE neon_v020_proposals SET expires=0 WHERE id=?',(p['id'],))
  with self.assertRaises(SaveError):self.commit(p)
 def test_batch_two_players_atomic_and_consent(self):
  self.setup_role('01');self.setup_role('02')
  p=self.propose(dict(kind='batch',operations=[dict(kind='resource',cid='01',field='cash',delta=10,reason='已到账'),dict(kind='resource',cid='02',field='cash',delta=20,reason='已到账')]))
  before=self.s.read(self.k)
  self.s.consent(self.k,'u01',p['id'],p['digest'])
  with self.assertRaises(SaveError):self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  self.assertEqual(before,self.s.read(self.k));self.s.consent(self.k,'u02',p['id'],p['digest']);self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  result=self.s.read(self.k);self.assertEqual(result['revision'],before['revision']+1)
  self.assertEqual(result['snapshot']['roles']['01']['cash'],710);self.assertEqual(result['snapshot']['roles']['02']['cash'],870)
 def test_batch_invalid_second_operation_no_partial_write(self):
  self.setup_role();before=self.s.read(self.k)
  with self.assertRaises(SaveError):self.propose(dict(kind='batch',operations=[dict(kind='resource',cid='03',field='cash',delta=10,reason='x'),dict(kind='resource',cid='03',field='ram',delta=1,reason='x')]))
  self.assertEqual(before,self.s.read(self.k))
 def test_catchup_and_unknown_xp(self):
  self.setup_role('01');self.commit(self.propose(dict(kind='resource',cid='01',field='xp',delta=20,reason='历史XP核对')))
  p=self.propose(dict(kind='initialize',cid='02',user='u02',nickname='虹膜'));self.commit(p)
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['02']['xp'],14)
 def test_upgrade_snapshot_bytes_not_rewritten(self):
  with self.s._db() as db: before=[tuple(r) for r in db.execute('SELECT * FROM saves')]
  SafeStore(self.tmp.name)
  with self.s._db() as db:self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM saves')])
 def test_proposal_survives_restart(self):
  p=self.propose(dict(kind='initialize',cid='03',user='u03',nickname='n'))
  self.s=SafeStore(self.tmp.name);self.commit(p)
  self.assertEqual(self.rev(),2)

 def test_chapter25_all_bonus_combinations(self):
  self.setup_role()
  total=0
  for i,(personal,faction,expected) in enumerate([(False,False,3),(True,False,5),(False,True,4),(True,True,6)]):
   with self.subTest(personal=personal,faction=faction):
    task='xp-'+str(i)
    self.commit(self.propose(dict(kind='world',field='current_task_id',value=task,reason='任务完成核对')))
    payload=dict(kind='mission',cid='03',task=task,meaningful_action='公开任务有效行动记录',personal_major_goal=personal,faction_shift=faction)
    if personal:payload['personal_goal_evidence']='公开重大目标完成记录'
    if faction:payload['faction_shift_evidence']='关键剧情改变阵营格局记录'
    p=self.propose(payload)
    self.assertEqual(p['required'],['u03'])
    total+=expected
    self.assertEqual(p['result']['roles']['03']['xp'],total)
    self.assertEqual(p['result']['roles']['03']['cumulative_xp'],total)
    self.commit(p)
    with self.assertRaises(SaveError):self.propose(payload)
  r=self.s.read(self.k)['snapshot']['roles']['03']
  self.assertEqual(r['xp'],18);self.assertEqual(r['cumulative_xp'],18);self.assertEqual(r['dossier_progress'],3)
 def test_chapter25_reject_invalid_bonus_and_override(self):
  self.setup_role()
  self.commit(self.propose(dict(kind='world',field='current_task_id',value='xp-test',reason='任务完成')))
  base=dict(kind='mission',cid='03',task='xp-test',meaningful_action='公开有效行动记录')
  before=self.s.read(self.k)
  for extra in [dict(personal_major_goal=True),dict(faction_shift=True),dict(personal_major_goal=1),dict(faction_shift='true'),dict(xp=99),dict(xp_award=7),dict(personal_major_goal=True,personal_goal_evidence=' '),dict(faction_shift=True,faction_shift_evidence=5)]:
   with self.subTest(extra=extra),self.assertRaises(SaveError):self.propose(dict(base,**extra))
   self.assertEqual(before,self.s.read(self.k))
 def test_chapter25_no_repeated_task_with_different_bonus(self):
  self.setup_role();self.commit(self.propose(dict(kind='world',field='current_task_id',value='once',reason='完成任务')))
  base=dict(kind='mission',cid='03',task='once',meaningful_action='有效行动')
  self.commit(self.propose(base));before=self.s.read(self.k)
  with self.assertRaises(SaveError):self.propose(dict(base,personal_major_goal=True,personal_goal_evidence='补充目标证据'))
  with self.assertRaises(SaveError):self.propose(dict(kind='batch',operations=[base,base]))
  self.assertEqual(before,self.s.read(self.k))
 def test_chapter25_stale_old_two_xp_preview_rejected(self):
  self.setup_role();self.commit(self.propose(dict(kind='world',field='current_task_id',value='old',reason='完成任务')))
  p=self.propose(dict(kind='mission',cid='03',task='old',meaningful_action='有效行动'))
  # Emulate a persisted v0.2.0 preview and its existing consent digest.
  old_preview=p['result'];old_preview['roles']['03']['xp']-=1;old_preview['roles']['03']['cumulative_xp']-=1
  with self.s._db() as db:db.execute('UPDATE neon_v020_proposals SET preview=? WHERE id=?',(canonical(old_preview),p['id']))
  self.s.consent(self.k,'u03',p['id'],p['digest']);before=self.s.read(self.k)
  with self.assertRaises(SaveError):self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  self.assertEqual(before,self.s.read(self.k))
