// ARU browser orchestration extracted from the verified category crawl.
// Run only inside CUA REPL with supported, authorized tab handles.
export async function openCollector({tabs,outputDir,readerPath,resume=false}) {
const aruFs=await import('node:fs/promises');
if(!Array.isArray(tabs)||tabs.length!==3) throw Error('Provide three supported CUA tabs in the same browser');
const [currentLogin,workerA,workerB]=tabs;
const repairDir=outputDir;
const aruCapture='('+ (await aruFs.readFile(readerPath,'utf8')).trim()+')()';
await aruFs.mkdir(repairDir+'/pages',{recursive:true});
let categoryMap,scanQueue,scanDone,scanReports;
if(resume){
 const categories=JSON.parse(await aruFs.readFile(repairDir+'/categories-menu.json','utf8'));
 const state=JSON.parse(await aruFs.readFile(repairDir+'/scan-progress.json','utf8'));
 categoryMap=new Map(categories.map(c=>[c.id,c]));scanQueue=state.remaining;scanDone=new Set(state.done);scanReports=state.reports;
 for(const key of scanDone){const [id,p]=key.split('|');const filename=id.split('/').filter(Boolean).join('__')+'__'+String(p).padStart(3,'0')+'.json';const d=JSON.parse(await aruFs.readFile(repairDir+'/pages/'+filename,'utf8'));if(d.category_id!==id||d.page!==Number(p)||!d.authenticated||!Number.isFinite(Date.parse(d.captured_at))||Date.now()-Date.parse(d.captured_at)>86400000)throw Error('Invalid or stale saved page: '+key);}
}else{
 try{await aruFs.access(repairDir+'/scan-progress.json');throw Error('Existing run: explicitly resume instead of overwriting');}catch(e){if(e.code!=='ENOENT')throw e;}
 const seed=await tabs[0].playwright.evaluate(aruCapture);if(!seed.authenticated)throw Error('Sign in through supplier UI before collecting');
 categoryMap=new Map(seed.categories.map(c=>[c.id,c]));for(const c of [{id:'/vse-tovary/',name:'Все товары',url:'https://aru.ooo/vse-tovary/'},{id:'/novinki/',name:'Новинки',url:'https://aru.ooo/novinki/'}])categoryMap.set(c.id,c);
 scanQueue=[...categoryMap.values()].map(c=>({category_id:c.id,page:1,url:c.url}));scanDone=new Set();scanReports=[];
}
const persistCapture=async(job,data)=>{const u=new URL(data.url);if(!data.authenticated||!data.title||u.pathname!==job.category_id||Number(u.searchParams.get('page')||1)!==job.page)return false;data.category_id=job.category_id;data.page=job.page;data.captured_at=new Date().toISOString();const name=job.category_id.split('/').filter(Boolean).join('__')+'__'+String(job.page).padStart(3,'0')+'.json';await aruFs.writeFile(repairDir+'/pages/'+name,JSON.stringify(data,null,2));if(job.page===1){categoryMap.set(job.category_id,{id:job.category_id,name:data.title,url:'https://aru.ooo'+job.category_id});for(let page=2;page<=data.pages;page++)scanQueue.push({category_id:job.category_id,page,url:'https://aru.ooo'+job.category_id+'?page='+page});for(const c of [...data.categories,...data.children])if(!categoryMap.has(c.id)){categoryMap.set(c.id,c);scanQueue.push({category_id:c.id,page:1,url:c.url});}}for(const item of data.items){const parts=new URL(item.url).pathname.split('/').filter(Boolean);parts.pop();for(let n=1;n<=parts.length;n++){const id='/'+parts.slice(0,n).join('/')+'/';if(!categoryMap.has(id)){categoryMap.set(id,{id,name:'',url:'https://aru.ooo'+id});scanQueue.push({category_id:id,page:1,url:'https://aru.ooo'+id});}}}scanDone.add(job.category_id+'|'+job.page);scanQueue.shift();scanReports.push({id:job.category_id,page:job.page,pages:data.pages,items:data.items.length,children:data.children.length,title:data.title});await aruFs.writeFile(repairDir+'/scan-progress.json',JSON.stringify({done:[...scanDone],remaining:scanQueue,reports:scanReports}));await aruFs.writeFile(repairDir+'/categories-menu.json',JSON.stringify([...categoryMap.values()],null,2));return true;};
const saveAruCapture=async(job,data)=>{
 if(!Number.isInteger(data.pages)||data.pages<job.page||data.pages>1000||!Array.isArray(data.items))throw Error('Invalid pagination');
 if(data.pages>1&&!data.items.length)throw Error('Empty paginated page');
 const prior=scanReports.find(r=>r.id===job.category_id);if(prior&&prior.pages!==data.pages)throw Error('Pagination changed; new run required');
 const ids=new Set();for(const row of data.items){if(!/^\d+$/.test(String(row.id))||ids.has(row.id)||!row.name)throw Error('Invalid or duplicate product');ids.add(row.id);const u=new URL(row.url);if(u.origin!=='https://aru.ooo')throw Error('Unexpected product host');}
 return await persistCapture(job,data);
};
const scanTriple=async(limit=36)=>{let count=0,start=Date.now();while(scanQueue.length&&count<limit&&Date.now()-start<42000){while(scanQueue[0]&&scanDone.has(scanQueue[0].category_id+'|'+scanQueue[0].page))scanQueue.shift();const jobs=scanQueue.slice(0,Math.min(3,limit-count));const tabs=[currentLogin,workerA,workerB];const results=await Promise.allSettled(jobs.map(async(job,i)=>{if(await tabs[i].playwright.evaluate(()=>document.URL)!==job.url)await tabs[i].goto(job.url);const data=await tabs[i].playwright.evaluate(aruCapture,undefined,{timeoutMs:5000});const view=await tabs[i].playwright.evaluate(()=>document.querySelector('.js-products-switcher-item._active')?.getAttribute('data-view'));if(data.items.length&&view!=='list')throw Error('List view required');return data;}));for(let i=0;i<jobs.length;i++){if(results[i].status!=='fulfilled')return{status:'error',tab:i,message:String(results[i].reason).slice(0,180),done:scanDone.size,next:jobs[i]};if(!await saveAruCapture(jobs[i],results[i].value))return{status:'mismatch',done:scanDone.size,next:jobs[i],url:results[i].value.url};count++;}}return{status:'saved',captured:count,done:scanDone.size,remaining:scanQueue.length,categories:categoryMap.size,next:scanQueue[0]};};
const checkpoint=async()=>{await aruFs.writeFile(repairDir+'/categories-menu.json',JSON.stringify([...categoryMap.values()],null,2));await aruFs.writeFile(repairDir+'/scan-progress.json',JSON.stringify({done:[...scanDone],remaining:scanQueue,reports:scanReports}));};
await checkpoint();
return {batch:async(limit=36)=>{if(!Number.isInteger(limit)||limit<1||limit>40)throw Error('Batch size must be 1..40');const result=await scanTriple(limit);await checkpoint();return result;},status:()=>({done:scanDone.size,remaining:scanQueue.length,categories:categoryMap.size,next:scanQueue[0]}),checkpoint};
}
