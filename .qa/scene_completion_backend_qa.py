"""Independent QA only: public manual routes, real PG and actual Qdrant HTTP.
All writes are to self-created tmpfs QA containers; encoder and OSS are explicit stubs.
"""
import os,sys,pathlib,json,socket,threading,time,traceback,hashlib,uuid,collections
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'));os.chdir(ROOT/'backend')
os.environ['NO_PROXY']=os.environ['no_proxy']='127.0.0.1,localhost'
import httpx,numpy as np,cv2,uvicorn
from fastapi import FastAPI
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,text,event
from sqlalchemy.exc import SQLAlchemyError
from app.api.router import router
from app.core.exceptions import register_exception_handlers
from app.core.middleware import TraceIdMiddleware
from app.bootstrap.state import ApplicationState
from app.bootstrap.catalog import CatalogServices
from app.clients.image_storage import MockImageStorage
from app.core.config.settings import ImageStorageSettings
from app.clients.qdrant import QdrantStore,QdrantConnection
from app.repositories.metadata import PgMetadataStore
from app.repositories.vectors import QdrantVectorStore
from app.repositories.catalog_lock import CATALOG_LOCK_KEY
from app.services.retrieval.manage_scenes import ManageScenesUseCase
from app.services.retrieval.manage_categories import ManageCategoriesUseCase
from app.services.retrieval.active_model import ActiveModel
from app.services.retrieval.index_image import IndexImageUseCase
from app.services.retrieval.search_image import SearchImageUseCase
from app.services.retrieval.list_images import ListImagesUseCase
from app.services.retrieval.sync_scenes import SyncSceneMembershipsUseCase
DB='postgresql+psycopg://qa_scene@127.0.0.1:64664/qa_scene_completion_b4';QURL='http://127.0.0.1:64667';COL='qa_scene_completion_b4'
checks=[];requests=[];audit=[];server=None;th=None;store=None;vectors=None;engine=None;rt=None
result={'target':{'db':'127.0.0.1:64664/qa_scene_completion_b4','qdrant':QURL,'collection':COL,'code_root':str(ROOT),'encoder':'deterministic4D stub','OSS':'memory-only MockImageStorage'},'checks':checks,'requests':requests,'evidence':'actual loopback HTTP; registered current app.api.router; real PG + Qdrant HTTP; no TestClient'}
def ck(name,ok,detail=None):
    checks.append({'name':name,'pass':bool(ok),'detail':detail})
    if not ok:raise AssertionError(name+': '+str(detail))
def sql(s,p=None):
    with engine.begin() as c:return c.execute(text(s),p or {})
def png(n=16):return cv2.imencode('.png',np.full((8,8,3),n,dtype=np.uint8))[1].tobytes()
def fingerprint():
    return {t:sql(f'SELECT coalesce(json_agg(x ORDER BY x::text)::text,\'[]\') FROM (SELECT row_to_json(t) AS x FROM {t} t) z').scalar_one() for t in ['images','image_scenes','image_embeddings','scene_rules']}
