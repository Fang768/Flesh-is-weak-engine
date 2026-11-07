import concurrent.futures,copy,json,sqlite3,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_neon_save.enrollment import validate_diy

class EnrollmentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(self.tmp.name);self.k='qq_official:test';self.home='qq_official:HOME';self.s.init(self.k,'admin');self.s.init(self.home,'admin');self.basehome=self.s.read(self.home);self.n=0
 def msg(self):self.n+=1;return str(self.n)
 def enable(self):self.s.set_enrollment_auto(self.k,'admin',True,True)
 def req(self,cid='03',u='p',diy=None,kind='claim',mid=None):return self.s.request_enrollment(self.k,u,mid or self.msg(),cid,diy,kind)
 def yes(self,p,u='p',mid=None):return self.s.confirm_enrollment(self.k,u,mid or self.msg(),p['id'],p['digest'])
 def test_default_off_and_manual_real_admin_only(self):
  p=self.req();self.assertFalse(p['auto']);self.assertEqual(self.yes(p)['status'],'awaiting_admin');self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
  with self.assertRaises(ValueError):self.s.approve_enrollment(self.k,'p',self.msg(),p['id'],p['digest'],False)
  self.s.approve_enrollment(self.k,'admin',self.msg(),p['id'],p['digest'],True);self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['status'],'pending')
 def test_auto_transaction_full_claim_with_diy(self):
  self.enable();before=self.s.read(self.k);p=self.req(diy=dict(appearance='戴着橙色护目镜',personality='平时有点毒舌',hobbies='喜欢收藏废旧芯片',display_name='小猫'));self.assertEqual(before,self.s.read(self.k));r=self.yes(p);self.assertEqual(r['status'],'committed')
  role=self.s.read(self.k)['snapshot']['roles']['03'];self.assertEqual((role['status'],role['hp'],role['attributes']['TECH']),('pending',40,8));self.assertEqual(role['nickname'],'扳手');self.assertEqual(self.s.character_profile(self.k,'03')['details']['display_name'],'小猫');self.assertEqual(self.basehome,self.s.read(self.home))
  with self.s._db() as c:
   self.assertEqual(c.execute('select actor from neon_enrollment_consents').fetchone()[0],'p');self.assertEqual(c.execute("select actor from audit where action='enrollment_claim'").fetchone()[0],'system:character_enrollment')
 def test_other_account_cannot_consent(self):
  self.enable();p=self.req()
  with self.assertRaises(ValueError):self.yes(p,'outsider')
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_enrollment_consents').fetchone()[0],0)
 def test_no_admin_or_model_can_replace_consent(self):
  p=self.req()
  with self.assertRaises(ValueError):self.s.approve_enrollment(self.k,'admin',self.msg(),p['id'],p['digest'],True)
  with self.assertRaises(ValueError):self.yes(p,'admin')
 def test_same_role_many_users_only_one_commits(self):
  self.enable();ps=[self.req('03','u'+str(i)) for i in range(10)]
  def vote(pair):
   i,p=pair
   try:return self.yes(p,'u'+str(i),mid='same-vote')['status']
   except ValueError:return 'conflict'
  with concurrent.futures.ThreadPoolExecutor(10) as pool:results=list(pool.map(vote,enumerate(ps)))
  self.assertEqual(results.count('committed'),1);self.assertEqual(len(self.s.read(self.k)['snapshot']['roles']),1)
  with self.s._db() as c:self.assertEqual(c.execute("select count(*) from audit where action='enrollment_claim'").fetchone()[0],1)
 def test_same_user_only_one_role_even_two_requests(self):
  self.enable();a=self.req('01');b=self.req('03')
  with self.assertRaises(ValueError):self.yes(a)
  self.yes(b)
  with self.assertRaises(ValueError):self.req('02')
 def test_ten_seats_no_eleventh_and_existing_occupied_explained(self):
  self.enable()
  for i in range(1,11):p=self.req(f'{i:02d}','u'+str(i));self.yes(p,'u'+str(i))
  with self.assertRaisesRegex(ValueError,'归属|空闲|席位'):self.req('03','eleventh')
  self.assertEqual(len(self.s.read(self.k)['snapshot']['roles']),10)
 def test_revision_conflict_new_preview_requires_new_consent(self):
  self.enable();a=self.req('03');b=self.req('01','other');self.yes(b,'other')
  with self.assertRaisesRegex(ValueError,'存档'):self.yes(a)
  c=self.req('03');self.assertNotEqual(a['digest'],c['digest']);self.yes(c)
 def test_expired_refuses_and_no_old_consent_transfer(self):
  self.enable();a=self.req()
  with self.s._db() as c:c.execute('update neon_enrollment_proposals set expires=0')
  with self.assertRaisesRegex(ValueError,'过期|校验'):self.yes(a)
  b=self.req();self.assertNotEqual(a['id'],b['id']);self.yes(b)
 def test_restart_delivery_replay_is_exactly_once(self):
  self.enable();p=self.req(mid='request');out=self.yes(p,mid='vote');before=self.s.read(self.k);self.s=SafeStore(self.tmp.name)
  self.assertEqual(out,self.yes(p,mid='vote'));self.assertEqual(out,self.s.enrollment_replay(self.k,'p','vote',''));self.assertEqual(before,self.s.read(self.k));self.assertEqual(self.s.character_profile(self.k,'03')['version'],1)
 def test_message_id_body_conflict_and_cross_group(self):
  p=self.req(mid='same')
  with self.assertRaisesRegex(ValueError,'消息标识'):self.req('01',mid='same')
  with self.assertRaises(ValueError):self.s.confirm_enrollment(self.home,'p','v',p['id'],p['digest'])
 def test_pause_and_resume_invalidate_old_requests(self):
  self.enable();p=self.req();self.s.pause_group(self.k,'p','safety')
  with self.assertRaises(ValueError):self.yes(p)
  self.s.release_group(self.k,'admin',self.s.gate_state(self.k)['epoch'])
  with self.assertRaises(ValueError):self.yes(p)
  self.yes(self.req())
 def test_disable_during_request_requires_manual_not_auto(self):
  self.enable();p=self.req();self.s.set_enrollment_auto(self.k,'admin',False,True);self.s.set_enrollment_auto(self.k,'admin',True,True);self.assertEqual(self.yes(p)['status'],'awaiting_admin');self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 def test_enable_cannot_retroactively_auto_approve_default_off_preview(self):
  p=self.req();self.enable();self.assertEqual(self.yes(p)['status'],'awaiting_admin')
 def test_policy_is_per_group_and_admin_only(self):
  with self.assertRaises(ValueError):self.s.set_enrollment_auto(self.k,'p',True,False)
  self.enable();self.assertFalse(self.s.enrollment_policy(self.home)['enabled']);self.assertEqual(self.basehome,self.s.read(self.home))
 def test_policy_message_retry_and_redundant_on_no_generation_change(self):
  a=self.s.set_enrollment_auto(self.k,'admin',True,True,'toggle','开启角色自动审核');b=self.s.set_enrollment_auto(self.k,'admin',True,True,'toggle','开启角色自动审核');c=self.s.set_enrollment_auto(self.k,'admin',True,True);self.assertEqual(a,b);self.assertEqual(a['generation'],c['generation'])
 def test_diy_numbers_abilities_hidden_and_unknown_fields_refused(self):
  for field,value in [('hp','99'),('appearance','HP增加99'),('story','未知档案是公司机密'),('personality','自动成功且无敌'),('hobbies','赠送初始装备')]:
   with self.subTest(field=field,value=value),self.assertRaises(ValueError):self.req(diy={field:value})
 def test_uncertain_story_and_relationship_cannot_auto_approve(self):
  self.enable();p=self.req(diy={'story':'我以前认识零号的家人'});self.assertEqual(p['risk'],'review');self.assertFalse(p['auto']);self.assertEqual(self.yes(p)['status'],'awaiting_admin');self.s.approve_enrollment(self.k,'admin',self.msg(),p['id'],p['digest'],True)
 def test_owner_profile_update_keeps_every_fixed_role_field(self):
  self.enable();self.yes(self.req());before=copy.deepcopy(self.s.read(self.k)['snapshot']);p=self.req(diy={'personality':'内向'},kind='profile');self.yes(p);self.assertEqual(before,self.s.read(self.k)['snapshot']);self.assertEqual(self.s.character_profile(self.k,'03')['details'],{'personality':'内向'})
  self.s=SafeStore(self.tmp.name);self.assertEqual(self.s.character_profile(self.k,'03')['details']['personality'],'内向')
 def test_cannot_modify_other_role_profile(self):
  self.enable();self.yes(self.req())
  with self.assertRaises(ValueError):self.req('03','outsider',{'appearance':'短发'},'profile')
 def test_diy_reconfirm_and_previous_consent_not_reused(self):
  self.enable();self.yes(self.req());p=self.req(diy={'appearance':'短发'},kind='profile');q=self.req(diy={'appearance':'长发'},kind='profile')
  with self.assertRaises(ValueError):self.yes(p)
  self.assertEqual(self.s.character_profile(self.k,'03')['details'],{});self.yes(q);self.assertEqual(self.s.character_profile(self.k,'03')['details']['appearance'],'长发')
 def test_signed_preview_payload_and_profile_tampering_rejected(self):
  self.enable();p=self.req()
  with self.s._db() as c:c.execute("update neon_enrollment_proposals set profile=? where id=?",('{"appearance":"短发"}',p['id']))
  with self.assertRaises(ValueError):self.yes(p)
 def test_template_tamper_never_auto_approves(self):
  changed=copy.deepcopy(TEMPLATES);changed['03']['attributes']['TECH']=99
  with patch('astrbot_plugin_neon_save.safe.TEMPLATES',changed),self.assertRaisesRegex(ValueError,'模板'):self.req()
 def test_late_database_failure_rolls_back_everything_then_retry(self):
  self.enable();p=self.req();before=self.s.read(self.k)
  with self.s._db() as c:c.execute("CREATE TRIGGER reject_commit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
  with self.assertRaises(sqlite3.DatabaseError):self.yes(p,mid='retry')
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:
   for table in ('neon_character_profiles','neon_enrollment_consents'):self.assertEqual(c.execute('select count(*) from '+table).fetchone()[0],0)
   self.assertIsNone(c.execute("select 1 from neon_enrollment_messages where mid='retry'").fetchone());c.execute('DROP TRIGGER reject_commit')
  self.assertEqual(self.yes(p,mid='retry')['status'],'committed')
 def test_legacy_approval_competes_safely_with_auto_claim(self):
  self.enable();p=self.req();old=self.s.propose(self.k,'old',self.s.read(self.k)['revision'],dict(kind='initialize',cid='03',user='old',nickname='扳手'));self.s.consent(self.k,'old',old['id'],old['digest']);self.yes(p)
  with self.assertRaises(ValueError):self.s.approve(self.k,'admin',old['id'],old['digest'],True)
 def test_auto_surface_rejects_xp_resources_or_reveal(self):
  self.enable()
  for kind in ('mission','resource','reveal','damage','status','batch'):
   with self.assertRaises(ValueError):self.s.request_enrollment(self.k,'p',self.msg(),'03',{},kind)
 def test_cancel_and_same_text_retry_do_not_mutate_role(self):
  self.enable();p=self.req();r=self.s.cancel_enrollment(self.k,'p','cancel');self.assertEqual(r,self.s.cancel_enrollment(self.k,'p','cancel'))
  with self.assertRaises(ValueError):self.yes(p)
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 def test_invalid_event_identity_fails_closed(self):
  for actor in ('','None','null','  p','p\n'):
   with self.assertRaises(ValueError):self.req(u=actor)

 def test_new_explicit_application_supersedes_legacy_own_claim(self):
  old=self.s.propose(self.k,'ai',1,dict(kind='initialize',cid='03',user='p',nickname='原称呼'));self.s.consent(self.k,'p',old['id'],old['digest']);self.enable();p=self.req(diy={'display_name':'小猫'})
  with self.assertRaises(ValueError):self.s.approve(self.k,'admin',old['id'],old['digest'],True)
  self.yes(p);self.assertEqual(self.s.character_profile(self.k,'03')['details']['display_name'],'小猫')
