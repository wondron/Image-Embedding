"""QA ONLY: actual loopback FastAPI HTTP + isolated PostgreSQL and Qdrant server.
Deterministic encoder and in-memory object storage are explicit substitutes, NOT production.
Never reads app connection YAML or Nacos; refuses non-QA connection targets.
"""
from __future__ import annotations
import io,json,os,socket,sys,threading,time,traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.chdir(ROOT/'backend')
os.environ['NO_PROXY']='127.0.0.1,localhost'
os.environ['no_proxy']='127.0.0.1,localhost' # QA process only; never proxy loopback
import httpx,numpy as np,cv2,uvicorn
from fastapi import FastAPI
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text
from app.api.v1 import scenes,retrieval,categories,enums
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.bootstrap.state import ApplicationState
from app.bootstrap.catalog import CatalogServices
from app.clients.image_storage import MockImageStorage
from app.core.config.settings import ImageStorageSettings
from app.clients.qdrant import QdrantStore,QdrantConnection
from app.repositories.metadata import PgMetadataStore
from app.repositories.vectors import QdrantVectorStore
from app.services.retrieval.manage_scenes import ManageScenesUseCase
from app.services.retrieval.manage_categories import ManageCategoriesUseCase
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.index_image import IndexImageUseCase
from app.services.retrieval.search_image import SearchImageUseCase
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
DB='postgresql+psycopg://qa_scene@127.0.0.1:52316/qa_scene_http_0957'
QURL='http://127.0.0.1:56339'
COL='qa_scene_http_0957'
OUT=ROOT/'.qa/scene-current-real-http-results.json'
assert DB.endswith('/qa_scene_http_0957') and ':52316/' in DB and COL.startswith('qa_scene_')
checks=[];requests=[];server=None;thread=None;vectors=None;store=None;engine=None
class EncoderStub:
    def embed_bytes(self,blob): return [1.0,0.0,0.0,0.0]
def check(name,ok,detail=None):
    checks.append({'name':name,'pass':bool(ok),'detail':detail})
    if not ok: raise AssertionError(name+': '+str(detail))
def sql(s,p=None):
    with engine.begin() as c:return c.execute(text(s),p or {})
def png(color):
    ok,b=cv2.imencode('.png',np.full((8,8,3),color,dtype=np.uint8));assert ok
    return b.tobytes()