def run():
    global server,th,store,vectors,engine,rt
    cfg=Config(str(ROOT/'backend/alembic.ini'));cfg.set_main_option('script_location',str(ROOT/'backend/alembic'));cfg.attributes['database_url']=DB;command.upgrade(cfg,'head')
    engine=create_engine(DB);ck('QA database identity',sql('SELECT current_database()').scalar_one()=='qa_scene_completion_b4')
    store=PgMetadataStore(url=DB);store.ensure_schema()
    def audit_sql(conn,cursor,statement,parameters,context,executemany):audit.append(statement)
    event.listen(store._get_engine(),'before_cursor_execute',audit_sql)
    vectors=QdrantVectorStore(QdrantStore(QdrantConnection(url=QURL,prefer_grpc=False),collection=COL,vector_dim=4));vectors.ensure_collection()
    mid=sql("INSERT INTO embedding_models(name,version,dim,distance,input_size,weights_uri,collection,status) VALUES ('qa','completion',4,'cosine',8,'stub://qa',:c,'current') RETURNING id",{'c':COL}).scalar_one()
    model=ActiveModel(id=mid,name='qa',version='completion',collection=COL,dim=4,distance='cosine')
    embed=SimpleNamespace(embed_bytes=lambda b:[1.,0.,0.,0.]);storage=MockImageStorage(ImageStorageSettings(public_base_url='https://qa-scene.invalid',bucket='qa_completion',mock_mirror_dir=''))
    stack=SimpleNamespace(index_uc=IndexImageUseCase(embed,vectors,store,model=model,storage=storage),search_uc=SearchImageUseCase(embed,vectors,store,model=model),list_uc=ListImagesUseCase(store),scene_sync_uc=SyncSceneMembershipsUseCase(store,vectors,model=model),model=model)
    rt=ApplicationState(config={},config_info={},http_client=httpx.AsyncClient(),settings=SimpleNamespace(predict=SimpleNamespace(max_image_bytes=1048576,http_timeout=5)),retrieval=stack,catalog=CatalogServices(store,ManageCategoriesUseCase(store),ManageScenesUseCase(store)))
    app=FastAPI();register_exception_handlers(app);app.add_middleware(TraceIdMiddleware);app.include_router(router);app.state.runtime=rt
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];base=f'http://127.0.0.1:{port}'
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False));th=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);th.start();deadline=time.monotonic()+15
    while not server.started:
        if not th.is_alive() or time.monotonic()>deadline:raise RuntimeError('HTTP server start failed')
        threading.Event().wait(.02)
    result['target'].update(http=base,pid=os.getpid())
    with httpx.Client(base_url=base,timeout=15,trust_env=False) as c:
        def req(m,p,status=200,**kw):
            r=c.request(m,p,**kw);b=r.json();requests.append({'method':m,'path':p,'status':r.status_code,'body':b,'trace':r.headers.get('X-Trace-Id')});ck(f'{m} {p} HTTP{status}',r.status_code==status,b)
            if status<400:ck('success trace header/manual no body trace',bool(r.headers.get('X-Trace-Id')) and ('scene-memberships' not in p or 'trace_id' not in b))
            else:ck('error envelope fields',b.get('ok') is False and isinstance(b.get('error'),str) and bool(b.get('trace_id')))
            return b
        def scene(code,**kw):return req('POST','/api/scenes',201,json={'code':code,'name':code,**kw})['scene']['id']
        def snap(i):return req('GET',f'/api/images/{i}/scene-memberships')
        def put(i,s,value,status=200):return req('PUT',f'/api/images/{i}/scene-memberships/{s}',status,json={'is_member':value})
        def delete(i,s,status=200):return req('DELETE',f'/api/images/{i}/scene-memberships/{s}',status)
        def row(snapshot,s):return next(x for x in snapshot['memberships'] if x['scene_id']==s)
        def ver(i):return sql('SELECT payload_version FROM images WHERE id=CAST(:i AS uuid)',{'i':i}).scalar_one()
        def point(i):
            q=httpx.get(QURL+f'/collections/{COL}/points/{i}',trust_env=False);ck('raw actual Qdrant HTTP point200',q.status_code==200);return q.json()['result']['payload']
        def sync(**body):return req('POST','/api/scenes/sync',json={'limit':10,**body})
        def search(code):return req('POST','/api/search',files={'file':('qa.png',png(),'image/png')},data={'scene':code,'score_threshold':'0'})
        sc=req('GET','/api/scenes');general=next(s['id'] for s in sc['scenes'] if s['code']=='general')
        active=scene('qa_rule',outputs_heating=True);other=scene('qa_plain');inactive=scene('qa_inactive',is_active=False);allid=scene('qa_all',include_all=True);alloff=scene('qa_all_off',include_all=True,is_active=False)
        cat=req('POST','/api/categories',201,json={'code':'dish','names':[{'lang':'zh','name':'QA dish','kind':'primary'}]})['category']['id']
        lab=req('POST',f'/api/scenes/{active}/labels',201,json={'code':'qa_label','name':'QA Label'})['scene']['labels'][0]['id']
        indexed=req('POST','/api/index',files={'file':('qa.png',png(32),'image/png')},data={'category':'dish','food_state':'cooked'})['indexed'][0];i=indexed['id'];path=f'/api/images/{i}/scene-memberships'
        # Historical rows intentionally seeded only into the fresh QA database.
        sql("INSERT INTO image_scenes(image_id,scene_id,origin,is_member) VALUES(CAST(:i AS uuid),:s,'manual',true),(CAST(:i AS uuid),:a,'manual',false)",{'i':i,'s':inactive,'a':allid})
        s=snap(i);ck('GET full directory includes inactive include_all no-row ordered',len(s['memberships'])==11 and [m['scene_id'] for m in s['memberships']]==sorted(m['scene_id'] for m in s['memberships']))
        required={'scene_id','scene_code','is_active','include_all','is_member','origin','manual_override'};ck('all snapshot keys present including nullables',all(set(m)==required for m in s['memberships']))
        ck('no row nullable origin/override',row(s,other)['origin'] is None and row(s,other)['manual_override'] is None and row(s,other)['is_member'] is False)
        ck('inactive historical manual remains visible not effective',row(s,inactive)['origin']=='manual' and row(s,inactive)['manual_override'] is True and row(s,inactive)['is_member'] is False)
        ck('include_all ignores legacy manual & implicit',row(s,allid)['origin']=='implicit' and row(s,allid)['manual_override'] is None and row(s,allid)['is_member'] is True)
        ck('inactive include_all implicit not member',row(s,alloff)['origin']=='implicit' and row(s,alloff)['manual_override'] is None and row(s,alloff)['is_member'] is False)
        ck('GET lacks sync_required', 'sync_required' not in s)
        rule=req('POST',f'/api/scenes/{active}/rules',201,json={'category_id':cat,'label_id':lab})['scene']['rules'][0]['id']
        before=fingerprint();audit.clear();old=snap(i);readsql=list(audit);ck('GET not recompute new rule',row(old,active)['origin'] is None and 'qa_rule' not in old['scenes']);ck('GET exact data unchanged',fingerprint()==before)
        ck('GET read-only SQL single snapshot query',not any(re.match(r'\s*(INSERT|UPDATE|DELETE)',x,re.I) for x in readsql) and sum('FROM images i LEFT JOIN scenes' in x for x in readsql)==1,readsql)
        # DELETE with no manual recomputes and must not delete a rule row.
        z=delete(i,active);ck('DELETE no override computes current rule & required true',row(z,active)['origin']=='rule' and z['sync_required'] is True);v=ver(i)
        z=put(i,active,True);ck('source rule->manual same member no bump',row(z,active)['origin']=='manual' and z['payload_version']==v and z['sync_required'] is True)
        stamp=sql('SELECT updated_at FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s',{'i':i,'s':active}).scalar_one()
        z=put(i,active,True);ck('repeat PUT unique row no bump no timestamp change',z['payload_version']==v and sql('SELECT count(*) FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s',{'i':i,'s':active}).scalar_one()==1 and sql('SELECT updated_at FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s',{'i':i,'s':active}).scalar_one()==stamp)
        z=delete(i,active);ck('clear manual returns rule same set no bump',row(z,active)['origin']=='rule' and z['payload_version']==v)
        z=delete(i,active);ck('DELETE no manual retains rule idempotent',row(z,active)['origin']=='rule' and z['payload_version']==v)
        z=put(i,active,False);ck('exclude changes effective set once',z['payload_version']==v+1 and row(z,active)['manual_override'] is False and 'qa_rule' not in z['scenes'])
        z=put(i,active,False);ck('repeat exclusion no extra bump',z['payload_version']==v+1)
        z=put(i,active,True);ck('reverse inclusion bumps once',z['payload_version']==v+2)
        z=delete(i,active);ck('clear returns rule without payload change',z['payload_version']==v+2 and row(z,active)['origin']=='rule')
        # Strict paths/body and error mappings.
        for body in [{},{'is_member':None},{'is_member':0},{'is_member':1},{'is_member':'true'},{'is_member':[]},{'is_member':True,'extra':1}]:req('PUT',path+f'/{other}',422,json=body)
        for suffix in ['0','-1','2147483648','x','1.5']:req('PUT',path+'/'+suffix,422,json={'is_member':True})
        req('GET','/api/images/not-a-uuid/scene-memberships',422);req('GET',f'/api/images/{uuid.uuid4()}/scene-memberships',422)
        put(i,2147483647,True,404);delete(i,2147483647,404);put(i,inactive,True,422)
        for sid in [general,allid,alloff]:put(i,sid,True,422);delete(i,sid,422)
        v=ver(i);z=delete(i,inactive);ck('inactive DELETE clears old override no bump',row(z,inactive)['origin'] is None and z['payload_version']==v)
        # PG-only operation: retrieval unavailable; catalog remains.
        rt.retrieval=None;v=ver(i);z=put(i,other,True);ck('PG-only save works without retrieval',row(z,other)['manual_override'] is True and z['sync_required'] is True and z['payload_version']==v+1)
        req('POST','/api/scenes/sync',503,json={});snap(i);rt.retrieval=stack
        ck('manual does not update ledger or vector payload','qa_plain' not in point(i)['scenes'] and sql('SELECT synced_version FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':i}).scalar_one()<ver(i))
        # Genuine database fault after manual mutation: image UPDATE trigger forces rollback.
        sql("CREATE FUNCTION qa_reject_version() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'QA rollback failure'; END $$")
        sql('CREATE TRIGGER qa_fail_version BEFORE UPDATE OF payload_version ON images FOR EACH ROW EXECUTE FUNCTION qa_reject_version()')
        before=fingerprint();fail=put(i,other,False,502);ck('DB fault mapped INDEX_POSTGRES_FAILED',fail['error']=='INDEX_POSTGRES_FAILED');ck('failed manual transaction rollback all tables',fingerprint()==before)
        sql('DROP TRIGGER qa_fail_version ON images');sql('DROP FUNCTION qa_reject_version()')
        # SQL fault injected at query execution only; still actual public HTTP, no fake response.
        def dbfail(conn,cursor,statement,parameters,context,executemany):
            if 'FROM images i LEFT JOIN scenes' in statement:raise SQLAlchemyError('QA injected database failure')
        event.listen(store._get_engine(),'before_cursor_execute',dbfail)
        try:ck('GET database failure502 mapped',req('GET',path,502)['error']=='INDEX_POSTGRES_FAILED')
        finally:event.remove(store._get_engine(),'before_cursor_execute',dbfail)
        catalog=rt.catalog;rt.catalog=None;req('GET',path,503);put(i,other,False,503);delete(i,other,503);rt.catalog=catalog
        original=catalog.scenes_uc.memberships;catalog.scenes_uc.memberships=lambda _:(_ for _ in ()).throw(RuntimeError('QA_PRIVATE_UNEXPECTED'))
        try:
            b=req('GET',path,500);ck('unexpected500 redacted',b['error']=='INTERNAL_ERROR' and 'QA_PRIVATE_UNEXPECTED' not in json.dumps(b))
        finally:catalog.scenes_uc.memberships=original
        # Lock order and lock-inside scene revalidation through a real waiting HTTP PUT.
        held=engine.connect();tx=held.begin();held.execute(text('SELECT pg_advisory_xact_lock(:k)'),{'k':CATALOG_LOCK_KEY});done=threading.Event();out=[]
        def waiting():
            try:out.append(httpx.put(base+path+f'/{other}',json={'is_member':False},timeout=10,trust_env=False))
            finally:done.set()
        audit.clear();t=threading.Thread(target=waiting);t.start();ck('catalog lock blocks HTTP before image mutation',not done.wait(.35))
        held.execute(text('UPDATE scenes SET is_active=false WHERE id=:s'),{'s':other});tx.commit();held.close();ck('waiting request completes after unlock',done.wait(10));t.join();ck('scene active revalidated within lock',out[0].status_code==422,out[0].text)
        statements=list(audit);catidx=next(j for j,x in enumerate(statements) if 'pg_advisory_xact_lock_shared' in x);imgidx=next(j for j,x in enumerate(statements) if 'FOR UPDATE' in x and 'FROM images' in x);sceneidx=next(j for j,x in enumerate(statements) if 'FROM scenes WHERE id =' in x);ck('observed catalog->image->scene SQL lock order',catidx<imgidx<sceneidx)
        req('PATCH',f'/api/scenes/{other}',json={'is_active':True})
        # Same-image row lock serializes requests; second intent launched only after first commit.
        hold=engine.connect();tx=hold.begin();hold.execute(text('SELECT id FROM images WHERE id=CAST(:i AS uuid) FOR UPDATE'),{'i':i});done=threading.Event();out=[];t=threading.Thread(target=waiting);t.start();ck('image lock blocks competing write',not done.wait(.35));tx.commit();hold.close();ck('write resumes after image unlock',done.wait(10));t.join();ck('blocked PUT committed200',out[0].status_code==200,out[0].text)
        put(i,other,True);ck('last committed intent wins unique manual',row(snap(i),other)['manual_override'] is True and sql('SELECT count(*) FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s',{'i':i,'s':other}).scalar_one()==1)
        # Full explicit sync actual Qdrant HTTP + ledger; valid cursor and missing behavior.
        ck('before explicit sync vector still stale','qa_plain' not in point(i)['scenes']);z=sync(limit=1);ck('full batch requires extra empty finish',z['done'] is False and z['next_after']==i)
        z=sync(after=z['next_after'],limit=1);ck('empty batch finishes scan',z['done'] is True and z['processed']==0)
        s=snap(i);ck('actual server payload equals PG effective codes',point(i)['scenes']==s['scenes']);ck('actual ledger catches payload_version',sql('SELECT synced_version FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':i}).scalar_one()==ver(i))
        ck('actual HTTP list and search share membership',req('GET','/api/images?scene=qa_plain')['total']==1 and search('qa_plain')['items'][0]['id']==i)
        sr=search('qa_rule');ck('actual HTTP heating label output',sr['outputs_heating'] is True and sr['items'][0]['scene_label']=='QA Label' and sr['items'][0]['food_state']=='cooked' and 'heating_group' in sr['items'][0])
        # A point absent from current vector server must not get marked pushed.
        req('POST','/api/index',files={'file':('missing.png',png(64),'image/png')})
        ids=sql('SELECT id::text FROM images ORDER BY id').scalars().all();missing=next(x for x in ids if x!=i);vectors.delete(missing);sql('DELETE FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':missing})
        z=sync();ck('missing scan done is not repaired',z['done'] is True and z['payload_missing']==[missing] and z['payload_pushed']==1 and sql('SELECT count(*) FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':missing}).scalar_one()==0)
        # Genuine Qdrant not-ready is simulated only at network client boundary; no PG rollback promise.
        original=stack.scene_sync_uc._vectors
        class FailingVector:
            def set_payloads(self,p):
                from app.core import errors as err
                raise err.index_qdrant_failed('QA vector outage')
        stack.scene_sync_uc._vectors=FailingVector()
        put(i,other,False);v=ver(i);b=req('POST','/api/scenes/sync',502,json={'after':None,'limit':10});ck('sync502 leaves PG manual intent',b['error']=='INDEX_QDRANT_FAILED' and row(snap(i),other)['manual_override'] is False and ver(i)==v)
        stack.scene_sync_uc._vectors=original;sync(after=None,limit=10);ck('same cursor retry repairs actual server','qa_plain' not in point(i)['scenes'])
        # Ledger failure after real vector write: no cross-store atomicity; retry repairs.
        put(i,other,True);v=ver(i)
        sql("CREATE FUNCTION qa_reject_ledger() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'QA ledger failure'; END $$")
        sql('CREATE TRIGGER qa_fail_ledger BEFORE INSERT OR UPDATE ON image_embeddings FOR EACH ROW EXECUTE FUNCTION qa_reject_ledger()')
        b=req('POST','/api/scenes/sync',502,json={'after':None,'limit':10})
        ck('ledger fault502 after actual vector payload write',b['error']=='INDEX_POSTGRES_FAILED' and 'qa_plain' in point(i)['scenes'])
        ck('ledger failure preserves PG intent and prior ledger',row(snap(i),other)['manual_override'] is True and sql('SELECT synced_version FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':i}).scalar_one()<v)
        sql('DROP TRIGGER qa_fail_ledger ON image_embeddings');sql('DROP FUNCTION qa_reject_ledger()');sync(after=None,limit=10)
        ck('same cursor retry repairs actual ledger',sql('SELECT synced_version FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':i}).scalar_one()==v)
        # Two real HTTP requests queue on the same image row, then serialize.
        put(i,other,False);hold=engine.connect();tx=hold.begin();hold.execute(text('SELECT id FROM images WHERE id=CAST(:i AS uuid) FOR UPDATE'),{'i':i})
        completed=[];events=[threading.Event(),threading.Event()]
        def concurrent(k,value):
            try:
                rr=httpx.put(base+path+f'/{other}',json={'is_member':value},timeout=10,trust_env=False)
                completed.append((rr.status_code,rr.json(),value))
                requests.append({'method':'PUT','path':path+f'/{other}','status':rr.status_code,'body':rr.json(),'scenario':'same-image concurrent'})
            finally:events[k].set()
        threads=[threading.Thread(target=concurrent,args=(k,value)) for k,value in enumerate([True,False])]
        for tt in threads:tt.start()
        ck('both concurrent HTTP writes wait for row lock',not events[0].wait(.25) and not events[1].is_set());tx.commit();hold.close()
        for tt in threads:tt.join(10)
        ck('both concurrent writes finish200',len(completed)==2 and all(x[0]==200 for x in completed))
        latest=max(completed,key=lambda x:x[1]['payload_version']);final=snap(i)
        ck('serialized last effective version wins unique row',final['payload_version']==latest[1]['payload_version'] and row(final,other)['manual_override']==latest[2] and sql('SELECT count(*) FROM image_scenes WHERE image_id=CAST(:i AS uuid) AND scene_id=:s',{'i':i,'s':other}).scalar_one()==1)
        sync();ck('post-concurrency real payload ledger consistent',point(i)['scenes']==final['scenes'] and sql('SELECT synced_version FROM image_embeddings WHERE image_id=CAST(:i AS uuid)',{'i':i}).scalar_one()==final['payload_version'])
        result['fixture_image']=i;result['sql_lock_order']=statements
import re
try:run();result['exit_code']=0
except BaseException:result['exit_code']=1;result['exception']=traceback.format_exc();print(result['exception'])
finally:
    if server:server.should_exit=True
    if th:th.join(10)
    if vectors:vectors.close()
    if store:store.close()
    if engine:engine.dispose()
    result.update(assertions_total=len(checks),assertions_passed=sum(x['pass'] for x in checks),http_requests=len(requests),status_counts=dict(collections.Counter(x['status'] for x in requests)),http_stopped=not th or not th.is_alive(),reported_at=datetime.now(ZoneInfo('America/Denver')).isoformat())
    (ROOT/'.qa/scene-completion-backend-http-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k not in ['checks','requests','sql_lock_order','exception']},ensure_ascii=False))
sys.exit(result['exit_code'])
