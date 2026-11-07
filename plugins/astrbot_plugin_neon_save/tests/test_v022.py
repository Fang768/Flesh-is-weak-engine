from . import test_safe
from astrbot_plugin_neon_save.store import SaveError
class Gates(test_safe.SafeTests):
 def test_mission_gates(self):
  self.setup_role();self.commit(self.propose(dict(kind='world',field='current_task_id',value='gate',reason='测试')))
  good=dict(kind='mission',cid='03',task='gate',meaningful_action='公开行动',completed=True,survived=True,actually_present=True,completion_evidence='完成记录')
  for f in ('completed','survived','actually_present','completion_evidence'):
   p=dict(good);p.pop(f)
   with self.assertRaises(SaveError):self.s.propose(self.k,'ai',self.rev(),p)
  for state in ('not_started','paused'):
   self.commit(self.propose(dict(kind='world',field='scene_status',value=state,reason='测试')))
   with self.assertRaises(SaveError):self.s.propose(self.k,'ai',self.rev(),good)
 def test_entry_gates(self):
  self.commit(self.propose(dict(kind='initialize',cid='03',user='u03',nickname='测试')))
  for entry in (None,dict(safe_node=True,in_combat=True,check_locked=False,scene='战斗',team='A'),dict(safe_node=True,in_combat=False,check_locked=True,scene='锁定',team='A')):
   p=dict(kind='status',cid='03',value='active',entry=entry)
   with self.assertRaises(SaveError):self.s.propose(self.k,'ai',self.rev(),p)

 def test_delayed_entry_and_batch(self):
  from unittest.mock import patch
  self.setup_role()
  for batch in (False,True):
   self.commit(self.propose(dict(kind='status',cid='03',value='away')))
   op=dict(kind='status',cid='03',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全据点',team='A'))
   payload=dict(kind='batch',operations=[op]) if batch else op
   with patch('astrbot_plugin_neon_save.safe.utcnow',return_value='2026-11-02T02:00:00+00:00'):
    p=self.propose(payload)
   with patch('astrbot_plugin_neon_save.safe.utcnow',return_value='2026-11-02T02:05:00+00:00'):
    self.commit(p)
   r=self.s.read(self.k)['snapshot']['roles']['03']
   self.assertEqual(r['equipment'][-1]['neon_v022_entry']['timestamp'],'2026-11-02T02:00:00+00:00')
