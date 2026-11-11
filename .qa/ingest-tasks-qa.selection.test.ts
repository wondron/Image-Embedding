import { beforeEach, describe, expect, it, vi } from 'vitest'
const h=vi.hoisted(()=>({slots:[] as any[],effects:[] as any[],cursor:0,dirty:false,pending:[] as any[],history:null as any}))
vi.mock('react',()=>({
 useState(initial:any){const n=h.cursor++;if(!(n in h.slots))h.slots[n]=typeof initial==='function'?initial():initial;return[h.slots[n],(update:any)=>{const v=typeof update==='function'?update(h.slots[n]):update;if(v!==h.slots[n]){h.slots[n]=v;h.dirty=true}}]},
 useEffect(fn:any,deps:any[]){const n=h.cursor++;const prev=h.effects[n];if(!prev||deps.some((d,i)=>d!==prev.deps[i]))h.pending.push(()=>{prev?.cleanup?.();h.effects[n]={deps,cleanup:fn()}})}
}))
vi.mock('../frontend/src/features/index-tasks/useIndexTaskHistory',()=>({TASK_PAGE_SIZE:20,useIndexTaskHistory:()=>h.history}))
import { IndexTaskHistory } from '../frontend/src/features/index-tasks/IndexTaskHistory'
import { TaskDetails } from '../frontend/src/features/index-tasks/TaskDetails'
function render(){let out:any,n=0;do{h.dirty=false;h.cursor=0;out=IndexTaskHistory({apiBase:'fixture'});h.pending.splice(0).forEach(fn=>fn());if(++n>15)throw Error('unit rerender loop')}while(h.dirty);return out}
function nodes(node:any):any[]{if(!node||typeof node!=='object')return[];return[node,...(Array.isArray(node.props?.children)?node.props.children:[node.props?.children]).flat(Infinity).flatMap(nodes)]}
const row=(patch:any={})=>({id:1,filename:'old.jpg',status:'succeeded',newImage:false,image:null,...patch})
beforeEach(()=>{h.slots=[];h.effects=[];h.cursor=0;h.pending=[];h.dirty=false;h.history={key:'same-query',query:{status:null,page:1},data:{total:1,counts:{succeeded:1,failed:0},items:[row()],readTraceId:'read-old'},loading:false,error:null,readAt:null,changed:false,filter:vi.fn(),page:vi.fn(),refresh:vi.fn()}})
describe('T25 component function/effect unit simulation, NOT GUI or native Dialog',()=>{
 it('refresh same id uses latest row and GET trace for selected details',()=>{let out=render();nodes(out).find(n=>n.type==='button'&&n.props.children==='查看详情').props.onClick();out=render();expect(nodes(out).find(n=>n.type===TaskDetails).props.task.filename).toBe('old.jpg');h.history={...h.history,data:{...h.history.data,items:[row({filename:'latest.jpg',image:{id:'current'}})],readTraceId:'read-new'}};out=render();const detail=nodes(out).find(n=>n.type===TaskDetails);expect(detail.props.task.filename).toBe('latest.jpg');expect(detail.props.task.image.id).toBe('current');expect(detail.props.readTraceId).toBe('read-new')})
 it('successful refresh removes id from current page, closes selection with non-deletion notice',()=>{let out=render();nodes(out).find(n=>n.type==='button'&&n.props.children==='查看详情').props.onClick();render();h.history={...h.history,data:{...h.history.data,items:[row({id:2})]}};out=render();expect(nodes(out).some(n=>n.type===TaskDetails)).toBe(false);expect(nodes(out).some(n=>n.type==='p'&&n.props.children==='该记录不在当前页，请重新选择')).toBe(true)})
})
