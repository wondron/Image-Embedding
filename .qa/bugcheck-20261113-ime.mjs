import { chromium } from '../frontend/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
const browser = await chromium.launch();
const page = await browser.newPage();
const category = (id,code,parent_id,name) => ({id,code,parent_id,path:String(id),name,names:[{lang:'zh',name,kind:'primary'}],cuisines:[],heating_group:null,sort_order:0,is_active:true,image_count:0,scene_rule_count:0,created_at:null,updated_at:null});
await page.route(url => url.pathname.startsWith('/api/'), route => {
  const path = new URL(route.request().url()).pathname;
  if(path === '/api/categories') return route.fulfill({json:{ok:true,categories:[category(1,'dish',null,'菜品'),category(2,'dish.pizza',1,'披萨'),category(3,'ingredient',null,'食材')],heating_groups:[],root_codes:['ingredient','dish','dessert','beverage','mixed','other']}});
  if(path === '/api/enums') return route.fulfill({json:{ok:true,enums:[]}});
  if(path === '/api/ping') return route.fulfill({json:{ok:true,max_image_bytes:15728640}});
  return route.abort('blockedbyclient');
});
try {
  await page.goto('http://127.0.0.1:5173/?view=categories');
  await page.getByRole('button',{name:'新建类别',exact:true}).click();
  const input = page.locator('#category-form-parentId');
  await input.fill('菜');
  const before = {input:await input.inputValue(),code:await page.locator('#category-form-code').inputValue(),expanded:await input.getAttribute('aria-expanded')};
  await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',isComposing:true,bubbles:true,cancelable:true});
  const after = {input:await input.inputValue(),code:await page.locator('#category-form-code').inputValue(),expanded:await input.getAttribute('aria-expanded')};
  const result={before,after,bug:before.input!==after.input && after.code==='dish.'};
  console.log(JSON.stringify(result,null,2));
  await fs.writeFile('.qa/bugcheck-20261113-ime.json',JSON.stringify(result,null,2));
  await page.screenshot({path:'.qa/bugcheck-20261113-ime.png'});
} finally { await browser.close(); }
