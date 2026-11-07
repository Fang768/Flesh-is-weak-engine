"""Only explicitly audited public rule chapters; no GM plots or tools."""
import json,re
from pathlib import Path
RULES=json.loads(Path(__file__).with_name('public_rules.json').read_text())
def retrieve_rules(question):
 def terms(s):
  return set(re.findall(r'[a-zA-Z]{2,}',s.lower()))|{s[i:i+2] for i in range(len(s)-1) if all('\u4e00'<=c<='\u9fff' for c in s[i:i+2])}
 q=terms(question);ranked=sorted(((len(q&terms(x['content'])),x) for x in RULES),key=lambda x:x[0],reverse=True)
 return [dict(chapter=x['chapter'],content=x['content'][:1800]) for n,x in ranked[:4] if n>=2]
