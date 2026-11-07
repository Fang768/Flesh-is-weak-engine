import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.simple_open import SimpleOpen
from astrbot_plugin_nexus_gm.dynamic.adapter import DynamicAdapter
class Opening(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=Path(self.tmp.name);self.s=SafeStore(p/'save');self.k='qq_official:isolated';self.s.init(self.k,'admin')
  for cid in ('02','05','07'):
   a=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],dict(kind='initialize',cid=cid,user=cid,nickname=cid));self.s.consent(self.k,cid,a['id'],a['digest']);self.s.approve(self.k,'admin',a['id'],a['digest'],True)
  d=p/'gm';d.mkdir();self.d=DynamicAdapter(d,self.s,None,None,None);self.o=SimpleOpen(d,self.s,self.d);self.before=self.s.read(self.k);self.d.allow.write_text(json.dumps([self.k]))
  (d/'simple_open_authorization.json').write_text(json.dumps(dict(enabled=True,groups=[self.k],scope='voluntary_noncombat_open_and_pending_join',source='explicit_test_operator_grant')))
 def test_first_player_starts_without_other_players(self):
  text=self.o.start(self.k,'02','first',False);self.assertIn('米拉',text);r=self.s.read(self.k)['snapshot']['roles'];self.assertEqual(r['02']['status'],'active');self.assertEqual(r['05']['status'],'pending');self.assertEqual(r['07']['status'],'pending');self.assertEqual(set(self.d.get().read(self.k)['roster']),{'02'})
  for cid,role in r.items():
   for f in ('attributes','skills','cash','xp','hp','ram'):self.assertEqual(role[f],self.before['snapshot']['roles'][cid][f])
 def test_scoped_authority_and_real_owner_required(self):
  with self.assertRaises(ValueError):self.o.start(self.k,'intruder','first',False)
  (self.o.directory/'simple_open_authorization.json').unlink()
  with self.assertRaises(ValueError):self.o.start(self.k,'02','first',False)
  self.assertEqual(self.before,self.s.read(self.k))
 def test_real_admin_can_open_without_being_a_player(self):
  (self.o.directory/'simple_open_authorization.json').unlink();self.assertIn('不等人齐',self.o.start(self.k,'admin','admin-start',True));self.assertEqual(self.before,self.s.read(self.k));self.o.arrive(self.k,'02','actual-player');self.assertEqual(self.s.read(self.k)['snapshot']['roles']['02']['status'],'active')
 def test_later_player_preserves_story_memory(self):
  self.o.start(self.k,'02','first',False);e=self.d.get();e.action(self.k,'02','talk','先问清条件','talk');e.checkpoint(self.k);e.consent(self.k,'02');e.approve(self.k,'admin',True);old=e.read(self.k);rev=self.s.read(self.k)['revision'];self.o.arrive(self.k,'05','late');new=e.read(self.k)
  self.assertEqual(new['summaries'],old['summaries']);self.assertEqual(new['round'],old['round']);self.assertEqual(new['facts'],old['facts']);self.assertEqual(set(new['roster']),{'02','05'});self.assertTrue(new['started']);self.assertEqual(self.s.read(self.k)['revision'],rev+1);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['07']['status'],'pending')
 def test_busy_window_queues_and_flushes_at_safe_boundary(self):
  self.o.start(self.k,'02','first',False);e=self.d.get();e.action(self.k,'02','talk','我先交谈','talk');self.assertIn('安全窗口',self.o.arrive(self.k,'05','late'));self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending');self.assertEqual(self.o.flush(self.k),[])
  e.checkpoint(self.k);e.consent(self.k,'02');e.approve(self.k,'admin',True);self.assertEqual(len(self.o.flush(self.k)),1);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'active');self.assertEqual(e.read(self.k)['events'],[])
 def test_pause_and_duplicates_do_not_reactivate_or_reopen(self):
  self.o.start(self.k,'02','first',False);rev=self.s.read(self.k)['revision'];self.o.start(self.k,'02','first',False);self.o.arrive(self.k,'02','again');self.assertEqual(rev,self.s.read(self.k)['revision']);self.s.pause_group(self.k,'02','pause')
  with self.assertRaises(ValueError):self.o.arrive(self.k,'05','late')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
 def test_postcommit_interruption_recovers_without_second_entry(self):
  e=self.d.get()
  with patch.object(e,'prepare',side_effect=RuntimeError('injected after native commit')):
   with self.assertRaises(RuntimeError):self.o.start(self.k,'02','first',False)
  rev=self.s.read(self.k)['revision'];self.o.arrive(self.k,'02','first');self.assertEqual(rev,self.s.read(self.k)['revision']);self.assertTrue(e.read(self.k)['started'])
 def test_external_revision_does_not_silently_rebase_story(self):
  self.o.start(self.k,'02','first',False);p=self.s.propose(self.k,'admin',self.s.read(self.k)['revision'],dict(kind='world',field='location',value='另一个地点',reason='隔离冲突'));self.s.consent(self.k,'02',p['id'],p['digest']);self.s.approve(self.k,'admin',p['id'],p['digest'],True)
  with self.assertRaises(ValueError):self.o.arrive(self.k,'05','late')
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')

 def test_cancel_queued_admission_preserves_bystander_status(self):
  self.o.start(self.k,'02','first',False);e=self.d.get();e.action(self.k,'02','talk','交谈','talk');self.o.arrive(self.k,'05','late');self.o.cancel(self.k,'05','cancel');self.assertEqual(self.o.read(self.k)['queue'],{});self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
 def test_expired_queued_request_does_not_join_later(self):
  self.o.start(self.k,'02','first',False);e=self.d.get();e.action(self.k,'02','talk','交谈','talk');self.o.arrive(self.k,'05','late');s=self.o.read(self.k);s['queue']['05']['expires']=0;self.o.write(self.k,s);e.checkpoint(self.k);e.consent(self.k,'02');e.approve(self.k,'admin',True);self.assertEqual(self.o.flush(self.k),[]);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['05']['status'],'pending')
