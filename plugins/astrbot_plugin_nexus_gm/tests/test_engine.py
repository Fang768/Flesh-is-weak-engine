import unittest,tempfile,copy
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.engine import Engine,GMError
class Flow(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.save=SafeStore(Path(self.tmp.name)/'save');self.k='qq_official:isolated';self.save.init(self.k,'admin')
  def commit(op):
   p=self.save.propose(self.k,'admin',self.save.read(self.k)['revision'],op)
   for u in p['required']:self.save.consent(self.k,u,p['id'],p['digest'])
   self.save.approve(self.k,'admin',p['id'],p['digest'],True)
  self.commit=commit
  self.commit(dict(kind='initialize',cid='03',user='u03',nickname='测试'))
  self.commit(dict(kind='status',cid='03',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='据点',team='A')))
  self.commit(dict(kind='world',field='scene_status',value='active',reason='隔离开场'))
  self.e=Engine(Path(self.tmp.name)/'gm.db',self.save,rand=lambda n:9);self.e.open(self.k,True);self.n=0
 def act(self,intent,user='u03'):
  self.n+=1;return self.e.action(self.k,user,str(self.n),'【行动】'+intent,intent)
 def finish(self):
  self.act('交涉');self.act('roll');self.act('工程');self.act('roll');self.act('end')
 def test_complete_proposal_does_not_commit(self):
  before=self.save.read(self.k);self.finish();p=self.e.proposal(self.k,'admin',True)
  self.assertEqual(before,self.save.read(self.k))
  with self.assertRaises(Exception):self.save.approve(self.k,'admin',p['id'],p['digest'],True)
  for u in p['required']:self.save.consent(self.k,u,p['id'],p['digest'])
  self.save.approve(self.k,'admin',p['id'],p['digest'],True)
  r=self.save.read(self.k)['snapshot']['roles']['03'];self.assertEqual((r['xp'],r['dossier_progress']),(3,1))
 def test_identity_dedup_and_restart(self):
  with self.assertRaises(GMError):self.act('工程','intruder')
  self.e.action(self.k,'u03','one','行动','交涉')
  self.assertIn('声明交涉',self.e.action(self.k,'u03','one','行动','roll'))
  e=Engine(self.e.path,self.save,rand=lambda n:9);self.assertIn('1d10=10',e.action(self.k,'u03','two','行动','roll'))
 def test_no_roll_before_dv(self):
  with self.assertRaises(GMError):self.act('roll')
 def test_pause(self):
  self.act('pause')
  with self.assertRaises(GMError):self.act('end')
 def test_conflict(self):
  self.commit(dict(kind='resource',cid='03',field='cash',delta=1,reason='模拟外部变化'))
  with self.assertRaises(GMError):self.act('end')
 def test_fail_no_free_retry(self):
  self.act('end');self.e.rand=lambda n:0;self.act('调查');self.act('roll')
  self.assertFalse(self.e.view(self.k)['complete'])
  with self.assertRaises(GMError):self.act('调查')
  self.act('工程')
 def test_multiplayer_window(self):
  self.commit(dict(kind='initialize',cid='01',user='u01',nickname='测试2'))
  self.commit(dict(kind='status',cid='01',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='据点',team='A')))
  e=Engine(Path(self.tmp.name)/'multi.db',self.save);e.open(self.k,True)
  e.action(self.k,'u03','a','结束','end');self.assertEqual(e.view(self.k)['scene'],'接洽');self.assertEqual(e.view(self.k)['waiting'],['01'])
  with self.assertRaises(GMError):e.action(self.k,'u03','b','结束','end')
  e.action(self.k,'u01','c','结束','end');self.assertEqual(e.view(self.k)['scene'],'取回')
 def test_admin_open_and_duplicate(self):
  with self.assertRaises(GMError):self.e.open(self.k,False)
  with self.assertRaises(GMError):self.e.open(self.k,True)
 def test_cannot_propose_early(self):
  with self.assertRaises(GMError):self.e.proposal(self.k,'admin',True)
