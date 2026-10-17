<script setup lang="ts">
import brandUrl from './assets/brand.png'
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import AppSelect from './components/AppSelect.vue'

type Topic = { id: string; name: string; description: string; memory_count: number }
type Memory = { id: string; scope: string; topic_id: string | null; key: string; content: string; source_content?: string; updated_at: string }
type Route = { status?: string; topic_ids?: string[]; read_topic_ids?: string[] | null; topic_names?: string[]; memory_count?: number; notice?: string }
type Message = { id: string; role: string; content: string; status: string; route: Route }
type Conversation = { id: string; title: string; updated_at: string }
type Pending = { id: string; content: string; job_status: string; error?: string }
const base = import.meta.env.BASE_URL.replace(/\/$/, '') + '/api'
const authenticated = ref(false), checking = ref(true)
const error = ref(''), page = ref('chat'), sidebar = ref(false), conversations = ref<Conversation[]>([])
const currentId = ref(''), messages = ref<Message[]>([]), draft = ref(''), busy = ref(false)
const phase = ref(''), memoryData = ref<{topics:Topic[];memories:Memory[];pending:Pending[];jobs:{status:string;count:number}[]}>({topics:[],memories:[],pending:[],jobs:[]})
const filter = ref('global'), search = ref(''), scroller = ref<HTMLElement>(), input = ref<HTMLTextAreaElement>()
const model = ref('DeepSeek'), route = ref<Route>({}), stopping = ref(false)
const modal = ref<'memory'|'topic'|null>(null), editingId = ref('')
const form = ref({key:'',content:'',scope:'global',topic_id:'',name:'',description:''}), saving = ref(false)
let timer: ReturnType<typeof setInterval> | undefined
const title = computed(()=>conversations.value.find(c=>c.id===currentId.value)?.title || '新的对话')
const filteredMemories = computed(()=>memoryData.value.memories.filter(m =>
  (filter.value==='all' || (filter.value==='global' ? m.scope==='global' : m.topic_id===filter.value)) &&
  (!search.value || (m.key+m.content).toLowerCase().includes(search.value.toLowerCase()))))
const jobCount = computed(()=>memoryData.value.jobs.filter(j=>['queued','running'].includes(j.status)).reduce((n,j)=>n+j.count,0))
const topicNames = (r:Route) => r.topic_names || memoryData.value.topics.filter(t=>(r.read_topic_ids ?? r.topic_ids)?.includes(t.id)).map(t=>t.name)
const html = (value:string) => DOMPurify.sanitize(marked.parse(value,{async:false}) as string)
const date = (value:string) => new Date(value).toLocaleDateString('zh-CN',{month:'short',day:'numeric'})

