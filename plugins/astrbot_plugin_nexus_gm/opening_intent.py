"""Recognize explicit launch requests before chat; questions and quotations never launch."""
import re,unicodedata
START_PHRASES=r'(?:开始第一幕|开第一幕|第一幕开始|开始跑团|开始游戏|开始玩|正式开团|正式开场|正式开始|开团|开场|启动跑团|启动游戏|启动|开始)'
def opening_intent(text):
 t=unicodedata.normalize('NFKC',text).strip()
 if t.startswith('/'):return None
 # Quoted, hypothetical or third-party words are data, never authorization.
 if re.search(r'[“”「」『』"`]|如果|假如|假设|要是|比如|他说|她说|有人说|转述|这句话',t):return None
 if re.search(r'(?:不要|别|先不|暂不|不想|不打算|禁止).{0,4}(?:开团|开场|开始|启动)',t):return None
 if re.search(r'(?:什么时候|啥时候|何时|几点|怎么还|为什么还|为啥还|怎么才能|如何).{0,12}(?:开场|开团|开始)|(?:开场|开团).{0,10}(?:条件|缺什么|需要什么)|(?:非要|必须|一定要).{0,8}人齐|(?:能不能|可以|能).{0,5}(?:开团|开场|开始).{0,3}[吗?？]',t):return 'status'
 if re.search(r'[?？]|是否|是不是',t):
  return 'status' if re.search(r'开团|开场|开始|第一幕|启动',t) else None
 clean=re.sub(r'[\s，,。！!]+','',t)
 prefix=r'(?:(?:请|帮忙|现在|就|我们|咱们|大家|好|好了|那|那就|直接|正式|马上|立刻|赶紧|今天|今晚|我决定|我宣布|我想|可以|受不了啦|不等人了|NEXUS))*'
 return 'start' if re.fullmatch(prefix+START_PHRASES+r'(?:吧|啦|了|咯|呀|啊|喽)*',clean,re.I) else None
