"""Resolve the paired plugin in AstrBot's namespace as well as standalone tests."""
import importlib
_parent=__package__.rpartition('.')[0]
_module=importlib.import_module((_parent+'.' if _parent else '')+'astrbot_plugin_neon_save.body_privacy')
public_diy=_module.public_diy
ANATOMY=_module.ANATOMY
