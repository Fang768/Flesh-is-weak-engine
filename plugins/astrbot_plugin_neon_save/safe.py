"""Approval engine. Host command handlers are the identity trust boundary."""
import copy
import hashlib
import json
import secrets
import sqlite3
import statistics
import time
from pathlib import Path
from .store import NeonStore, SaveError, canonical, new_role, validate_snapshot, utcnow

TEMPLATES=json.loads(Path(__file__).with_name('templates.json').read_text('utf-8'))

def integer(v,lo,hi):
    if type(v) is not int or not lo<=v<=hi: raise SaveError('整数超出允许范围')
    return v

from .enrollment import EnrollmentMixin

class SafeStore(EnrollmentMixin,NeonStore):
    def __init__(self,data_dir):
        super().__init__(data_dir)
        # Additive tables only. Original saves/audit schema and snapshots remain v1.
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS neon_v020_proposals(id TEXT PRIMARY KEY, group_key TEXT, revision INTEGER, payload TEXT, preview TEXT, required TEXT, digest TEXT, expires REAL, status TEXT, actor TEXT);
            CREATE TABLE IF NOT EXISTS neon_v020_consents(pid TEXT, actor TEXT, digest TEXT, timestamp TEXT, PRIMARY KEY(pid,actor));
            CREATE TABLE IF NOT EXISTS neon_v020_rolls(id TEXT PRIMARY KEY, group_key TEXT, cid TEXT, purpose TEXT, dice TEXT, actor TEXT, created REAL, used TEXT);
            CREATE TABLE IF NOT EXISTS neon_safety_gate(group_key TEXT PRIMARY KEY,paused INTEGER NOT NULL,epoch INTEGER NOT NULL,pause_id TEXT NOT NULL,actor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS neon_safety_pause_actors(group_key TEXT,actor TEXT,PRIMARY KEY(group_key,actor));
            CREATE TABLE IF NOT EXISTS neon_safety_proposals(pid TEXT PRIMARY KEY,epoch INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS neon_v020_events(id INTEGER PRIMARY KEY, group_key TEXT, actor TEXT, action TEXT, detail TEXT, timestamp TEXT);
            ''')
        self.enrollment_migrate()
        # Adopt an explicit pre-upgrade NEXUS pause before any command can approve old proposals.
        legacy=self.data_dir.parent/'astrbot_plugin_nexus_gm'/'rehearsal.sqlite3'
        if legacy.exists():
            with sqlite3.connect('file:'+str(legacy)+'?mode=ro',uri=True) as control:
                for group,raw in control.execute('SELECT k,v FROM sessions'):
                    state=json.loads(raw);pauses=[e for e in state.get('log',[]) if e.get('method')=='pause']
                    if state.get('paused') and pauses and state['log'][-1].get('method')=='pause' and not self.gate_state(group)['paused']:
                        event=pauses[-1];owner=state['roles'].get(event['cid'],{}).get('qq_user_id','')
                        self.pause_group(group,owner,'legacy:'+state['task']+':'+event.get('message_id',''))
    def log(self,db,key,actor,action,detail):
        db.execute('INSERT INTO neon_v020_events(group_key,actor,action,detail,timestamp) VALUES(?,?,?,?,?)',(key,actor,action,detail,utcnow()))
    def roll(self,key,actor,cid,purpose,n,sides):
        integer(n,1,6); integer(sides,2,100)
        if purpose not in ('damage','reveal','check'): raise SaveError('骰子用途无效')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE'); self.assert_running(db,key); s=json.loads(self._row(db,key)['snapshot']); r=s['roles'].get(cid)
            if not r or r['status']!='active': raise SaveError('仅出场角色可掷骰')
            if purpose=='reveal':
                if r['qq_user_id']!=actor or r['dossier_stage']!='consented' or (n,sides)!=(1,10): raise SaveError('必须由本人先同意揭晓，再掷1d10')
                prior=db.execute("SELECT id,dice FROM neon_v020_rolls WHERE group_key=? AND cid=? AND purpose='reveal' ORDER BY created LIMIT 1",(key,cid)).fetchone()
                if prior: return {'id':prior['id'],'dice':json.loads(prior['dice'])}
            rid=secrets.token_hex(12); dice=[secrets.randbelow(sides)+1 for _ in range(n)]
            db.execute('INSERT INTO neon_v020_rolls VALUES(?,?,?,?,?,?,?,NULL)',(rid,key,cid,purpose,canonical(dice),actor,time.time()))
            self.log(db,key,actor,'roll',canonical({'id':rid,'cid':cid,'purpose':purpose,'n':n,'sides':sides,'dice':dice}))
            return {'id':rid,'dice':dice}
    def evidence(self,db,key,cid,purpose,rid):
        row=db.execute('SELECT * FROM neon_v020_rolls WHERE id=? AND group_key=? AND cid=? AND purpose=?',(rid,key,cid,purpose)).fetchone()
        if not row or row['used'] or (purpose!='reveal' and time.time()-row['created']>3600): raise SaveError('骰点证据不存在、已使用、过期或群/角色/用途不符')
        return json.loads(row['dice'])
    def apply(self,db,key,s,p,entry_time=None):
        entry_time = entry_time or utcnow()
        if not isinstance(p,dict): raise SaveError('提案必须为对象')
        if not isinstance(p.get('kind'),str) or not isinstance(p.get('cid',''),str): raise SaveError('提案类型或角色ID无效')
        if 'reason' in p and (not isinstance(p['reason'],str) or not 1<=len(p['reason'])<=250): raise SaveError('原因必须为1—250字')
        if p.get('kind')=='batch':
            if set(p)!={'kind','operations'} or not isinstance(p['operations'],list) or not 1<=len(p['operations'])<=10: raise SaveError('批次限1—10项')
            required=[]; rolls=[]
            for op in p['operations']:
                if not isinstance(op,dict) or op.get('kind') in ('batch','initialize'): raise SaveError('批次不允许嵌套或创建角色')
                req,ids=self.apply(db,key,s,op,entry_time); required+=req; rolls+=ids
            if len(rolls)!=len(set(rolls)): raise SaveError('同一批次不能重复使用骰点')
            return sorted(set(required)),rolls
        kind=p.get('kind'); cid=p.get('cid'); roles=s['roles']; required=[]; rolls=[]
        if kind=='initialize':
            if set(p)!={'kind','cid','user','nickname'} or cid not in TEMPLATES: raise SaveError('初始化参数无效')
            if cid in roles or any(r['qq_user_id']==p['user'] for r in roles.values()): raise SaveError('角色或玩家已绑定，禁止覆盖')
            t=copy.deepcopy(TEMPLATES[cid]); r=new_role(p['user'],cid,p['nickname'])
            xp=[r['cumulative_xp'] for r in roles.values() if r['status']=='active']
            if any(type(x) is not int for x in xp): raise SaveError('活跃角色累计XP未确认')
            catch=max(0,statistics.median(xp)-6) if xp else 0
            if int(catch)!=catch: raise SaveError('中位数产生半点XP，规则未指定取整，需先由团体裁定')
            r.update(job=t['job'],hp=t['hp'],max_hp=t['hp'],ram=t['ram'],max_ram=t['ram'],cash=t['cash'],stress=0,xp=int(catch),cumulative_xp=int(catch),attributes=t['attributes'],skills=t['skills'])
            r['skills']={k:r['skills'].get(k,0) for k in '运动 格斗 强韧 手枪 长枪 近战 驾驶 闪避 工程 爆破 医疗 开锁 网络技术 调查 电子学 潜行 交涉 洞察 威吓 街头关系 感知他人 安抚'.split()}
            r['equipment']=t['equipment']+['基础神经接口','通讯器']+[c['name'] for c in t['cyberware']]+[{'neon_v020':t}]; roles[cid]=r; required=[p['user']]
            if cid=='08':
                if any(isinstance(a,dict) and a.get('asset_id')=='rush_vehicle' for a in s['world']['team_assets']): raise SaveError('团队车辆已存在，禁止重复发放')
                s['world']['team_assets'].append({'asset_id':'rush_vehicle','name':'改装越野车','durability':40,'owner':'team','operator':'08'})
        elif kind=='world':
            if set(p)!={'kind','field','value','reason'} or p['field'] not in ('current_task_id','scene_status','scene','location','action_window','recap','alert_level'): raise SaveError('仅允许白名单世界字段，不支持自动团队资产表决')
            from .store import WORLD_FIELDS
            if not WORLD_FIELDS[p['field']](p['value']): raise SaveError('世界值无效')
            s['world'][p['field']]=p['value']
            required=[r['qq_user_id'] for r in roles.values() if r['status']=='active']
        else:
            r=roles.get(cid)
            if not r: raise SaveError('角色不存在')
            required=[r['qq_user_id']]
            if kind=='status':
                if set(p)-{'kind','cid','value','entry'} or not {'kind','cid','value'}<=set(p) or p['value'] not in ('active','away','retired','pending'): raise SaveError('状态无效')
                if p['value']=='active' and r['status']!='active':
                    entry=p.get('entry')
                    if not isinstance(entry,dict) or set(entry)!={'safe_node','in_combat','check_locked','scene','team'}: raise SaveError('入场需安全节点、战斗/锁定状态、场景及队伍')
                    if entry['safe_node'] is not True or entry['in_combat'] is not False or entry['check_locked'] is not False: raise SaveError('禁止战斗或锁定检定中入场')
                    if any(not isinstance(entry[f],str) or not 1<=len(entry[f].strip())<=250 for f in ('scene','team')): raise SaveError('入场场景及队伍必填')
                    r['equipment'].append({'neon_v022_entry':dict(entry, timestamp=entry_time)})
                r['status']=p['value']
            elif kind=='resource':
                if set(p)!={'kind','cid','field','delta','reason'} or p['field'] not in ('cash','ram','xp','stress'): raise SaveError('资源提案无效')
                if r['status']!='active': raise SaveError('暂离或未入场角色不可结算资源')
                f=p['field']; d=integer(p['delta'],-100000,100000)
                if type(r[f]) is not int: raise SaveError('未知数值不能当作0')
                r[f]+=d
                if f=='xp' and d>0: r['cumulative_xp']+=d
                if f=='stress': integer(r[f],0,6)
            elif kind=='damage':
                if set(p)!={'kind','cid','roll','bonus','arm','penetrating','reason'}: raise SaveError('伤害参数无效')
                if r['status']!='active' or not r['hp']: raise SaveError('仅支持未倒地出场角色；死亡标记需人工裁定')
                dice=self.evidence(db,key,cid,'damage',p['roll']); rolls=[p['roll']]
                integer(p['bonus'],0,20); integer(p['arm'],0,8)
                if type(p['penetrating']) is not bool: raise SaveError('穿透必须是布尔值')
                damage=max(0,sum(dice)+p['bonus']-(0 if p['penetrating'] else p['arm']))
                r['hp']=max(0,r['hp']-damage)
                s['world']['recent_roll']={'id':p['roll'],'dice':dice,'damage':damage,'cid':cid}
            elif kind=='mission':
                required_fields={'kind','cid','task','meaningful_action'}
                optional={'completed','survived','actually_present','completion_evidence','personal_major_goal','faction_shift','personal_goal_evidence','faction_shift_evidence'}
                if not required_fields<=set(p) or set(p)-required_fields-optional or not isinstance(p['meaningful_action'],str) or not 1<=len(p['meaningful_action'])<=500: raise SaveError('需有效行动证据说明，禁止直接指定XP')
                for flag,evidence in (('personal_major_goal','personal_goal_evidence'),('faction_shift','faction_shift_evidence')):
                    if type(p.get(flag,False)) is not bool: raise SaveError('奖励条件必须为布尔值')
                    if evidence in p and (not isinstance(p[evidence],str) or not 1<=len(p[evidence].strip())<=500): raise SaveError('奖励证据须为1—500字')
                    if p.get(flag,False) and evidence not in p: raise SaveError('额外XP必须提供公开证据供本人及管理员审核')
                if s['world']['scene_status'] not in ('active','complete'): raise SaveError('尚未开场或暂停中，禁止结算')
                if any(p.get(f) is not True for f in ('completed','survived','actually_present')): raise SaveError('任务须完成、幸存且实际出场')
                if not isinstance(p.get('completion_evidence'),str) or not 1<=len(p['completion_evidence'].strip())<=500 or not p['meaningful_action'].strip(): raise SaveError('须公开任务完成与有效参与证据')
                # Chapter 25 supersedes chapter 09: 3 base + 2 personal + 1 faction, max 6.
                xp_award=3+2*int(p.get('personal_major_goal',False))+int(p.get('faction_shift',False))
                if not 3<=xp_award<=6: raise SaveError('单任务XP超出第25章上限')
                if r['status']!='active' or p['task']!=s['world']['current_task_id'] or not p['task']: raise SaveError('必须匹配当前任务及出场角色')
                if p['task'] in r['quest_participation']: raise SaveError('重复结算任务')
                if type(r['xp']) is not int or type(r['cumulative_xp']) is not int: raise SaveError('XP未确认')
                r['quest_participation'].append(p['task']); r['xp']+=xp_award; r['cumulative_xp']+=xp_award
                if r['dossier_progress']<3:
                    r['dossier_completed_tasks'].append(p['task']); r['dossier_progress']+=1
                    if r['dossier_progress']==3:r['dossier_stage']='ready'
            elif kind=='omen':
                if set(p)!={'kind','cid','omen'} or not isinstance(p['omen'],str) or not 1<=len(p['omen'])<=500 or r['dossier_stage']!='ready': raise SaveError('需3/3及公开无数值异象')
                r['dossier_stage']='consented' # exact proposal consent proves A then B
            elif kind=='reveal':
                if set(p)!={'kind','cid','roll','fact','clue','reward'} or r['dossier_stage']!='consented': raise SaveError('需先完成异象及本人同意')
                if p['reward'] not in ('线索共鸣','行动直觉','义体升级线索'): raise SaveError('奖励不符合第40章')
                for f in ('fact','clue'):
                    if not isinstance(p[f],str) or not 1<=len(p[f])<=400: raise SaveError('事实和公共线索必填')
                dice=self.evidence(db,key,cid,'reveal',p['roll']); rolls=[p['roll']]
                r.update(dossier_stage='revealed',dossier_category_id=dice[0],dossier_content=p['fact']+'；线索：'+p['clue'],dossier_public_evidence=p['roll'],reward_option=p['reward'],reward_uses=0)
            elif kind=='restore_role':
                if set(p)!={'kind','cid','source_revision'}: raise SaveError('恢复参数无效')
                integer(p['source_revision'],1,2147483647)
                row=db.execute('SELECT after_json FROM audit WHERE group_key=? AND revision=?',(key,p['source_revision'])).fetchone()
                old=json.loads(row[0])['roles'].get(cid) if row else None
                if not old or old['qq_user_id']!=r['qq_user_id']: raise SaveError('历史角色不存在或归属不符')
                roles[cid]=old
            else: raise SaveError('不支持的提案类型')
        validate_snapshot(s)
        return sorted(set(required)),rolls
    def propose(self,key,actor,revision,payload):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE'); self.assert_running(db,key); row=self._row(db,key)
            if type(revision) is not int or row['revision']!=revision: raise SaveError('版本冲突，请重新查询')
            s=json.loads(row['snapshot']); validate_snapshot(s)
            req,_=self.apply(db,key,s,payload)
            text=canonical(payload)
            if len(text)>12000: raise SaveError('提案过大')
            preview=canonical(s); digest=hashlib.sha256((str(revision)+text+preview).encode()).hexdigest()
            pid=secrets.token_hex(12)
            db.execute('INSERT INTO neon_v020_proposals VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,key,revision,text,preview,canonical(req),digest,time.time()+1800,'pending',actor))
            db.execute('INSERT INTO neon_safety_proposals VALUES(?,?)',(pid,self.gate_state(key,db)['epoch']))
            self.log(db,key,actor,'propose',pid+':'+text)
            return {'id':pid,'digest':digest,'revision':revision,'payload':payload,'result':s,'required':req}
    def proposal(self,db,key,pid):
        p=db.execute('SELECT * FROM neon_v020_proposals WHERE id=? AND group_key=?',(pid,key)).fetchone()
        if not p or p['status']!='pending' or p['expires']<time.time(): raise SaveError('提案不存在、已处理或过期')
        return p
    def consent(self,key,actor,pid,digest,accept=True):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE'); self.assert_running(db,key,pid); p=self.proposal(db,key,pid)
            if actor not in json.loads(p['required']) or digest!=p['digest']: raise SaveError('只有提案相关玩家本人可确认且摘要必须一致')
            if self._row(db,key)['revision']!=p['revision']: raise SaveError('提案已过时')
            if accept: db.execute('INSERT OR REPLACE INTO neon_v020_consents VALUES(?,?,?,?)',(pid,actor,digest,utcnow()))
            else: db.execute("UPDATE neon_v020_proposals SET status='rejected' WHERE id=?",(pid,))
            self.log(db,key,actor,'consent' if accept else 'reject',pid+':'+digest)
    def approve_narrative(self,key,pid,digest):
        policy=self.data_dir/'narrative_auto.json'
        try: allowed=json.loads(policy.read_text()).get('groups',[])
        except (OSError,ValueError): allowed=[]
        if key not in allowed: raise SaveError('本群未授权自动记录剧情')
        return self._approve(key,'system:narrative-auto',pid,digest,True,narrative=True)
    def approve(self,key,actor,pid,digest,is_admin=False):
        return self._approve(key,actor,pid,digest,is_admin)
    def _approve(self,key,actor,pid,digest,is_admin=False,narrative=False):
        if is_admin is not True: raise SaveError('只有AstrBot管理员可以提交')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE'); self.assert_running(db,key,pid); p=self.proposal(db,key,pid); row=self._row(db,key)
            if row['revision']!=p['revision'] or digest!=p['digest']: raise SaveError('版本或提案摘要冲突')
            if narrative:
                payload=json.loads(p['payload'])
                ops=payload.get('operations',[])
                if not p['actor'].startswith('system:dne:') or payload.get('kind')!='batch' or not ops or any(op.get('kind')!='world' or op.get('field') not in ('recap','scene','alert_level','action_window') for op in ops): raise SaveError('自动记录仅限主持生成的剧情状态，不允许角色数值或开场操作')
                self.log(db,key,actor,'narrative_policy_approval',pid+':'+digest)
            for user in ([] if narrative else json.loads(p['required'])):
                if not db.execute('SELECT 1 FROM neon_v020_consents WHERE pid=? AND actor=? AND digest=?',(pid,user,digest)).fetchone(): raise SaveError('尚缺玩家本人明确同意')
            old=json.loads(row['snapshot']); s=copy.deepcopy(old)
            # Reuse the proposal's immutable entry time, including batch transitions.
            preview=json.loads(p['preview']); entry_times=set()
            for cid,role in preview['roles'].items():
                previous=old['roles'].get(cid,{}).get('equipment',[])
                for item in role['equipment'][len(previous):]:
                    if isinstance(item,dict) and 'neon_v022_entry' in item:
                        entry_times.add(item['neon_v022_entry']['timestamp'])
            if len(entry_times)>1: raise SaveError('旧批次入场时间不一致，请重建提案')
            _,rolls=self.apply(db,key,s,json.loads(p['payload']),next(iter(entry_times),None))
            if json.loads(p['payload'])['kind']=='initialize':
                cid=json.loads(p['payload'])['cid']; s['roles'][cid]['joined_at']=json.loads(p['preview'])['roles'][cid]['joined_at']
            if canonical(s)!=p['preview']: raise SaveError('预览已变化，拒绝提交')
            if canonical(s)==canonical(old): raise SaveError('没有变化')
            # Pre-image backup and audit share the commit; external full DB backup available separately.
            self.log(db,key,actor,'preimage',canonical({'revision':row['revision'],'snapshot':old}))
            rev=row['revision']+1; now=utcnow()
            db.execute('UPDATE saves SET snapshot=?,revision=?,updated_by=?,updated_at=? WHERE group_key=?',(canonical(s),rev,actor,now,key))
            db.execute('INSERT INTO audit(group_key,revision,actor,action,reason,before_json,after_json,timestamp) VALUES(?,?,?,?,?,?,?,?)',(key,rev,actor,'safe_'+json.loads(p['payload'])['kind'],pid+':'+digest,canonical(old),canonical(s),now))
            for rid in rolls: db.execute('UPDATE neon_v020_rolls SET used=? WHERE id=?',(pid,rid))
            db.execute("UPDATE neon_v020_proposals SET status='committed' WHERE id=?",(pid,))
            self.log(db,key,actor,'approve',pid+':'+digest)
            return rev
    def backup_database(self):
        folder=self.data_dir/'database_backups';folder.mkdir(exist_ok=True)
        path=folder/(secrets.token_hex(12)+'.sqlite3')
        with sqlite3.connect(str(self.db_path)) as src, sqlite3.connect(str(path)) as dst: src.backup(dst)
        return path.name

    def gate_state(self,key,db=None):
        if db is None:
            with self._db() as conn:return self.gate_state(key,conn)
        row=db.execute('SELECT * FROM neon_safety_gate WHERE group_key=?',(self._key(key),)).fetchone()
        result=dict(row) if row else dict(paused=0,epoch=0,pause_id='',actor='')
        result['actors']=[x['actor'] for x in db.execute('SELECT actor FROM neon_safety_pause_actors WHERE group_key=? ORDER BY actor',(self._key(key),))]
        return result
    def assert_running(self,db,key,pid=None):
        gate=self.gate_state(key,db)
        if gate['paused']:raise SaveError('本群安全暂停中，禁止骰点、提案授权和结算')
        if pid:
            row=db.execute('SELECT epoch FROM neon_safety_proposals WHERE pid=?',(pid,)).fetchone()
            if (row['epoch'] if row else 0)!=gate['epoch']:raise SaveError('暂停恢复后旧提案失效，请重新生成并重新同意')
    def pause_group(self,key,actor,pause_id):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');g=self.gate_state(key,db)
            if g['paused'] and g['pause_id']==pause_id:return g
            if not g['paused']:db.execute('DELETE FROM neon_safety_pause_actors WHERE group_key=?',(self._key(key),))
            if actor:db.execute('INSERT OR IGNORE INTO neon_safety_pause_actors VALUES(?,?)',(self._key(key),actor))
            epoch=g['epoch']+1
            db.execute('INSERT OR REPLACE INTO neon_safety_gate VALUES(?,1,?,?,?)',(self._key(key),epoch,pause_id,actor))
            self.log(db,key,actor,'safety_pause',canonical({'epoch':epoch,'pause_id':pause_id}))
            return self.gate_state(key,db)
    def release_group(self,key,actor,expected_epoch):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE');g=self.gate_state(key,db)
            if not g['paused'] or g['epoch']!=expected_epoch:raise SaveError('暂停版本已变化，恢复授权失效')
            db.execute('UPDATE neon_safety_gate SET paused=0,epoch=epoch+1 WHERE group_key=?',(self._key(key),))
            self.log(db,key,actor,'safety_resume',canonical({'epoch':expected_epoch+1}))
            return self.gate_state(key,db)
