"""Serialize brief opening/admission transactions, including nested start -> arrive."""
import fcntl,threading
from functools import wraps
from pathlib import Path
_registry={};_registry_lock=threading.Lock();_local=threading.local()
def serialized(method):
 @wraps(method)
 def call(self,*args,**kwargs):
  key=str(Path(self.path).resolve())
  with _registry_lock:lock=_registry.setdefault(key,threading.RLock())
  with lock:
   depths=getattr(_local,'depths',{});_local.depths=depths;depth=depths.get(key,0);handle=None
   if not depth:
    handle=Path(self.path).with_suffix('.lock').open('a+');fcntl.flock(handle,fcntl.LOCK_EX)
   depths[key]=depth+1
   try:return method(self,*args,**kwargs)
   finally:
    depths[key]-=1
    if handle:fcntl.flock(handle,fcntl.LOCK_UN);handle.close()
 return call
