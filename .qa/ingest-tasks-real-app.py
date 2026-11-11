"""QA-only composition: unchanged create_app/routes/services/SQL; override external bootstrap lifespan."""
import argparse,sys,pathlib,json,datetime
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
a=argparse.ArgumentParser();a.add_argument('--run-id',required=True);a.add_argument('--pg-port',type=int,required=True);a.add_argument('--port',type=int,required=True);a.add_argument('--phase',required=True);args=a.parse_args()
import httpx,uvicorn
from app.bootstrap.application import create_app
from app.bootstrap.catalog import build_catalog
from app.bootstrap.state import ApplicationState
from app.core.config.settings import Settings
config={'POSTGRES_CONFIG':{'host':'127.0.0.1','port':args.pg_port,'user':'qa_ingest','database':'qa_ingest','password':'','connect_timeout':2},'TOTAL_CONFIG':{'retrieval_enabled':False}}
@asynccontextmanager
async def isolated_lifespan(app):
 client=httpx.AsyncClient(trust_env=False)
 runtime=ApplicationState(config=config,config_info={'source':'QA isolated explicit config; no Nacos'},http_client=client,settings=Settings())
 try:
  try:runtime.catalog=build_catalog(config)
  except Exception as exc:runtime.catalog_error=str(exc)
  runtime.retrieval=None;runtime.retrieval_error='QA: intentionally disabled; no OSS/Qdrant/encoder'
  app.state.runtime=runtime
  (ROOT/'.qa'/f'ingest-tasks-real-{args.phase}-startup.json').write_text(json.dumps({'run_id':args.run_id,'pid':__import__('os').getpid(),'catalog_ready':runtime.catalog is not None,'catalog_error':runtime.catalog_error,'target':f'127.0.0.1:{args.pg_port}/qa_ingest','routes':[(r.path,sorted(r.methods or [])) for r in app.routes if hasattr(r,'methods')]},ensure_ascii=False,indent=2),encoding='utf-8')
  yield
 finally:await runtime.close()
class Capture:
 def __init__(self,app):self.app=app
 async def __call__(self,scope,receive,send):
  if scope['type']!='http':return await self.app(scope,receive,send)
  chunks=[];status=None;headers=[]
  async def wrapped(message):
   nonlocal status,headers
   if message['type']=='http.response.start':status=message['status'];headers=[(k.decode(),v.decode()) for k,v in message['headers']]
   elif message['type']=='http.response.body':chunks.append(message.get('body',b''))
   await send(message)
  try:await self.app(scope,receive,wrapped)
  finally:
   raw=b''.join(chunks).decode('utf-8','replace')
   try:body=json.loads(raw)
   except Exception:body=raw
   entry={'time_mst':datetime.datetime.now(ZoneInfo('America/Denver')).isoformat(),'run_id':args.run_id,'phase':args.phase,'target':f'http://127.0.0.1:{args.port}','method':scope['method'],'path':scope['path'],'query':scope.get('query_string',b'').decode(),'status':status,'response_headers':headers,'response_body':body}
   with (ROOT/'.qa'/'ingest-tasks-real-http-exchanges.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
app=create_app();app.router.lifespan_context=isolated_lifespan;app.add_middleware(Capture)
uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='info',access_log=True,timeout_graceful_shutdown=3)
