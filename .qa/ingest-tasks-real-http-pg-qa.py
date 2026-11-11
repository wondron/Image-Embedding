"""QA supervisor: real current FastAPI route + temporary PG16/migrations. No browser."""
import sys,pathlib,json,subprocess,uuid,datetime,time,socket,os,traceback,hashlib
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parents[1];BACKEND=ROOT/'backend';Q=ROOT/'.qa';sys.path.insert(0,str(BACKEND))
RUN=uuid.uuid4().hex;PGPORT=57512;APIPORT=57511;BASE=f'http://127.0.0.1:{APIPORT}';DB=f'postgresql+psycopg://qa_ingest@127.0.0.1:{PGPORT}/qa_ingest'
STATE={'run_id':RUN,'started_mst':datetime.datetime.now(ZoneInfo('America/Denver')).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'targets':{'api':BASE,'pg':f'127.0.0.1:{PGPORT}/qa_ingest','postgres_image':'postgres:16'},'assertions':[],'supervisor_exchanges':[],'api_processes':[],'cleanup':[],'scope':'Real TCP HTTP via unchanged create_app route/service/use case/repository and migrated isolated PG16. QA overrides only lifespan/external bootstrap. QA fixture rows are NOT actual index/upload outcomes. No OSS/Nacos/encoder/Qdrant or browser.','exception':None};cid=None;api=None;log=None;engine=None;store=None

