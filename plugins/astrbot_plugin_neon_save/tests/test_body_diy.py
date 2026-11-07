import copy,json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_neon_save.enrollment import validate_diy
from astrbot_plugin_neon_save.body_privacy import public_diy
class BodyDIY(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=self.tmp.name;self.s=SafeStore(self.path);self.k='qq_official:isolated-body';self.s.init(self.k,'admin');self.s.set_enrollment_auto(self.k,'admin',True,True)
  p=self.s.request_enrollment(self.k,'owner','claim','03',{});self.s.confirm_enrollment(self.k,'owner','yes',p['id'],p['digest']);self.before=self.s.read(self.k);self.seq=0
 def request(self,diy,source='我的角色已成年，身体外观：有阴茎',actor='owner'):
  self.seq+=1;return self.s.request_enrollment(self.k,actor,str(self.seq),'03',diy,'profile',source)
 def confirm(self,p,actor='owner'):
  self.seq+=1;return self.s.confirm_enrollment(self.k,actor,str(self.seq),p['id'],p['digest'])
 def test_anatomical_words_are_cosmetic_not_blocked(self):
  for word in ('阳具','阴茎','生殖器','外阴','睾丸'):
   clean,risk=validate_diy(dict(adult_status='成年',body_features='有'+word));self.assertEqual(risk,'auto')
 def test_must_confirm_and_persists_after_restart_without_stat_changes(self):
  p=self.request(dict(adult_status='成年',body_features='有阴茎'));self.assertEqual(self.s.character_profile(self.k,'03')['details'],{});self.assertEqual(self.s.read(self.k),self.before)
  self.assertEqual(self.confirm(p)['status'],'committed');s=SafeStore(self.path);d=s.character_profile(self.k,'03')['details'];self.assertEqual(d['body_visibility'],'私密');self.assertEqual(public_diy(d),{});self.assertEqual(self.before['snapshot'],s.read(self.k)['snapshot'])
  with s._db() as c:self.assertEqual(c.execute("select count(*) from neon_enrollment_audit where event='commit'").fetchone()[0],2)
 def test_unknown_age_rejected(self):
  with self.assertRaises(ValueError):self.request(dict(body_features='有阴茎'))
 def test_model_cannot_assert_adulthood_without_owner_statement(self):
  with self.assertRaises(ValueError):self.request(dict(adult_status='成年',body_features='有阴茎'),source='修改生殖器外观')
 def test_minor_age_and_conflicting_source_rejected(self):
  for text in ('我的角色未成年，身体外观：有阴茎','我的角色已成年但17岁，身体外观：有阴茎','我的角色不是成年人，身体外观：有阴茎','角色未满18岁，身体外观：有阴茎'):
   with self.assertRaises(ValueError):self.request(dict(adult_status='成年',body_features='有阴茎'),text)
 def test_normal_adult_age_not_misread_as_minor(self):
  self.request(dict(adult_status='成年',body_features='有阴茎'),'我的角色25岁，角色已成年，身体外观：有阴茎')
 def test_other_actor_cannot_modify_or_confirm(self):
  d=dict(adult_status='成年',body_features='有阴茎')
  with self.assertRaises(ValueError):self.request(d,actor='intruder')
  p=self.request(d)
  with self.assertRaises(ValueError):self.confirm(p,'intruder')
 def test_explicit_or_functional_content_denied(self):
  for content in ('性交','勃起','电击功能的阴茎','阴茎增加BODY','有阴茎并提升力量','义体生殖器'):
   with self.assertRaises(ValueError):self.request(dict(adult_status='成年',body_features=content))
 def test_private_field_cannot_be_smuggled_into_public_diy(self):
  for field in ('appearance','story','display_name','hobbies'):
   with self.assertRaises(ValueError):validate_diy({field:'有阴茎'})
 def test_owner_publication_requires_exact_new_confirmation(self):
  p=self.request(dict(adult_status='成年',body_features='有阴茎'));self.confirm(p)
  p=self.request(dict(body_visibility='公开'),'公开我的身体外观');self.assertNotIn('body_features',public_diy(self.s.character_profile(self.k,'03')['details']));self.confirm(p)
  self.assertEqual(public_diy(self.s.character_profile(self.k,'03')['details']),dict(body_features='有阴茎'))
  p=self.request(dict(body_visibility='私密'),'隐藏我的身体外观');self.confirm(p);self.assertEqual(public_diy(self.s.character_profile(self.k,'03')['details']),{})
 def test_replacement_returns_to_private_and_dedup_is_not_new_commit(self):
  p=self.request(dict(adult_status='成年',body_features='有阴茎',body_visibility='公开'));self.confirm(p)
  p=self.request(dict(body_features='有外阴'));r=self.confirm(p);rev=self.s.read(self.k)['revision']
  self.assertEqual(self.s.character_profile(self.k,'03')['details']['body_visibility'],'私密');self.confirm(p);self.assertEqual(rev,self.s.read(self.k)['revision'])
 def test_pause_prevents_both_request_and_commit(self):
  p=self.request(dict(adult_status='成年',body_features='有阴茎'));self.s.pause_group(self.k,'owner','pause')
  with self.assertRaises(ValueError):self.confirm(p)
  with self.assertRaises(ValueError):self.request(dict(body_visibility='公开'),'公开我的身体外观')
 def test_existing_cosmetics_unchanged(self):
  self.assertEqual(validate_diy(dict(appearance='戴橙色护目镜'))[1],'auto')

 def test_new_private_fields_retain_revision_and_expiry_checks(self):
  from unittest.mock import patch
  p=self.request(dict(adult_status='成年',body_features='有阴茎'))
  with patch('astrbot_plugin_neon_save.enrollment.time.time',return_value=p['expires']+1):
   with self.assertRaises(ValueError):self.confirm(p)
  self.assertEqual(self.s.character_profile(self.k,'03')['details'],{})
 def test_private_profile_commit_failure_rolls_back(self):
  from unittest.mock import patch
  p=self.request(dict(adult_status='成年',body_features='有阴茎'));before=self.s.read(self.k)
  original=self.s._enroll_log
  def fail(c,k,pid,actor,event,detail):
   if event=='commit':raise RuntimeError('injected rollback')
   return original(c,k,pid,actor,event,detail)
  with patch.object(self.s,'_enroll_log',side_effect=fail):
   with self.assertRaises(RuntimeError):self.confirm(p)
  self.assertEqual(before,self.s.read(self.k));self.assertEqual(self.s.character_profile(self.k,'03')['details'],{})

 def test_campaign_declaration_removes_repeated_adult_question_not_consent(self):
  (Path(self.path)/'body_adult_campaigns.json').write_text(json.dumps(dict(confirmed=True,groups=[self.k],source='explicit_user_declaration')))
  p=self.request(dict(body_features='有阴茎'),source='我想修改身体外观，生殖器外观：有阴茎')
  self.assertEqual(self.s.character_profile(self.k,'03')['details'],{})
  self.confirm(p);self.assertEqual(self.s.character_profile(self.k,'03')['details']['adult_status'],'成年')
 def test_campaign_declaration_does_not_override_minor_or_other_groups(self):
  (Path(self.path)/'body_adult_campaigns.json').write_text(json.dumps(dict(confirmed=True,groups=['qq_official:other'])))
  with self.assertRaises(ValueError):self.request(dict(body_features='有阴茎'),source='修改生殖器外观：有阴茎')
  (Path(self.path)/'body_adult_campaigns.json').write_text(json.dumps(dict(confirmed=True,groups=[self.k])))
  with self.assertRaises(ValueError):self.request(dict(body_features='有阴茎'),source='我的角色17岁，生殖器外观：有阴茎')