def run():
    global server,thread,vectors,store,engine
    cfg=Config(str(ROOT/'backend/alembic.ini'));cfg.set_main_option('script_location',str(ROOT/'backend/alembic'));cfg.attributes['database_url']=DB
    command.upgrade(cfg,'head')
    engine=create_engine(DB)
    check('isolated database identity',sql('SELECT current_database()').scalar_one()=='qa_scene_http_0957')
    store=PgMetadataStore(url=DB);store.ensure_schema()
    qstore=QdrantStore(QdrantConnection(url=QURL,prefer_grpc=False),collection=COL,vector_dim=4)
    vectors=QdrantVectorStore(qstore);vectors.ensure_collection()
    mid=sql("INSERT INTO embedding_models (name,version,dim,distance,input_size,weights_uri,collection,status) VALUES ('qa_scene','0957',4,'cosine',8,'stub://qa_scene',:c,'current') RETURNING id",{'c':COL}).scalar_one()
    model=ActiveModel(id=mid,name='qa_scene',version='0957',collection=COL,dim=4,distance='cosine')
    embed=EncoderStub();storage=MockImageStorage(ImageStorageSettings(public_base_url='https://qa-scene.invalid',bucket='qa_scene',mock_mirror_dir=''))
    stack=SimpleNamespace(index_uc=IndexImageUseCase(embed,vectors,store,model=model,storage=storage),search_uc=SearchImageUseCase(embed,vectors,store,model=model),list_uc=ListImagesUseCase(store),scene_sync_uc=SyncSceneMembershipsUseCase(store,vectors,model=model),model=model)
    rt=ApplicationState(config={},config_info={},http_client=httpx.AsyncClient(),settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1048576,http_timeout=5.0)),retrieval=stack,catalog=CatalogServices(store,ManageCategoriesUseCase(store),ManageScenesUseCase(store)))
    app=FastAPI(title='QA scene isolated HTTP (encoder/OSS STUB)');register_exception_handlers(app);app.add_middleware(TraceIdMiddleware)
    for router in (scenes.router,retrieval.router,categories.router,enums.router):app.include_router(router)
    app.state.runtime=rt
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    deadline=time.monotonic()+15
    while not server.started:
        if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('QA HTTP did not start')
        threading.Event().wait(.02)
    base=f'http://127.0.0.1:{port}'
    provenance={'pid':os.getpid(),'http_url':base,'database':'127.0.0.1:52316/qa_scene_http_0957','qdrant_url':QURL,'collection':COL,'code_root':str(ROOT),'encoder':'explicit deterministic 4D stub','storage':'MockImageStorage in memory; no OSS/network/mirror writes'}
    with httpx.Client(base_url=base,timeout=15,trust_env=False) as client:
        def req(method,url,status=200,**kwargs):
            r=client.request(method,url,**kwargs);body=r.json()
            requests.append({'method':method,'url':url,'status':r.status_code,'body':body,'trace_header':r.headers.get('X-Trace-Id')})
            check(method+' '+url+' status '+str(status),r.status_code==status,body)
            return body
        def search(code=None,status=200):
            data={'score_threshold':'0','top_k':'10'}
            if code is not None:data['scene']=code
            return req('POST','/api/search',status,files={'file':('query.png',png(16),'image/png')},data=data)
        def index(color,**data):
            return req('POST','/api/index',files={'file':('qa.png',png(color),'image/png')},data=data)['indexed'][0]
        def payload(iid):
            r=httpx.get(QURL+f'/collections/{COL}/points/{iid}',timeout=10,trust_env=False)
            check('actual Qdrant HTTP get point',r.status_code==200,r.text)
            return r.json()['result']['payload']
        listed=req('GET','/api/scenes');check('default_scene=general',listed['default_scene']=='general');check('six initial DB scenes',len(listed['scenes'])==6)
        general=next(s['id'] for s in listed['scenes'] if s['code']=='general')
        req('POST','/api/scenes',422,json={'code':'','name':'bad'})
        req('POST','/api/scenes',422,json={'code':'device','name':'reserved alias'})
        created=req('POST','/api/scenes',201,json={'code':'qa_dynamic','name':'QA dynamic','outputs_heating':True})['scene'];sid=created['id']
        req('POST','/api/scenes',409,json={'code':'qa_dynamic','name':'duplicate'})
        req('PATCH',f'/api/scenes/{sid}',422,json={'name':'  '})
        req('PATCH',f'/api/scenes/{sid}',422,json={'name':None})
        req('PATCH',f'/api/scenes/{sid}',422,json={})
        req('PATCH',f'/api/scenes/{sid}',422,json={'code':'renamed'})
        req('PATCH',f'/api/scenes/{general}',403,json={'is_active':False})
        req('PATCH',f'/api/scenes/{general}',403,json={'include_all':False})
        req('DELETE',f'/api/scenes/{general}',403)
        edited=req('PATCH',f'/api/scenes/{sid}',json={'name':'QA edited','description':'  '})['scene'];check('edit echo & clear description',edited['name']=='QA edited' and edited['description'] is None)
        hg=sql("INSERT INTO heating_groups(code,name) VALUES ('qa_grill','QA Grill') RETURNING id").scalar_one()
        root=req('POST','/api/categories',201,json={'code':'dish','names':[{'lang':'zh','name':'QA dish','kind':'primary'}],'heating_group_id':hg,'cuisines':['american']})['category']['id']
        child=req('POST','/api/categories',201,json={'code':'dish.qa','parent_id':root,'names':[{'lang':'zh','name':'QA child','kind':'primary'}]})['category']['id']
        label_scene=req('POST',f'/api/scenes/{sid}/labels',201,json={'code':'qa_label','name':'QA scene label'})['scene'];lid=label_scene['labels'][0]['id']
        rule_scene=req('POST',f'/api/scenes/{sid}/rules',201,json={'category_id':root,'label_id':lid})['scene'];rid=rule_scene['rules'][0]['id']
        req('POST',f'/api/scenes/{sid}/rules',409,json={'category_id':root})
        req('POST',f'/api/scenes/{sid}/rules',422,json={'category_id':root,'cuisine':'american'})
        req('DELETE',f'/api/scenes/{sid}/labels/{lid}',409)
        first=index(32,category='dish.qa',scenes='["general"]',food_state='cooked');a=first['id']
        second=index(64,scenes='["qa_dynamic"]');b=second['id']
        check('rules not index hints decide membership',first['scenes']==['general','qa_dynamic'] and second['scenes']==['general'],[first,second])
        check('include_all general no materialized rows',sql('SELECT count(*) FROM image_scenes WHERE scene_id=:s',{'s':general}).scalar_one()==0)
        check('rule row stored',sql("SELECT origin FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s",{'i':a,'s':sid}).scalar_one()=='rule')
        check('index payload actual server matches effective scenes',payload(a)['scenes']==first['scenes'])
        gl=req('GET','/api/images');check('default list returns both',gl['scene']=='general' and gl['total']==2)
        al=req('GET','/api/images?scene=normal');check('list legacy normal alias',al['scene']=='general' and al['total']==2)
        dl=req('GET','/api/images?scene=qa_dynamic');check('dynamic list rule filter',dl['total']==1 and dl['items'][0]['id']==a)
        check('list heating object and food fields',dl['items'][0]['heating_group']['code']=='qa_grill' and dl['items'][0]['food_state']=='cooked')
        check('general list omits heating fields',all('heating_group' not in x and 'food_state' not in x for x in gl['items']))
        sr=search('qa_dynamic');check('actual Qdrant search with PG join scene filter',sr['source']=='self' and [x['id'] for x in sr['items']]==[a])
        check('HTTP scene_label string and heating contract',sr['outputs_heating'] is True and sr['items'][0]['scene_label']=='QA scene label' and sr['items'][0]['heating_group']['code']=='qa_grill' and sr['items'][0]['food_state']=='cooked')
        sg=search();check('default search general omits heating',sg['scene']=='general' and sg['outputs_heating'] is False and len(sg['items'])==2 and all('heating_group' not in x and 'food_state' not in x for x in sg['items']))
        check('search normal alias',search('normal')['scene']=='general');check('search device alias',search('device')['scene']=='oven');check('blank scene defaults general',search('   ')['scene']=='general')
        req('GET','/api/images?scene=qa_unknown',422)
        check('unknown search code',search('qa_unknown',422)['error']=='SEARCH_UNKNOWN_SCENE')
        unknown=req('POST','/api/index',422,files={'file':('qa.png',png(96),'image/png')},data={'scene':'qa_unknown'});check('unknown index code',unknown['error']=='INDEX_UNKNOWN_SCENE')
        # Manual membership has NO public API: exercise existing repository, then public sync.
        store.set_manual_scene_membership(a,'qa_dynamic',is_member=False);store.set_manual_scene_membership(b,'qa_dynamic',is_member=True)
        sync=req('POST','/api/scenes/sync',json={'limit':10});check('sync writes actual Qdrant server',sync['processed']==2 and sync['payload_pushed']==2 and sync['payload_missing']==[])
        check('manual overrides reflected in server payload',payload(a)['scenes']==['general'] and payload(b)['scenes']==['general','qa_dynamic'])
        check('manual rows retain priority',dict(sql('SELECT image_id::text,is_member FROM image_scenes WHERE scene_id=:s AND origin=\'manual\'',{'s':sid}).fetchall())=={a:False,b:True})
        check('manual overrides reflected in HTTP list/search',[x['id'] for x in req('GET','/api/images?scene=qa_dynamic')['items']]==[b] and [x['id'] for x in search('qa_dynamic')['items']]==[b])
        req('DELETE',f'/api/scenes/{sid}/rules/{rid}');req('POST','/api/scenes/sync',json={'limit':10})
        check('manual add survives rule removal',[x['id'] for x in search('qa_dynamic')['items']]==[b])
        req('PATCH',f'/api/scenes/{sid}',json={'is_active':False});disabled=req('GET','/api/scenes');check('list configurations includes inactive flag',next(x for x in disabled['scenes'] if x['id']==sid)['is_active'] is False)
        req('GET','/api/images?scene=qa_dynamic',422);search('qa_dynamic',422)
        req('POST','/api/index',422,files={'file':('qa.png',png(96),'image/png')},data={'scene':'qa_dynamic'})
        req('POST','/api/scenes/sync',json={'limit':10});check('inactive removed from actual payloads',all('qa_dynamic' not in payload(i)['scenes'] for i in [a,b]))
        req('PATCH',f'/api/scenes/{sid}',json={'is_active':True,'include_all':True});req('POST','/api/scenes/sync',json={'limit':10})
        check('include_all dominates existing manual exclusions',req('GET','/api/images?scene=qa_dynamic')['total']==2 and len(search('qa_dynamic')['items'])==2)
        check('include_all included in actual Qdrant payload',all('qa_dynamic' in payload(i)['scenes'] for i in [a,b]))
        req('PATCH',f'/api/scenes/{sid}/labels/{lid}',json={'name':'QA renamed label'})
        req('DELETE',f'/api/scenes/{sid}/labels/{lid}');req('DELETE',f'/api/scenes/{sid}')
        req('POST','/api/scenes/sync',json={'limit':10});check('scene deletion sync removes server payload codes',all('qa_dynamic' not in payload(i)['scenes'] for i in [a,b]))
        check('no public manual API fabricated',not any('manual' in p for p in app.openapi()['paths']))
        check('OSS substitute never mirrors disk',storage._mirror is None and storage.ping()['driver']=='mock')
    return provenance
result={'kind':'actual FastAPI loopback HTTP + actual PostgreSQL + actual Qdrant HTTP; encoder and OSS explicit substitutes','checks':checks,'requests':requests}
try:result['target']=run();result['exit_code']=0
except BaseException as e:result['exit_code']=1;result['exception']=traceback.format_exc();print(result['exception'])
finally:
    if server is not None:server.should_exit=True
    if thread is not None:thread.join(10)
    if vectors is not None:vectors.close()
    if store is not None:store.close()
    if engine is not None:engine.dispose()
    result['http_server_stopped']=thread is None or not thread.is_alive()
    result['assertions_total']=len(checks);result['assertions_passed']=sum(x['pass'] for x in checks);result['http_requests']=len(requests)
    result['reported_at']=datetime.now(ZoneInfo('America/Denver')).isoformat()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','requests','exception')},ensure_ascii=False))
sys.exit(result['exit_code'])