def save():
 (Q/'ingest-tasks-real-http-pg-results.json').write_text(json.dumps(STATE,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def check(name,condition,detail=None):
 STATE['assertions'].append({'name':name,'pass':bool(condition),'detail':detail})
 print(('PASS ' if condition else 'PRODUCT_FAIL ')+name,flush=True);save()
 if not condition:raise AssertionError(name)
def command(args,timeout=60):
 p=subprocess.run(args,capture_output=True,text=True,timeout=timeout,cwd=ROOT)
 if p.returncode:raise RuntimeError(f'COMMAND {args!r} exit={p.returncode}\n{p.stdout}\n{p.stderr}')
 return p.stdout.strip()
def free(port):
 with socket.socket() as s:
  try:s.bind(('127.0.0.1',port));return True
  except OSError:return False

def stop_api():
 global api,log
 if api is None:return
 item={'kind':'api','pid':api.pid}
 if api.poll() is None:
  identity=command(['powershell','-NoProfile','-Command',f'(Get-CimInstance Win32_Process -Filter "ProcessId={api.pid}").CommandLine'])
  item['command_line']=identity;item['matched']='ingest-tasks-real-app.py' in identity and RUN in identity
  if not item['matched']:STATE['cleanup'].append(item);save();raise RuntimeError('QA API PID identity mismatch; not stopped')
  api.terminate()
  try:api.wait(timeout=8)
  except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
 item['exit_code']=api.poll();item['port_closed']=free(APIPORT);STATE['cleanup'].append(item);api=None
 if log:log.close();log=None
 save()

def start_api(phase):
 global api,log
 if not free(APIPORT):raise RuntimeError('QA API port occupied; no existing process touched')
 log=(Q/f'ingest-tasks-real-api-{phase}.log').open('w',encoding='utf-8')
 args=[sys.executable,'-u',str(Q/'ingest-tasks-real-app.py'),'--run-id',RUN,'--pg-port',str(PGPORT),'--port',str(APIPORT),'--phase',phase]
 api=subprocess.Popen(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
 STATE['api_processes'].append({'phase':phase,'pid':api.pid,'command':args});save()
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  if api.poll() is not None:raise RuntimeError(f'QA API failed to start phase={phase} exit={api.returncode}; see exact phase log')
  try:
   with socket.create_connection(('127.0.0.1',APIPORT),timeout=.2):return
  except OSError:time.sleep(.1)
 raise RuntimeError('QA API startup readiness exceeded30s')

import httpx
client=httpx.Client(trust_env=False,timeout=10)
def get(params=None,path='/api/index-tasks',tag=''):
 url=BASE+path
 response=client.get(url,params=params,headers={'X-Trace-Id':f'qa-{RUN[:8]}-{len(STATE["supervisor_exchanges"])+1}'})
 try:body=response.json()
 except Exception:body=response.text
 STATE['supervisor_exchanges'].append({'time_mst':datetime.datetime.now(ZoneInfo('America/Denver')).isoformat(),'tag':tag,'target':BASE,'method':'GET','url':str(response.url),'status':response.status_code,'headers':dict(response.headers),'body':body});save();return response,body

def migrate(revision):
 from alembic import command as ac
 from alembic.config import Config
 cfg=Config(str(BACKEND/'alembic.ini'));cfg.set_main_option('script_location',str(BACKEND/'alembic'));cfg.attributes['database_url']=DB
 ac.upgrade(cfg,revision)
 STATE.setdefault('migrations',[]).append({'revision':revision,'target':STATE['targets']['pg'],'explicit_database_url':True});save()

try:
 if not free(PGPORT) or not free(APIPORT):raise RuntimeError('Reserved QA loopback port in use; stop without touching owner')
 cid=command(['docker','run','--pull','never','-d','--rm','--name','qa-ingest-'+RUN[:12],'--label','com.grok.qa.run='+RUN,'--tmpfs','/var/lib/postgresql/data','-p',f'127.0.0.1:{PGPORT}:5432','-e','POSTGRES_USER=qa_ingest','-e','POSTGRES_DB=qa_ingest','-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16','timeout','--signal=TERM','900','docker-entrypoint.sh','postgres'])
 STATE['container_id']=cid;save()
 from sqlalchemy import create_engine,text,inspect
 engine=create_engine(DB,connect_args={'connect_timeout':1})
 deadline=time.monotonic()+40
 while True:
  try:
   with engine.connect() as conn:conn.execute(text('SELECT 1'))
   break
  except Exception:
   if time.monotonic()>deadline:raise
   time.sleep(.25)
 with engine.connect() as conn:tables=conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars().all()
 check('Fresh isolated PG public schema has no tables',not tables,tables)
 start_api('empty-schema');response,body=get({'page':1,'page_size':20},tag='empty schema real NOT_READY')
 check('Empty real schema produces503 NOT_READY not fake empty',response.status_code==503 and body.get('error')=='NOT_READY' and body.get('ok') is False and 'items' not in body,body)
 check('NOT_READY contains real GET trace',bool(response.headers.get('X-Trace-Id')) and bool(body.get('trace_id')))
 stop_api()
 migrate('20261109_0007');start_api('missing-0008');response,body=get({'page':1,'page_size':20},tag='actual missing migration0008')
 check('Real schema0007 missing0008 gives503 NOT_READY',response.status_code==503 and body.get('error')=='NOT_READY',body)
 stop_api();migrate('head')
 from app.repositories.metadata import PgMetadataStore
 from alembic.autogenerate import compare_metadata
 from alembic.migration import MigrationContext
 from app.db.base import Base
 with engine.connect() as conn:
  rev=conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one();diff=compare_metadata(MigrationContext.configure(conn,opts={'compare_type':True}),Base.metadata)
 check('Isolated database at real migration20261111_0008',rev=='20261111_0008',rev)
 check('Migrated schema matches actual ORM metadata',not diff,str(diff))
 store=PgMetadataStore(url=DB);store.ensure_schema();start_api('ready')
 response,body=get({'page':1,'page_size':20},tag='migrated empty task table')
 check('Migrated empty task table real200 zero counts',response.status_code==200 and body['total']==0 and body['counts']=={'succeeded':0,'failed':0} and body['items']==[])
 iid='11111111-2222-4333-8444-555555555555';gone='99999999-8888-4777-8666-555555555555'
 with engine.begin() as conn:
  cat=conn.execute(text("INSERT INTO categories(code) VALUES('qa_ingest_http') RETURNING id")).scalar_one()
  conn.execute(text("INSERT INTO category_names(category_id,lang,name,kind) VALUES(:cat,'zh',:name,'primary')"),{'cat':cat,'name':'当前类别甲'})
  conn.execute(text("INSERT INTO images(id,sha256,original_url,thumb_url,width,height,source_type,license,label_status,category_id) VALUES(CAST(:id AS uuid),:sha,'https://qa.invalid/o.jpg','',4,3,'user_upload','unknown','verified',:cat)"),{'id':iid,'sha':hashlib.sha256(b'qa-isolated-image').digest(),'cat':cat})
 records=[]
 for n in range(1,44):
  failed=n%3==0
  records.append({'filename':'<script>not executable</script>.jpg' if n==1 else '中文长文件名'+('测'*300) if n==2 else f'qa-http-{n}.jpg','status':'failed' if failed else 'succeeded','image_id':None if failed else gone if n==4 else iid,'new_image':None if failed else n%2==0,'error_code':'INDEX_BAD_IMAGE' if failed else None,'error_detail':'公开失败'+('长'*1100) if n==3 else 'cannot decode QA sample' if failed else None,'duration_ms':None if n==3 else 0 if n==1 else n,'trace_id':f'qa-original-index-{n}'})
 store.record_index_tasks(records)
 with engine.begin() as conn:
  conn.execute(text("UPDATE index_tasks SET created_at='2026-11-11T09:00:00Z'"));conn.execute(text("UPDATE index_tasks SET created_at='2026-11-11T10:00:00Z' WHERE id=5"));conn.execute(text("UPDATE index_tasks SET created_at='2026-11-11T08:00:00Z' WHERE id=1"))
 STATE['fixture']={'records':43,'counts':{'succeeded':29,'failed':14},'current_image_id':iid,'missing_projection_image_id':gone,'category_id':cat,'seed_method':'actual PgMetadataStore.record_index_tasks + isolated SQL current image/category/timestamps; not actual POST index outcomes','thumbnail_null':'images.thumb_url is NOT NULL in real schema; used legal empty text, not a null-wire proof. FE safety mapper can turn empty URL into no-preview.', 'created_at_null':'Not representable in migrated PG schema: NOT NULL. Existing fixture/unit null-wire coverage is separate, not real-PG proof.'};save()
 fields={'id','filename','status','image_id','new_image','error_code','error_detail','duration_ms','trace_id','created_at','image'}
 allrows=[]
 for page,size in [(1,20),(2,20),(3,20)]:
  response,body=get({'page':page,'page_size':size},tag=f'actual20 pagination page{page}')
  check(f'Page{page}200 echoes20 and correct total/counts',response.status_code==200 and body['page']==page and body['page_size']==20 and body['total']==43 and body['counts']=={'succeeded':29,'failed':14})
  check(f'Page{page}expected length',len(body['items'])==(20 if page<3 else 3))
  check(f'Page{page}all business fields returned',all(set(row)==fields for row in body['items']))
  check(f'Page{page}GET trace distinct from original index traces',bool(response.headers.get('X-Trace-Id')) and 'trace_id' not in body and all(row['trace_id']!=response.headers.get('X-Trace-Id') for row in body['items']))
  allrows.extend(body['items'])
 expected=[5]+list(range(43,5,-1))+[4,3,2,1]
 check('Actual PG ordering created_at DESC then id DESC across pages', [x['id'] for x in allrows]==expected,[x['id'] for x in allrows])
 check('No same-image record merge;43 unique numeric ids',len({x['id'] for x in allrows})==43 and all(isinstance(x['id'],int) for x in allrows) and sum(x['image_id']==iid for x in allrows)>1)
 byid={x['id']:x for x in allrows}
 check('Real PG/Wire duration0 and newImage false preserved',byid[1]['duration_ms']==0 and byid[1]['new_image'] is False and byid[1]['status']=='succeeded')
 check('Real PG/Wire failure null duration/newImage/image preserved',byid[3]['duration_ms'] is None and byid[3]['new_image'] is None and byid[3]['image_id'] is None and byid[3]['image'] is None and byid[3]['status']=='failed')
 check('Real PG newImage true distinct',byid[2]['new_image'] is True)
 check('Missing current image projection keeps historical success/image id',byid[4]['status']=='succeeded' and byid[4]['image_id']==gone and byid[4]['image'] is None)
 check('Current projection complete with schema-legal empty thumbnail',set(byid[1]['image'])=={'id','thumb_url','category_id','category','label_status'} and byid[1]['image']['thumb_url']=='' and byid[1]['image']['category']=='当前类别甲')
 check('Actual record writer clips filename255 and error detail1000',len(byid[2]['filename'])==255 and len(byid[3]['error_detail'])==1000)
 check('Record timestamps carry timezone (not UI MST claim)',all(x['created_at'].endswith('Z') or '+' in x['created_at'][10:] for x in allrows))
 for status,total in [('succeeded',29),('failed',14)]:
  response,body=get({'status':status,'page':1,'page_size':20},tag='valid status '+status)
  check(f'{status}filtered total/global counts real SQL',response.status_code==200 and body['status']==status and body['total']==total and body['counts']=={'succeeded':29,'failed':14} and all(x['status']==status for x in body['items']))
 response,body=get(tag='BE defaults; all status omitted')
 check('Omitted query status null; BE default50 (FE separately sends20)',response.status_code==200 and body['status'] is None and body['page_size']==50 and len(body['items'])==43)
 for params in [{'status':'all'},{'status':''},{'status':'running'},{'status':'null'},{'status':'SUCCEEDED'},{'page':0},{'page':1000001},{'page':'x'},{'page_size':0},{'page_size':101}]:
  response,body=get(params,tag='invalid parameter '+repr(params))
  check('Invalid query '+repr(params)+' =>422 VALIDATION_ERROR',response.status_code==422 and body.get('error')=='VALIDATION_ERROR' and body.get('trace_id'))
 for params in [{'page':1000000,'page_size':20},{'page':1,'page_size':1},{'page':1,'page_size':100}]:
  response,body=get(params,tag='legal query boundary '+repr(params))
  check('Legal boundary '+repr(params),response.status_code==200 and body['page']==params['page'] and body['page_size']==params['page_size'] and body['total']==43)
  if params['page']==1000000:check('Backend returns requested out-of-range page empty, does not clamp',body['items']==[])
 response,body=get(path='/api/index-tasks/1',tag='verify no invented detail endpoint')
 check('No task-detail route: GET collection/id is404',response.status_code==404)
 # FE entity issues real GETs to the same TCP server; no mocked fetch and no browser.
 env={**os.environ,'QA_INGEST_REAL_BASE':BASE};run=subprocess.run(['node','node_modules/vitest/vitest.mjs','run','--config','../.qa/ingest-tasks-real.vitest.config.mts','--reporter=json','--outputFile='+str(Q/'ingest-tasks-real-client-results.json')],cwd=ROOT/'frontend',env=env,capture_output=True,text=True,timeout=60)
 (Q/'ingest-tasks-real-client.log').write_text(run.stdout+'\n'+run.stderr,encoding='utf-8');STATE['frontend_real_client_exit']=run.returncode;save()
 if (Q/'ingest-tasks-real-client-results.json').exists():
  cr=json.loads((Q/'ingest-tasks-real-client-results.json').read_text(encoding='utf-8-sig'));STATE['frontend_real_client']={k:cr.get(k) for k in ['success','numTotalTests','numPassedTests','numFailedTests','numFailedTestSuites']}
 check('Actual FE client realHTTP/PG integration5/5',run.returncode==0 and STATE.get('frontend_real_client',{}).get('numPassedTests')==5,STATE.get('frontend_real_client'))
 with engine.begin() as conn:conn.execute(text("UPDATE category_names SET name=:name WHERE category_id=:cat AND lang='zh' AND kind='primary'"),{'name':'当前类别乙','cat':cat})
 response,body=get({'page':3,'page_size':20},tag='current projection category changed in isolated SQL')
 item=next(x for x in body['items'] if x['id']==1)
 check('Historical status stable while current category projection changes',item['status']=='succeeded' and item['image']['category']=='当前类别乙' and item['trace_id']=='qa-original-index-1')
 with engine.begin() as conn:conn.execute(text('DELETE FROM images WHERE id=CAST(:id AS uuid)'),{'id':iid})
 response,body=get({'page':3,'page_size':20},tag='isolated image removal, history retained')
 item=next(x for x in body['items'] if x['id']==1)
 check('Real PG LEFT JOIN deletion preserves43 history rows/status/image_id',body['total']==43 and item['status']=='succeeded' and item['image_id']==iid and item['image'] is None)
 with engine.begin() as conn:conn.execute(text('ALTER TABLE index_tasks RENAME TO qa_saved_index_tasks'))
 response,body=get({'page_size':20},tag='real SQL missing relation after cached schema ->502')
 check('Actual SQL failure becomes502 LIST_POSTGRES_FAILED not fake empty',response.status_code==502 and body.get('error')=='LIST_POSTGRES_FAILED' and body.get('trace_id') and 'items' not in body,body)
 with engine.begin() as conn:conn.execute(text('ALTER TABLE qa_saved_index_tasks RENAME TO index_tasks'))
 response,body=get({'page':1,'page_size':20},tag='restored isolated relation reads recover')
 check('Restored real relation GET recovers43 rows',response.status_code==200 and body['total']==43)
 with engine.connect() as conn:remaining=conn.execute(text('SELECT count(*) FROM index_tasks')).scalar_one()
 check('GET-only HTTP run did not create/delete history rows',remaining==43,remaining)
 STATE['completed']=True
except BaseException as exc:
 STATE['exception']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()};print('RUN_EXCEPTION',traceback.format_exc(),flush=True)
finally:
 try:stop_api()
 except BaseException as exc:STATE['cleanup'].append({'api_cleanup_error':repr(exc)})
 client.close()
 if store:store.close()
 if engine:engine.dispose()
 if cid:
  try:
   raw=command(['docker','inspect',cid]);info=json.loads(raw)[0]
   matched=info['Id']==cid and info['Config']['Labels'].get('com.grok.qa.run')==RUN and info['Config']['Image']=='postgres:16'
   cleanup={'kind':'postgres-container','id':cid,'matched':matched,'labels':info['Config']['Labels'],'port_bindings':info['HostConfig']['PortBindings'],'ttl_seconds':900}
   if matched:
    cleanup['stop_output']=command(['docker','stop','--time','5',cid])
    probe=subprocess.run(['docker','inspect',cid],capture_output=True,text=True,timeout=10);cleanup['absent_after']=probe.returncode!=0 and 'no such' in probe.stderr.lower();cleanup['inspect_after_stderr']=probe.stderr;cleanup['pg_port_closed']=free(PGPORT)
   else:cleanup['action']='NOT stopped; identity mismatch'
   STATE['cleanup'].append(cleanup)
  except BaseException as exc:STATE['cleanup'].append({'container_cleanup_error':repr(exc),'id':cid})
 STATE['assertion_counts']={'pass':sum(x['pass'] for x in STATE['assertions']),'fail':sum(not x['pass'] for x in STATE['assertions'])};STATE['finished_mst']=datetime.datetime.now(ZoneInfo('America/Denver')).isoformat();save();print('FINAL',json.dumps({'completed':STATE.get('completed',False),'counts':STATE['assertion_counts'],'exception':STATE['exception'],'cleanup':STATE['cleanup']},ensure_ascii=False),flush=True)
if STATE.get('exception') or STATE['assertion_counts']['fail']:sys.exit(1)
