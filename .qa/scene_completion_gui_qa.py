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
DB='postgresql+psycopg://qa_scene@127.0.0.1:64664/qa_scene_completion_gui_1055';QURL='http://127.0.0.1:64667';COL='qa_scene_completion_gui_1055'
frontend=None;checks=[];requests=[];audit=[];server=None;th=None;store=None;vectors=None;engine=None;rt=None
result={'target':{'db':'127.0.0.1:64664/qa_scene_completion_gui_1055','qdrant':QURL,'collection':COL,'code_root':str(ROOT),'encoder':'deterministic4D stub','OSS':'memory-only MockImageStorage'},'checks':checks,'requests':requests,'evidence':'actual loopback HTTP; registered current app.api.router; real PG + Qdrant HTTP; no TestClient'}
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
    engine=create_engine(DB);ck('QA database identity',sql('SELECT current_database()').scalar_one()=='qa_scene_completion_gui_1055')
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
    global frontend
    import subprocess
    with httpx.Client(base_url=base,timeout=15,trust_env=False) as c:
        def call(method,path,**kw):
            rr=c.request(method,path,**kw);rr.raise_for_status();return rr.json()
        active=call('POST','/api/scenes',json={'code':'qa_gui','name':'QA GUI dynamic','outputs_heating':True})['scene']['id']
        inactive=call('POST','/api/scenes',json={'code':'qa_gui_inactive','name':'QA inactive old manual','is_active':False})['scene']['id']
        alloff=call('POST','/api/scenes',json={'code':'qa_gui_all_off','name':'QA inactive include all','include_all':True,'is_active':False})['scene']['id']
        cat=call('POST','/api/categories',json={'code':'dish','names':[{'lang':'zh','name':'QA dish','kind':'primary'}]})['category']['id']
        label=call('POST',f'/api/scenes/{active}/labels',json={'code':'qa_gui_label','name':'QA GUI label'})['scene']['labels'][0]['id']
        call('POST',f'/api/scenes/{active}/rules',json={'category_id':cat,'label_id':label})
        image=call('POST','/api/index',files={'file':('qa-scene.png',png(32),'image/png')},data={'category':'dish','food_state':'cooked'})['indexed'][0]['id']
        sql("INSERT INTO image_scenes(image_id,scene_id,origin,is_member) VALUES(CAST(:i AS uuid),:s,'manual',true)",{'i':image,'s':inactive})
        call('POST','/api/scenes/sync',json={'limit':10})
    (ROOT/'.qa/scene-completion-query.png').write_bytes(png(32))
    fsock=socket.socket();fsock.bind(('127.0.0.1',0));feport=fsock.getsockname()[1];fsock.close()
    env=dict(os.environ);env['VITE_API_PROXY_TARGET']=base
    log=open(ROOT/'.qa/scene-completion-gui-frontend.log','w',encoding='utf-8')
    frontend=subprocess.Popen(['D:/98-softwaredata/nodejs/node.exe',str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(feport),'--strictPort'],cwd=ROOT/'frontend',env=env,stdout=log,stderr=subprocess.STDOUT)
    fe=f'http://127.0.0.1:{feport}'
    deadline=time.monotonic()+20
    while True:
        if frontend.poll() is not None:raise RuntimeError('QA Vite exited')
        try:
            rr=httpx.get(fe+'/api/scenes',timeout=2,trust_env=False)
            if rr.status_code==200 and any(x['code']=='qa_gui' for x in rr.json()['scenes']):break
        except (httpx.HTTPError,ValueError):pass
        if time.monotonic()>deadline:raise RuntimeError('QA Vite proxy not ready')
        threading.Event().wait(.1)
    ready={'ready':True,'head':'08dea28228cad79da67eefb3aadbc6ef55078a42','code_root':str(ROOT),'api_url':base,'frontend_url':fe,'scene_management_url':fe+f'/?view=scenes&scene={active}&sceneTab=overview','image_id':image,'active_scene_id':active,'inactive_scene_id':inactive,'inactive_include_all_scene_id':alloff,'coordinator_pid':os.getpid(),'frontend_pid':frontend.pid,'api_pid':os.getpid(),'db':'qa_scene_completion_gui_1055','pg':'127.0.0.1:64664','qdrant':QURL,'collection':COL,'proxy_verified_HTTP':200,'network_boundary':'ONLY wondron127.0.0.1; NOT box loopback. No tunnel/public binding. API base must be blank/use proxy or exactQA API; do not use saved business API base.','encoder':'deterministic4D stub','OSS':'memory mock with invalidQA HTTPS URLs; no real images uploaded/CDN thumbnails expected unavailable','query_file':str(ROOT/'.qa/scene-completion-query.png'),'ready_at':datetime.now(ZoneInfo('America/Denver')).isoformat(),'lifetime_seconds':1200,'expires_at':datetime.fromtimestamp(time.time()+1200,ZoneInfo('America/Denver')).isoformat(),'cleanup':'Coordinator natural TTL finally stops its exact frontend and removes only two QA container IDs; explicit cleanup script also provided.'}
    (ROOT/'.qa/scene-completion-gui-ready.json').write_text(json.dumps(ready,ensure_ascii=False,indent=2),encoding='utf-8')
    cleanup=f"Stop-Process -Id {frontend.pid} -ErrorAction SilentlyContinue\nStop-Process -Id {os.getpid()} -ErrorAction SilentlyContinue\ndocker stop 797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4 ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb\ndocker rm 797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4 ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb\n"
    (ROOT/'.qa/scene-completion-gui-cleanup.ps1').write_text(cleanup,encoding='utf-8')
    print('QA_GUI_READY '+json.dumps(ready,ensure_ascii=True),flush=True)
    end=time.monotonic()+1200
    while time.monotonic()<end and frontend.poll() is None and th.is_alive():threading.Event().wait(1)
import re,subprocess
try:run()
except BaseException:traceback.print_exc()
finally:
    if server:server.should_exit=True
    if th:th.join(10)
    if frontend is not None and frontend.poll() is None:frontend.terminate();frontend.wait(10)
    if vectors:vectors.close()
    if store:store.close()
    if engine:engine.dispose()
    subprocess.run(['docker','stop','797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4','ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb'],check=False)
    subprocess.run(['docker','rm','797c6a3c97e166e9ad5c939c24e3bca885b355490f8a71793f1efc423176eda4','ad801c0769beada83f230dd38013d4b691249262692238756fb586aa470d8aeb'],check=False)
    (ROOT/'.qa/scene-completion-gui-ended.json').write_text(json.dumps({'ended_at':datetime.now(ZoneInfo('America/Denver')).isoformat(),'frontend_stopped':frontend is None or frontend.poll() is not None,'api_stopped':not th or not th.is_alive()}),encoding='utf-8')
