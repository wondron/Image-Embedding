import { describe,expect,it } from 'vitest'
import { listIndexTasks } from '../frontend/src/entities/task/api'
const baseUrl=process.env.QA_INGEST_REAL_BASE!
if(!/^http:\/\/127\.0\.0\.1:57511$/.test(baseUrl||''))throw Error('Explicit QA loopback only')
describe('FE entity against actual TCP HTTP and migrated isolated PostgreSQL; no browser',()=>{
 it('default omit status/20 counts and original-vs-read traces',async()=>{const d=await listIndexTasks({baseUrl});expect(d.pageSize).toBe(20);expect(d.status).toBeNull();expect(d.total).toBe(43);expect(d.counts).toEqual({succeeded:29,failed:14});expect(d.items).toHaveLength(20);expect(d.readTraceId).toBeTruthy();expect(d.readTraceId).not.toBe(d.items[0].traceId)})
 it('succeeded filtered total29 global counts43',async()=>{const d=await listIndexTasks({baseUrl,status:'succeeded',page:2});expect(d.page).toBe(2);expect(d.total).toBe(29);expect(d.items).toHaveLength(9);expect(d.items.every(x=>x.status==='succeeded')).toBe(true);expect(d.counts.failed).toBe(14)})
 it('failed nulls survive real wire mapping',async()=>{const d=await listIndexTasks({baseUrl,status:'failed'});const r=d.items.find(x=>x.id===3)!;expect(r).toMatchObject({newImage:null,image:null,imageId:null,durationMs:null,status:'failed'});expect(r.errorCode).toBe('INDEX_BAD_IMAGE')})
 it('0 and false survive real wire; record ids do not merge same image',async()=>{const d=await listIndexTasks({baseUrl,page:3});const r=d.items.find(x=>x.id===1)!;expect(r.durationMs).toBe(0);expect(r.newImage).toBe(false);expect(r.status).toBe('succeeded');expect(d.items.map(x=>x.id)).toEqual([3,2,1]);expect(d.items.find(x=>x.id===2)?.newImage).toBe(true)})
 it('missing current image projection does not change historical success',async()=>{const d=await listIndexTasks({baseUrl,page:2});const r=d.items.find(x=>x.id===4)!;expect(r.status).toBe('succeeded');expect(r.image).toBeNull();expect(r.imageId).toBeTruthy()})
})
