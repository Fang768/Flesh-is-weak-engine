import json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.engine import Engine,GMError

class OnboardingSafety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(Path(self.tmp.name)/'save');self.k='qq_official:pre-game';self.s.init(self.k,'admin');self.s.set_enrollment_auto(self.k,'admin',True,True)
  for cid,u in [('03','a'),('01','b')]:
   p=self.s.request_enrollment(self.k,u,'request'+u,cid);self.s.confirm_enrollment(self.k,u,'yes'+u,p['id'],p['digest'])
  self.e=Engine(Path(self.tmp.name)/'gm.sqlite3',self.s)
 def test_pending_player_pause_and_manual_unanimous_restore_without_start(self):
  before=self.s.read(self.k);self.e.onboarding_pause(self.k,'a','pause1');self.assertTrue(self.s.gate_state(self.k)['paused']);self.assertEqual(before,self.s.read(self.k));self.assertEqual(self.e.view(self.k)['scene'],'未开场（登记安全控制）')
  r=self.e.request_resume(self.k,'a')
  self.e.consent_resume(self.k,'a',r['id'],r['digest'])
  with self.assertRaises(GMError):self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True)
  self.e.consent_resume(self.k,'b',r['id'],r['digest'])
  with self.assertRaises(GMError):self.e.approve_resume(self.k,'a',r['id'],r['digest'],False)
  self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True);self.assertEqual(before,self.s.read(self.k));self.assertFalse(self.s.gate_state(self.k)['paused'])
  with self.assertRaises(GMError):self.e.action(self.k,'a','bad','调查','调查')
  self.e.onboarding_pause(self.k,'a','pause1');self.assertFalse(self.s.gate_state(self.k)['paused'])
 def test_new_pause_after_new_pending_member_refreshes_safety_roster_only(self):
  self.e.onboarding_pause(self.k,'a','pause1');r=self.e.request_resume(self.k,'admin',True)
  for u in ('a','b'):self.e.consent_resume(self.k,u,r['id'],r['digest'])
  self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True)
  p=self.s.request_enrollment(self.k,'c','c','02');self.s.confirm_enrollment(self.k,'c','cyes',p['id'],p['digest']);before=self.s.read(self.k)
  self.e.onboarding_pause(self.k,'c','pause2');r=self.e.request_resume(self.k,'admin',True);self.assertEqual(set(r['required']),{'a','b','c'});self.assertEqual(before,self.s.read(self.k));self.assertEqual(r['round'],0)
 def test_restart_and_partial_crash_keep_pre_game_pause(self):
  self.e.onboarding_pause(self.k,'a','pause');self.e=Engine(Path(self.tmp.name)/'gm.sqlite3',SafeStore(Path(self.tmp.name)/'save'));self.assertTrue(self.e.is_paused(self.k))
  r=self.e.request_resume(self.k,'admin',True);self.e.consent_resume(self.k,'a',r['id'],r['digest']);self.e=Engine(Path(self.tmp.name)/'gm.sqlite3',self.s)
  with self.assertRaises(GMError):self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True)
  p=None
  with self.assertRaises(ValueError):p=self.s.request_enrollment(self.k,'c','no','02')
  self.assertIsNone(p)
 def test_recover_checkpoint_after_gate_closed_before_journal_creation(self):
  self.s.pause_group(self.k,'a','crash-pause');self.e.ensure_onboarding_control(self.k);self.assertTrue(self.e.is_paused(self.k));r=self.e.request_resume(self.k,'admin',True);self.assertEqual(set(r['required']),{'a','b'})
 def test_non_player_cannot_pause_or_start_control(self):
  with self.assertRaises(GMError):self.e.onboarding_pause(self.k,'outsider','pause')
  self.assertFalse(self.s.gate_state(self.k)['paused'])
  with self.e.db() as c:self.assertIsNone(c.execute('select 1 from sessions').fetchone())

 def test_control_only_mode_is_bound_into_resume_checkpoint(self):
  self.e.onboarding_pause(self.k,'a','pause');r=self.e.request_resume(self.k,'admin',True)
  for u in ('a','b'):self.e.consent_resume(self.k,u,r['id'],r['digest'])
  with self.e.db() as c:
   state=json.loads(c.execute('select v from sessions where k=?',(self.k,)).fetchone()[0]);state['onboarding_only']=False;c.execute('update sessions set v=? where k=?',(json.dumps(state),self.k))
  with self.assertRaises(GMError):self.e.approve_resume(self.k,'admin',r['id'],r['digest'],True)
  self.assertTrue(self.s.gate_state(self.k)['paused'])
