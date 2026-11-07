"""Mocked AstrBot API smoke test. This is NOT a real AstrBot runtime test."""
import asyncio
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class Event:
    def __init__(self, group="group-one", is_admin=True):
        self.group = group
        self.admin = is_admin
        self.out = []
    def is_admin(self): return self.admin
    def get_group_id(self): return self.group
    def get_sender_id(self): return "open-id-1"
    def get_platform_name(self): return "qq_official"
    def plain_result(self, s): return s


def make_stubs(temp):
    roots = {k: types.ModuleType(k) for k in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.core",
        "astrbot.core.utils", "astrbot.core.utils.astrbot_path")}
    class Decorators:
        class PermissionType: ADMIN = "ADMIN"
        def command(self, name): return lambda fn: fn
        def permission_type(self, permission): return lambda fn: fn
        def llm_tool(self, name): return lambda fn: fn
    roots["astrbot.api.event"].filter = Decorators()
    roots["astrbot.api.event"].AstrMessageEvent = Event
    class Star:
        def __init__(self, context): self.context=context
    roots["astrbot.api.star"].Star=Star
    roots["astrbot.api.star"].Context=object
    roots["astrbot.core.utils.astrbot_path"].get_astrbot_plugin_data_path=lambda: temp
    return roots


async def collect(agen):
    return [x async for x in agen]


class TestPluginAdapter(unittest.TestCase):
    def test_import_and_guarded_handlers_with_stub_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(sys.modules, make_stubs(tmp)):
                sys.modules.pop("astrbot_plugin_neon_save.main", None)
                mod=importlib.import_module("astrbot_plugin_neon_save.main")
                plugin=mod.NeonSavePlugin(object())
                unauthorized=Event(is_admin=False)
                denied=asyncio.run(collect(plugin.neon_init(unauthorized)))
                self.assertIn("只有AstrBot管理员", denied[0])
                event=Event()
                out=asyncio.run(collect(plugin.neon_init(event)))
                self.assertIn("revision=1",out[0])
                output=[asyncio.run(plugin.neon_read_save(event,"summary"))]
                self.assertIn("not_started", output[0])
                self.assertIn('"revision": 1', output[0])
                outsider=Event(group="another-group")
                out=asyncio.run(collect(plugin.neon_status(outsider)))
                self.assertIn("尚未初始化",out[0])
                sys.modules.pop("astrbot_plugin_neon_save.main", None)

if __name__ == "__main__": unittest.main()

class TestSafeAdapter(unittest.TestCase):
    def test_tools_and_direct_admin_bypass(self):
        import inspect
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules,make_stubs('unused')):
            # Bind actual temporary persistent path for this test.
            sys.modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:tmp
            sys.modules.pop('astrbot_plugin_neon_save.main',None)
            mod=importlib.import_module('astrbot_plugin_neon_save.main');plugin=mod.NeonSavePlugin(object())
            asyncio.run(collect(plugin.neon_init(Event())))
            event=Event(is_admin=False)
            out=asyncio.run(collect(plugin.neon_claim(event,1,'03','扳手')))
            self.assertIn('待审核提案',out[0]);self.assertEqual(plugin.store.read('qq_official:group-one')['revision'],1)
            with plugin.store._db() as db:p=db.execute('SELECT * FROM neon_v020_proposals').fetchone()
            asyncio.run(collect(plugin.neon_consent(event,p['id'],p['digest'])))
            denied=asyncio.run(collect(plugin.neon_approve(event,p['id'],p['digest'])))
            self.assertIn('只有AstrBot管理员',denied[0]);self.assertEqual(plugin.store.read('qq_official:group-one')['revision'],1)
            out=asyncio.run(collect(plugin.neon_approve(Event(),p['id'],p['digest'])))
            self.assertIn('revision=2',out[0])
            self.assertTrue(inspect.iscoroutinefunction(plugin.neon_read_save));self.assertTrue(inspect.iscoroutinefunction(plugin.neon_propose_change))
            out=asyncio.run(collect(plugin.neon_set(Event(),2,'role:03','cash','9999')))
            self.assertIn('已停用',out[0]);self.assertEqual(plugin.store.read('qq_official:group-one')['revision'],2)
            sys.modules.pop('astrbot_plugin_neon_save.main',None)
