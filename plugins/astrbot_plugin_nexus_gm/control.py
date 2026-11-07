"""Version-bound unanimous resume authorization. Does not change game snapshots."""
import json,hashlib,secrets

class PauseControl:
 def is_paused(self,k):
  with self.db() as c:s=self.load(c,k)
  return bool(s['paused'] or self.save.gate_state(k)['paused'])
 def epoch(self,k):return self.save.gate_state(k)['epoch']
 def assert_epoch(self,k,expected=None):
  g=self.save.gate_state(k)
  if g['paused'] or expected is not None and g['epoch']!=expected:raise self.control_error('安全暂停或控制版本已变化，旧请求已取消；没有追加骰点或推进')
 def checkpoint(self,s):
  fields=('task','revision','roles','stage','round','done','pending','success','complete')
  body={x:s[x] for x in fields}
  if s.get('onboarding_only'):body['control_mode']='onboarding_only'
  return hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
 def required_resume(self,s,g):
  return sorted({r['qq_user_id'] for r in s['roles'].values()}|set(s.get('pause_actors',[]))|set(g.get('actors',[]))|({g['actor']} if g.get('actor') else set()))
 def request_resume(self,k,actor,is_admin=False):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=self.load(c,k);g=self.save.gate_state(k)
   if not s['paused'] or not g['paused']:raise self.control_error('当前不是安全暂停，无需恢复申请')
   required=self.required_resume(s,g)
   if actor not in required and is_admin is not True:raise self.control_error('仅相关玩家或管理员可申请恢复')
   if self.save.read(k)['revision']!=s['revision']:raise self.control_error('真实存档已变化，不能自动恢复旧窗口')
   old=s.get('resume_request');checkpoint=self.checkpoint(s)
   if old and old['epoch']==g['epoch'] and old['checkpoint']==checkpoint and old['expires']>self.clock() and old['status'] in ('pending','authorized'):return old
   rid=secrets.token_hex(12);payload=dict(id=rid,epoch=g['epoch'],checkpoint=checkpoint,required=required,expires=self.clock()+1800,task=s['task'],revision=s['revision'],stage=s['stage'],round=s['round'],done=list(s['done']),pending=dict(s['pending']))
   payload['digest']=hashlib.sha256(json.dumps(dict(group=k,**payload),sort_keys=True).encode()).hexdigest();payload['status']='pending'
   payload['labels']=[cid for cid,r in s['roles'].items() if r['qq_user_id'] in required]
   if len(required)>len(payload['labels']):payload['labels'].append('暂停发起者')
   c.execute('INSERT INTO resume_requests VALUES(?,?,?)',(k,rid,json.dumps(payload,ensure_ascii=False)))
   s['resume_request']=payload;self.write(c,k,s,{'resume_request':rid,'actor':actor,'epoch':g['epoch']});return payload
 def _resume(self,c,k,s,rid,digest):
  r=s.get('resume_request');g=self.save.gate_state(k)
  if not r or r['id']!=rid or r['digest']!=digest or r['status'] not in ('pending','authorized','resumed'):raise self.control_error('恢复申请或摘要无效，请重新查询')
  signed={field:r[field] for field in ('id','epoch','checkpoint','required','expires','task','revision','stage','round','done','pending')}
  expected_digest=hashlib.sha256(json.dumps(dict(group=k,**signed),sort_keys=True).encode()).hexdigest()
  if expected_digest!=r['digest']:raise self.control_error('恢复申请完整性校验失败，保持暂停')
  if r['status']!='resumed' and r['required']!=self.required_resume(s,g):raise self.control_error('相关玩家范围变化，必须重新申请和确认')
  if r['status']=='resumed' and not g['paused'] and g['epoch']==r['epoch']+1:return r,g
  if r['expires']<=self.clock() or r['checkpoint']!=self.checkpoint(s) or self.save.read(k)['revision']!=r['revision']:raise self.control_error('恢复申请已过期或安全状态变化，必须重新申请和确认')
  expected=r['epoch']+(1 if r['status']=='resumed' else 0)
  if r['status']=='authorized':valid=(g['paused'] and g['epoch']==r['epoch']) or (not g['paused'] and g['epoch']==r['epoch']+1)
  else:valid=g['epoch']==expected and bool(g['paused'])==(r['status']!='resumed')
  if not valid:raise self.control_error('出现了新的暂停或控制版本，旧恢复确认无效')
  return r,g
 def consent_resume(self,k,actor,rid,digest,accept=True):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=self.load(c,k)
   previous=s.get('resume_request')
   if not accept and previous and previous['id']==rid and previous['digest']==digest and previous['status']=='rejected' and actor in previous['required']:return '该申请已拒绝，保持暂停'
   r,g=self._resume(c,k,s,rid,digest)
   if actor not in r['required']:raise self.control_error('只能由相关玩家本人确认恢复')
   if r['status']=='resumed':return '该版本已恢复，不重复执行'
   if not accept:
    # A refusal racing admin authorization closes a fresh gate epoch first.
    self.save.pause_group(k,actor,'resume-rejected:'+rid+':'+actor)
    s['paused']=True;s['pause_actors']=sorted(set(s.get('pause_actors',[]))|{actor})
    r['status']='rejected';s['resume_request']=r;self.write(c,k,s,{'resume_rejected':rid,'actor':actor});return '已拒绝恢复，保持暂停，无需解释原因'
   c.execute('INSERT OR IGNORE INTO resume_consents VALUES(?,?,?,?)',(k,rid,actor,digest))
   self.write(c,k,s,{'resume_consent':rid,'actor':actor});return '已记录本人愿意恢复；仍保持暂停，等待全员确认和管理员授权'
 def approve_resume(self,k,actor,rid,digest,is_admin=False):
  if is_admin is not True:raise self.control_error('只有管理员可授权恢复')
  # Persist authorization first; shared gate stays closed until every check has passed.
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=self.load(c,k);r,g=self._resume(c,k,s,rid,digest)
   if r['status']=='resumed':return '该版本已恢复，不重复执行'
   for user in r['required']:
    if not c.execute('SELECT 1 FROM resume_consents WHERE k=? AND rid=? AND actor=? AND digest=?',(k,rid,user,digest)).fetchone():raise self.control_error('尚缺相关玩家本人愿意继续的明确确认，保持暂停')
   r['status']='authorized';s['resume_request']=r;s['paused']=False
   self.write(c,k,s,{'resume_authorized':rid,'actor':actor})
  # Crash before this step: gate remains closed. Retrying uses persisted authorization.
  if g['paused']:
   try:g=self.save.release_group(k,actor,r['epoch'])
   except Exception:
    current_gate=self.save.gate_state(k)
    if current_gate['paused'] or current_gate['epoch']!=r['epoch']+1:raise
    g=current_gate
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=self.load(c,k);current=s.get('resume_request');gate=self.save.gate_state(k)
   if not current or current['id']!=rid or current['status'] not in ('authorized','resumed') or gate['paused'] or gate['epoch']!=r['epoch']+1:raise self.control_error('恢复期间出现新暂停，保持安全停止')
   if current['status']=='resumed':return '该版本已恢复，不重复执行'
   s['paused']=False;current['status']='resumed';s['resume_request']=current;s['pause_actors']=[]
   self.write(c,k,s,{'resume_committed':rid,'actor':actor,'epoch':gate['epoch']})
  return '已按全员确认及管理员授权恢复原窗口；未重掷、未重复行动、未修改角色存档'
