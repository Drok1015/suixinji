const {chromium}=require('/opt/homebrew/lib/node_modules/playwright');const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch({headless:true});const p=await b.newPage({viewport:{width:390,height:844}});const errors=[];let cid='';
 p.on('pageerror',e=>errors.push(e.message));p.on('response',async r=>{if(r.request().method()==='POST'&&r.url().endsWith('/api/conversations'))cid=(await r.json()).id});
 try {
 await p.goto('http://127.0.0.1:5174/');
 await p.getByText('今天，想聊点什么？',{exact:true}).waitFor();await p.screenshot({path:'docs/qa/mobile-home.png'});
 await p.getByText('记忆',{exact:true}).last().click();await p.getByText('找一条记忆…',{exact:true}).waitFor();await p.getByText('小排畸产检安排',{exact:true}).waitFor();
 await p.screenshot({path:'docs/qa/mobile-memory.png'});await p.getByText('小排畸产检安排',{exact:true}).click();await p.getByText('保存记忆',{exact:true}).waitFor();
 await p.waitForFunction(()=>Array.from(document.querySelectorAll('input')).some(e=>e.value==='小排畸产检安排'));assert.equal(await p.locator('input:visible').first().inputValue(),'小排畸产检安排');await p.screenshot({path:'docs/qa/mobile-editor.png'});
 await p.goto('http://127.0.0.1:5174/#/pages/profile/index');await p.getByText('deepseek-v4.1-flash',{exact:true}).waitFor();await p.screenshot({path:'docs/qa/mobile-profile.png'});
 await p.goto('http://127.0.0.1:5174/#/pages/chat/index');await p.locator('textarea').fill('请计算17+25，只回复结果，不用保存记忆。');await p.getByText('↑',{exact:true}).click();
 await p.getByText('42',{exact:true}).waitFor({timeout:180000});await p.getByText('↑',{exact:true}).waitFor();await p.screenshot({path:'docs/qa/mobile-chat.png'});
 await p.locator('textarea').fill('写一篇很长的童话故事，不用保存记忆。');await p.getByText('↑',{exact:true}).click();await p.getByText('■',{exact:true}).click();await p.getByText('已停止',{exact:true}).waitFor({timeout:30000});await p.getByText('↑',{exact:true}).waitFor();
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);assert.deepEqual(errors,[]);console.log('MOBILE_BROWSER_LOGIN_MEMORY_EDITOR_CHAT_STOP_OK');
 } finally {
 const token=await p.evaluate(()=>uni.getStorageSync('self-agent-token'));const headers={'Authorization':'Bearer '+token,'X-Self-Agent':'1'};
 if(cid){await p.request.post('http://127.0.0.1:5174/self-agent/api/conversations/'+cid+'/stop',{headers});const r=await p.request.delete('http://127.0.0.1:5174/self-agent/api/conversations/'+cid,{headers});assert(r.ok())}
 await p.request.post('http://127.0.0.1:5174/self-agent/api/logout',{headers});await b.close();console.log('MOBILE_QA_CLEANED');
 }
})().catch(e=>{console.error(e.message);process.exit(1)})
