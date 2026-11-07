import asyncio,importlib,json,sys,tempfile,unittest
from unittest.mock import patch
from .test_adapter_stub import make_stubs,Event,collect
class ClaimToolGuard(unittest.TestCase):
 def test_llm_cannot_construct_initialization_or_reported_wrong_payload(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,make_stubs('unused')):
   sys.modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:tmp
   sys.modules.pop('astrbot_plugin_neon_save.main',None);mod=importlib.import_module('astrbot_plugin_neon_save.main');p=mod.NeonSavePlugin(object());e=Event();asyncio.run(collect(p.neon_init(e)))
   for payload in (dict(type='claim_character',role_id='03',nickname='扳手'),dict(kind='initialize',cid='03',user='forged',nickname='扳手')):
    out=asyncio.run(p.neon_propose_change(e,1,json.dumps(payload)));self.assertIn('模型不能',out)
   with p.store._db() as c:self.assertEqual(c.execute('select count(*) from neon_v020_proposals').fetchone()[0],0)
   self.assertEqual(p.store.read('qq_official:group-one')['snapshot']['roles'],{})
   sys.modules.pop('astrbot_plugin_neon_save.main',None)