async function api<T=any>(path:string, method='GET', body?:unknown):Promise<T> {
  const response = await fetch(base+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Self-Agent':'1'},body:body===undefined?undefined:JSON.stringify(body)})
  if(response.status===401 && path!='/login') authenticated.value=false
  const data = await response.json()
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'请求失败，请稍后重试')
  return data
}
function report(e:unknown){ error.value=e instanceof Error?e.message:'请求失败' }
async function init(){
  const me=await api('/me'); authenticated.value=true; model.value=me.model
  await Promise.all([loadConversations(),loadMemory()])
  if(!currentId.value && conversations.value.length) await select(conversations.value[0]!.id)
}
async function loadConversations(){conversations.value=await api('/conversations')}
async function loadMemory(){memoryData.value=await api('/memory')}
async function select(id:string){
  if(busy.value) return
  currentId.value=id; page.value='chat'; sidebar.value=false; error.value=''
  try{
    const data=await api(`/conversations/${id}/messages`)
    messages.value=data.messages; route.value=messages.value.filter(m=>m.role==='user').at(-1)?.route || {}
    if(data.active) error.value='此会话在另一个页面生成回复，稍后刷新即可查看。'
    await scroll()
  }catch(e){report(e)}
}
async function newChat(){
  if(busy.value) return
  currentId.value='';messages.value=[];route.value={};page.value='chat';sidebar.value=false;draft.value='';error.value=''
  await nextTick();input.value?.focus()
}
async function rename(c:Conversation){
  const value=window.prompt('会话名称',c.title)
  if(value?.trim()){try{await api(`/conversations/${c.id}`,'PATCH',{title:value.trim()});await loadConversations()}catch(e){report(e)}}
}
async function deleteChat(c:Conversation){
  if(!window.confirm('删除此会话及聊天记录？已整理的长期记忆会保留，可以在记忆页面删除。')) return
  try{await api(`/conversations/${c.id}`,'DELETE');if(currentId.value===c.id) await newChat();await loadConversations()}catch(e){report(e)}
}
async function scroll(){ await nextTick();scroller.value?.scrollTo({top:scroller.value.scrollHeight,behavior:'smooth'}) }
async function send(retry?:Message){
  if(busy.value || (!retry && !draft.value.trim()))return
  busy.value=true; stopping.value=false; phase.value='正在匹配记忆';error.value=''
  const text=retry?retry.content:draft.value.trim();draft.value=''
  let cid=currentId.value
  try{
    if(!cid){const convo=await api('/conversations','POST');cid=convo.id;currentId.value=cid}
    if(retry) messages.value.pop()
    else messages.value.push({id:'local-user',role:'user',content:text,status:'complete',route:{}})
    const assistant:Message={id:'local-assistant',role:'assistant',content:'',status:'streaming',route:{}}
    messages.value.push(assistant)
    await scroll()
    const response=await fetch(base+`/conversations/${cid}/chat`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Self-Agent':'1'},body:JSON.stringify({content:text,retry_message_id:retry?.id})})
    if(!response.ok){const data=await response.json();if(response.status===401)authenticated.value=false;throw new Error(typeof data.detail==='string'?data.detail:'发送失败')}
    if(!response.body) throw new Error('浏览器无法读取流式回复')
    const reader=response.body.getReader(), decoder=new TextDecoder()
    let buffer='', finished=false
    while(true){
      const {done,value}=await reader.read();if(done)break
      buffer+=decoder.decode(value,{stream:true})
      const blocks=buffer.split('\n\n');buffer=blocks.pop() || ''
      for(const block of blocks){
        const kind=block.split('\n').find(line=>line.startsWith('event: '))?.slice(7)
        const raw=block.split('\n').filter(line=>line.startsWith('data: ')).map(line=>line.slice(6)).join('\n')
        if(!raw)continue
        const data=JSON.parse(raw)
        if(kind==='start'){
          assistant.id=data.assistant_id
          const user=messages.value[messages.value.length-2];if(user)user.id=data.user_id
        }else if(kind==='route'){
          route.value=data;assistant.route=data
          const user=messages.value[messages.value.length-2];if(user)user.route=data
          phase.value='正在回复'
        }else if(kind==='delta'){
          // Find the reactive item; mutating the raw object would not trigger rendering.
          const item=messages.value.find(m=>m.id===assistant.id);if(item)item.content+=data.text
          await scroll()
        }else if(kind==='done') {finished=true;assistant.status='complete'}
        else if(kind==='stopped'){finished=true;assistant.status='interrupted'}
        else if(kind==='error'){finished=true;assistant.status='error';error.value=data.message}
      }
    }
    if(!finished) throw new Error('连接已中断，可以重试回复')
  }catch(e){report(e)}finally{
    busy.value=false;phase.value='';stopping.value=false
    if(cid){try{await loadConversations();await select(cid);await loadMemory()}catch(e){report(e)}}
    await nextTick();input.value?.focus()
  }
}
async function stop(){
  stopping.value=true
  try{await api(`/conversations/${currentId.value}/stop`,'POST')}catch(e){stopping.value=false;report(e)}
}
function keydown(e:KeyboardEvent){if(e.key==='Enter' && !e.shiftKey && !e.isComposing){e.preventDefault();void send()}}
async function showMemory(){page.value='memory';sidebar.value=false;try{await loadMemory()}catch(e){report(e)}}
function openMemory(m?:Memory){
  editingId.value=m?.id || '';modal.value='memory'
  form.value={key:m?.key || '',content:m?.content || '',scope:m?.scope || (filter.value==='global'?'global':'topic'),topic_id:m?.topic_id || (filter.value==='global' || filter.value==='all'?'':filter.value),name:'',description:''}
}
function openTopic(t?:Topic){
  editingId.value=t?.id || '';modal.value='topic'
  form.value={key:'',content:'',scope:'global',topic_id:'',name:t?.name || '',description:t?.description || ''}
}
async function save(){
  saving.value=true;error.value=''
  try{
    if(modal.value==='topic'){
      await api('/topics'+(editingId.value?'/'+editingId.value:''),editingId.value?'PATCH':'POST',{name:form.value.name.trim(),description:form.value.description.trim()})
    }else{
      await api('/memories'+(editingId.value?'/'+editingId.value:''),editingId.value?'PATCH':'POST',{key:form.value.key.trim(),content:form.value.content.trim(),scope:form.value.scope,topic_id:form.value.scope==='global'?null:form.value.topic_id})
    }
    modal.value=null;await loadMemory()
  }catch(e){report(e)}finally{saving.value=false}
}
async function removeMemory(m:Memory){if(window.confirm('删除这条长期记忆？')){try{await api('/memories/'+m.id,'DELETE');await loadMemory()}catch(e){report(e)}}}
async function removeTopic(t:Topic){if(window.confirm(`删除「${t.name}」以及其中所有记忆？聊天记录会保留。`)){try{await api('/topics/'+t.id,'DELETE');filter.value='global';await loadMemory()}catch(e){report(e)}}}
async function merge(t:Topic){
  const candidates=memoryData.value.topics.filter(x=>x.id!==t.id)
  if(!candidates.length){error.value='请先创建另一个分类作为合并目标';return}
  const name=window.prompt('合并到哪个分类？请输入名称：\n'+candidates.map(x=>x.name).join('、'))
  if(!name)return
  const target=candidates.find(x=>x.name===name.trim())
  if(!target){error.value='没有找到这个分类';return}
  if(!window.confirm(`将「${t.name}」合并到「${target.name}」？两边记忆都会保留。`))return
  try{await api(`/topics/${t.id}/merge`,'POST',{target_id:target.id});filter.value=target.id;await loadMemory()}catch(e){report(e)}
}
async function assign(p:Pending,id:string){
  if(!id)return
  try{await api(`/pending/${p.id}/assign`,'POST',{topic_id:id});await loadMemory()}catch(e){report(e)}
}
async function retryMemory(){try{await api('/memory/retry','POST');await loadMemory()}catch(e){report(e)}}
onMounted(async()=>{
  try{await init()}catch(e){report(e)}finally{checking.value=false}
  timer=setInterval(()=>{if(authenticated.value&&(page.value==='memory'||jobCount.value>0))void loadMemory().catch(report)},5000)
})
onUnmounted(()=>{if(timer)clearInterval(timer)})
</script>

<template>
  <div v-if="checking" class="loading-screen"><img class="brand-symbol brand-image" :src="brandUrl" alt="随心记"/><p>正在打开你的空间…</p></div>
  <div v-else-if="!authenticated" class="login-screen"><div class="login-card"><h1>暂时无法连接</h1><p>{{error || '请检查网络后重试'}}</p><button class="primary" @click="init().catch(report)">重新连接</button></div></div>
  <div v-else class="shell">
    <div v-if="sidebar" class="backdrop" @click="sidebar=false"></div>
    <aside class="sidebar" :class="{open:sidebar}">
      <a class="brand" href="#" @click.prevent="newChat"><img class="brand-symbol brand-image" :src="brandUrl" alt=""/><span>随心记<small>随心聊，轻松记。</small></span></a>
      <button class="new-chat" :disabled="busy" @click="newChat"><span>＋</span> 新的对话 <span class="arrow">↗</span></button>
      <nav class="nav"><button :class="{selected:page==='chat'}" @click="page='chat';sidebar=false"><span>◉</span> 聊天</button><button :class="{selected:page==='memory'}" @click="showMemory"><span>▤</span> 我的记忆 <span class="count">{{memoryData.memories.length}}</span></button></nav>
      <div class="history-label">最近对话 <span>{{conversations.length}}</span></div>
      <div class="history"><div v-for="c in conversations" :key="c.id" class="history-item" :class="{active:currentId===c.id&&page==='chat'}"><button class="history-title" :disabled="busy" @click="select(c.id)">{{c.title}}</button><div class="history-actions"><button title="重命名会话" aria-label="重命名会话" :disabled="busy" @click="rename(c)">✎</button><button title="删除会话" aria-label="删除会话" :disabled="busy" @click="deleteChat(c)">×</button></div></div><p v-if="!conversations.length" class="empty-history">从第一句问候开始。</p></div>
      <div class="sidebar-bottom"><span class="avatar">我</span><div>我的私人空间<small>{{model}}</small></div></div>
    </aside>
    <main class="main">
      <header class="topbar"><button class="mobile-menu" aria-label="打开菜单" @click="sidebar=true">☰</button><div class="breadcrumb">我的空间 <span>/</span> {{page==='chat'?'对话':'记忆'}}</div><button class="memory-status" @click="showMemory"><span class="status-dot"></span>{{jobCount?`${jobCount} 项记忆整理中`:'记忆已就绪'}}<span>↗</span></button></header>
      <div v-if="error" class="error-banner" role="alert"><span>{{error}}</span><button aria-label="关闭提示" @click="error=''">×</button></div>
      <template v-if="page==='chat'">
        <div class="chat-header" v-if="messages.length"><h2>{{title}}</h2><span>让想法接着生长</span></div>
        <div class="chat-scroll" ref="scroller">
          <section v-if="!messages.length" class="welcome"><img class="welcome-icon welcome-brand" :src="brandUrl" alt="随心记 Logo"/><span class="eyebrow">随心记 · 随心聊，轻松记。</span><h1>今天，想聊些什么？</h1><p>通过聊天记录生活，自动整理，<br>在需要时找回。</p>
            <div class="suggestions"><button @click="draft='我想梳理一下最近的目标，帮我一起想想。';input?.focus()"><span>↗</span><strong>梳理我的目标</strong><small>把想做的事理清楚</small></button><button @click="draft='我更喜欢简洁的中文回复，请记住这个偏好。';input?.focus()"><span>♡</span><strong>让你更了解我</strong><small>告诉我你的习惯与偏好</small></button><button @click="draft='你现在记得哪些关于我的事情？';input?.focus()"><span>▤</span><strong>看看我的记忆</strong><small>回顾我们积累的点滴</small></button></div>
            <div class="welcome-note"><span class="status-dot"></span>通用记忆 + 话题记忆，为每次对话补上背景</div>
          </section>
          <div class="message-list" v-else><article v-for="(m,i) in messages" :key="m.id" class="message" :class="m.role"><div class="message-body"><div class="message-label">{{m.role==='user'?'你':'随心记'}}<span v-if="m.role==='assistant'">{{topicNames(m.route).join(' · ') || (m.route.status==='pending'?'待归类':'')}}</span></div><div v-if="m.content" class="markdown" v-html="html(m.content)"></div><div v-else-if="busy && i===messages.length-1" class="thinking"><span></span><span></span><span></span><small>{{phase}}</small></div><p v-else-if="m.status==='error'" class="muted">回复未完成</p><div class="message-meta" v-if="m.role==='assistant'"><span v-if="m.route.memory_count">参考了 {{m.route.memory_count}} 条记忆</span><span v-if="m.status==='interrupted'">已停止</span><button v-if="['error','interrupted'].includes(m.status)&&i===messages.length-1&&!busy" @click="send(messages[i-1])">↻ 重试回复</button></div></div></article></div>
        </div>
        <div class="composer-wrap"><div v-if="route.notice" class="route-notice">{{route.notice}}</div><div class="composer"><textarea ref="input" v-model="draft" rows="2" placeholder="说说你的想法…" aria-label="聊天消息" :disabled="busy" @keydown="keydown"></textarea><div class="composer-bottom"><span>Shift + Enter 换行</span><button v-if="busy" class="stop-button" :disabled="stopping" @click="stop">■ {{stopping?'正在停止':'停止生成'}}</button><button v-else class="send-button" aria-label="发送消息" :disabled="!draft.trim()" @click="send()">↑</button></div></div><p class="composer-footnote">重要的信息会整理为记忆，你随时可以查看和修改。</p></div>
      </template>
      <section v-else class="memory-page"><div class="page-heading"><div><span class="eyebrow">A SPACE FOR WHAT MATTERS</span><h1>我的记忆</h1><p>把重要的事留下，让下次对话更有默契。</p></div><div class="page-actions"><button class="text-button" @click="openTopic()">新建分类</button><button class="primary compact" @click="openMemory()">＋ 添加记忆</button></div></div>
        <div class="memory-summary"><div><strong>{{memoryData.memories.length}}</strong><span>长期记忆</span></div><div><strong>{{memoryData.topics.length}}</strong><span>话题分类</span></div><button @click="filter='pending'"><strong>{{memoryData.pending.length}}</strong><span>待归类 / 待重试 →</span></button></div>
        <div class="memory-layout"><aside class="category-panel"><div class="category-heading">记忆分类<button aria-label="新建分类" @click="openTopic()">＋</button></div><button class="category" :class="{chosen:filter==='global'}" @click="filter='global'"><span>♡ 通用记忆</span><span>{{memoryData.memories.filter(m=>m.scope==='global').length}}</span></button><button class="category" :class="{chosen:filter==='all'}" @click="filter='all'"><span>▤ 全部记忆</span><span>{{memoryData.memories.length}}</span></button><div class="category-separator">话题</div><div v-for="t in memoryData.topics" :key="t.id" class="topic-row"><button class="category" :class="{chosen:filter===t.id}" @click="filter=t.id"><span>{{t.name}}</span><span>{{t.memory_count}}</span></button><div v-if="filter===t.id" class="topic-tools"><button @click="openTopic(t)">编辑</button><button @click="merge(t)">合并</button><button @click="removeTopic(t)">删除</button></div></div><button class="category pending-category" :class="{chosen:filter==='pending'}" @click="filter='pending'"><span>◷ 待归类</span><span>{{memoryData.pending.length}}</span></button></aside>
          <div class="memory-content"><template v-if="filter==='pending'"><div class="content-heading"><div><h2>待归类与失败任务</h2><p>上下文不足的内容先保留，后续对话会帮助归类。</p></div><button class="text-button" @click="retryMemory">↻ 重试</button></div><div v-if="!memoryData.pending.length" class="empty-state"><span>✓</span><h3>暂时没有待归类内容</h3><p>分类不确定时，内容会出现在这里。</p></div><article class="memory-card" v-for="p in memoryData.pending" :key="p.id"><span class="tag">{{p.job_status==='failed'?'整理失败':'等待更多上下文'}}</span><p>{{p.content}}</p><small v-if="p.error">{{p.error}}</small><AppSelect ariaLabel="手动归类" placeholder="手动选择分类…" :options="memoryData.topics.map(t=>({value:t.id,label:t.name}))" @change="assign(p,$event)" /></article></template>
            <template v-else><div class="content-heading"><div><h2>{{filter==='global'?'通用记忆':filter==='all'?'全部记忆':memoryData.topics.find(t=>t.id===filter)?.name}}</h2><p>{{filter==='global'?'个人信息、偏好和习惯，跨话题使用。':memoryData.topics.find(t=>t.id===filter)?.description || '从对话中逐渐积累的重要信息。'}}</p></div></div><input class="search" v-model="search" placeholder="搜索记忆…" aria-label="搜索记忆"><div v-if="!filteredMemories.length" class="empty-state"><span>✳</span><h3>{{search?'没有找到相关记忆':'记忆从对话开始'}}</h3><p>{{search?'换个关键词试试。':'聊聊你的偏好和计划，重要的信息会慢慢出现在这里。'}}</p><button v-if="!search" class="text-button" @click="newChat">开始对话 →</button></div><article class="memory-card" v-for="m in filteredMemories" :key="m.id"><div class="card-heading"><span class="tag">{{m.scope==='global'?'通用':memoryData.topics.find(t=>t.id===m.topic_id)?.name}}</span><div><button aria-label="编辑记忆" @click="openMemory(m)">✎</button><button aria-label="删除记忆" @click="removeMemory(m)">×</button></div></div><h3>{{m.key}}</h3><p>{{m.content}}</p><div class="card-footer"><span>{{date(m.updated_at)}} 更新</span><span>{{m.source_content?'来自聊天':'手动维护'}}</span></div><details v-if="m.source_content"><summary>查看来源</summary><p>{{m.source_content}}</p></details></article></template>
          </div></div>
      </section>
    </main>
    <div v-if="modal" class="modal-backdrop" @click.self="modal=null"><form class="modal" @submit.prevent="save"><div class="modal-heading"><h2>{{editingId?'编辑':'添加'}}{{modal==='topic'?'分类':'记忆'}}</h2><button type="button" aria-label="关闭弹窗" @click="modal=null">×</button></div><template v-if="modal==='topic'"><label>分类名称<input v-model="form.name" required maxlength="40" placeholder="例如：个人学习计划"></label><label>分类说明<textarea v-model="form.description" maxlength="300" placeholder="这个话题包含哪些内容？"></textarea></label></template><template v-else><label>记忆范围<AppSelect v-model="form.scope" ariaLabel="记忆范围" :options="[{value:'global',label:'通用记忆'},{value:'topic',label:'话题记忆'}]" /></label><label v-if="form.scope==='topic'">所属分类<AppSelect v-model="form.topic_id" ariaLabel="所属分类" placeholder="请选择分类" :options="memoryData.topics.map(t=>({value:t.id,label:t.name}))" required /></label><label>记忆名称<input v-model="form.key" required maxlength="100" placeholder="例如：回复语言偏好"></label><label>记忆内容<textarea v-model="form.content" required maxlength="2000" placeholder="你希望被记住的信息"></textarea></label></template><p v-if="error" class="error">{{error}}</p><div class="modal-footer"><button type="button" class="text-button" @click="modal=null">取消</button><button class="primary compact" :disabled="saving">{{saving?'保存中…':'保存'}}</button></div></form></div>
  </div>
</template>
