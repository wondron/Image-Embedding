# QA ONLY: extra actual-HTTP directory/default/alias validation against OUR isolated GUI service.
import json,pathlib,httpx,uuid,traceback
from datetime import datetime
from zoneinfo import ZoneInfo
q=pathlib.Path('.qa');ready=json.loads((q/'scene-completion-gui-ready.json').read_text(encoding='utf-8'));assert ready['db']=='qa_scene_completion_gui_1055';assert ready['collection']=='qa_scene_completion_gui_1055';assert ready['api_url'].startswith('http://127.0.0.1:')
checks=[];requests=[]
def check(n,b):
 checks.append({'name':n,'pass':bool(b)});assert b,n
png=(q/'scene-completion-query.png').read_bytes()
with httpx.Client(base_url=ready['api_url'],timeout=10,trust_env=False) as c:
 def req(m,p,expected=200,**kw):
  r=c.request(m,p,**kw);j=r.json();requests.append({'method':m,'path':p,'status':r.status_code,'body':j,'trace':r.headers.get('x-trace-id')});check(f'{m} {p} HTTP{expected}',r.status_code==expected);check('response trace header exists',bool(r.headers.get('x-trace-id')));return j
 try:
  d=req('GET','/api/scenes');general=next(x for x in d['scenes'] if x['code']=='general');check('active protected general default dynamically reported',d['default_scene']=='general' and general['include_all'] and general['is_active']);gid=general['id']
  for body in [{'is_active':False},{'include_all':False}]:
   e=req('PATCH',f'/api/scenes/{gid}',403,json=body);check('default protection code',e['error']=='SCENE_PROTECTED')
  e=req('DELETE',f'/api/scenes/{gid}',403);check('default delete protected',e['error']=='SCENE_PROTECTED')
  for value,canonical in [(None,'general'),('normal','general'),('device','oven')]:
   path='/api/images'+('' if value is None else '?scene='+value);x=req('GET',path);check('list canonical default/alias',x['scene']==canonical)
   data={} if value is None else {'scene':value};x=req('POST','/api/search',files={'file':('qa.png',png,'image/png')},data=data);check('search canonical default/alias',x['scene']==canonical)
  name='qa_directory_'+uuid.uuid4().hex[:6]
  created=req('POST','/api/scenes',201,json={'code':name,'name':'QA dynamic directory','include_all':True});sid=created['scene']['id']
  d=req('GET','/api/scenes');check('new scene in real directory',any(x['id']==sid and x['code']==name for x in d['scenes']))
  req('PATCH',f'/api/scenes/{sid}',json={'name':'QA edited','is_active':False})
  d=req('GET','/api/scenes');check('inactive edited scene remains visible',any(x['id']==sid and x['name']=='QA edited' and x['is_active'] is False for x in d['scenes']))
  before=req('GET','/api/images')['total']
  for value in ['Normal','OVEN','qa_missing_123',name]:
   e=req('GET','/api/images?scene='+value,422);check('unknown/inactive list error',e['error']=='LIST_UNKNOWN_SCENE')
   e=req('POST','/api/search',422,files={'file':('qa.png',png,'image/png')},data={'scene':value});check('unknown/inactive search error',e['error']=='SEARCH_UNKNOWN_SCENE')
   e=req('POST','/api/index',422,files={'file':('qa.png',png,'image/png')},data={'scenes':json.dumps([value])});check('unknown/inactive index error',e['error']=='INDEX_UNKNOWN_SCENE')
  check('rejected index did not add images',req('GET','/api/images')['total']==before)
  req('PATCH',f'/api/scenes/{sid}',json={'is_active':True});req('GET','/api/images?scene='+name)
  req('DELETE',f'/api/scenes/{sid}');d=req('GET','/api/scenes');check('deleted dynamic scene no longer in directory',not any(x['id']==sid for x in d['scenes']))
  code=0
 except BaseException:
  traceback.print_exc();code=1
r={'exit_code':code,'target':ready['api_url'],'db':ready['db'],'collection':ready['collection'],'evidence':'actual FastAPI HTTP + actual isolated PG; actual Qdrant search; deterministic encoder/memory OSS substitutes','checks':checks,'requests':requests,'assertions_total':len(checks),'assertions_passed':sum(x['pass'] for x in checks),'api_requests':len(requests),'reported_at':datetime.now(ZoneInfo('America/Denver')).isoformat()};(q/'scene-completion-directory-http-results.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in r.items() if k not in ['checks','requests']},ensure_ascii=False));raise SystemExit(code)
