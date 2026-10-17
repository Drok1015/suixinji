// Transport contract: arbitrary UTF-8 fragmentation, terminal events, and interrupted streams.
const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const ts=require('../frontend/node_modules/typescript');
const source=fs.readFileSync('mobile/common/sse.uts','utf8').replace(/import[^\n]+\n/,'');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
function session(){let options,chunk;const events=[],errors=[],exports={};let closed=0;
 const ctx={exports,API_BASE:'/api',headers:()=>({}),Uint8Array,uni:{request:(o)=>{options=o;return {onChunkReceived:(cb)=>chunk=cb}},removeStorageSync:()=>{},reLaunch:()=>{}}};
 vm.runInNewContext(compiled,ctx);exports.streamChat('id',{}, {onEvent:(k,d)=>events.push([k,d]),onError:e=>errors.push(e),onClose:()=>closed++});
 return {events,errors,feed:(bytes)=>chunk({data:bytes.buffer}),finish:()=>options.success({statusCode:200}),closed:()=>closed};}
const good=session();const bytes=new TextEncoder().encode('event: delta\ndata: {"text":"土豆豆角，小排畸"}\n\nevent: done\ndata: {}\n\n');
for(const byte of bytes)good.feed(new Uint8Array([byte]));good.finish();
assert.equal(good.events[0][1].text,'土豆豆角，小排畸');assert.equal(good.events[1][0],'done');assert.equal(good.errors.length,0);assert.equal(good.closed(),1);
const broken=session();broken.feed(new TextEncoder().encode('event: delta\ndata: {"text":"半截'));broken.finish();assert.equal(broken.errors.length,1);
console.log('MOBILE_SSE_UTF8_AND_INTERRUPTION_OK');
