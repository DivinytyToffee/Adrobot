const el = id => document.getElementById(id);
const form = el('createForm');
let options, activeCampaign, loadId = 0;
const notify = text => { el('msg').textContent = text; };
async function api(path, method='GET', data, headers={}) {
    const r = await fetch('/api/' + path, {method, headers:{'Content-Type':'application/json',...headers}, body:JSON.stringify(data)});
    const value = await r.json();
    if (!r.ok) throw Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
    return value;
}
function action(label, run) {
    const b = document.createElement('button');
    b.textContent = label;
    b.onclick = () => run().catch(e=>notify(e.message));
    return b;
}
function choices(select, rows) {
    select.replaceChildren(...rows.map(r => new Option(r.name + ' · ' + r.id,r.id)));
}
function show(editor) {
    el('editor').hidden = !editor;
    el('creator').hidden = editor;
    notify('');
}
el('tabEditor').onclick = ()=>show(true);
el('tabCreate').onclick = ()=>show(false);
async function list(selected) {
    choices(el('campaign'),await api('campaigns'));
    if (selected) el('campaign').value = selected;
    await load();
}
async function load() {
    const ticket = ++loadId;
    activeCampaign = el('campaign').value;
    el('flows').replaceChildren();
    if (!activeCampaign) return;
    const rows = await api('campaigns/'+activeCampaign+'/streams');
    if (ticket!==loadId) return;
    for (const row of rows) renderFlow(row,activeCampaign);
}
function renderFlow(flow,cid) {
    const box=document.createElement('article');
    el('flows').append(box);
    let rows=flow.offers, pins={...flow.pinned}, dirty=false, reveal=false;
    const path='campaigns/'+cid+'/streams/'+flow.id;
    const ids=()=>rows.filter(r=>r.state!=='disabled').map(r=>Number(r.offer_id));
    async function change(next, locked, save=false) {
        box.inert=true;
        try {
            const result=await api(path+(save?'/offers':'/preview'),save?'PUT':'POST',{revision:flow.revision,active_ids:next,pinned:locked});
            rows=result.offers;
            pins=locked;
            dirty=!save;
            if(save) {flow={...flow,...result,pinned:{...locked}};notify('Изменения сохранены.');}
            else notify('Черновик. Нажмите «Сохранить», чтобы применить изменения.');
            draw();
        } finally {box.inert=false;}
    }
    function draw() {
        box.replaceChildren();
        const title=document.createElement('h2');title.textContent=flow.position+'. '+flow.name;box.append(title);
        if(!flow.editable) return;
        const active=ids();
        for(const r of rows) {
            const id=Number(r.offer_id), on=active.includes(id);
            if(!on && !reveal) continue;
            const line=document.createElement('div');line.className='row'+(on?'':' inactive');
            const label=document.createElement('span');label.textContent=options.offers.find(o=>o.id===id)?.name || '#'+id;
            line.append(label);
            if(on){
                const share=document.createElement('input');share.type='number';share.min=0;share.max=100;share.value=r.share;share.setAttribute('aria-label','Доля, %');
                share.onchange=()=>change(active,{...pins,[id]:Number(share.value)}).catch(e=>{notify(e.message);draw();});
                const fixed=document.createElement('label'), cb=document.createElement('input');cb.type='checkbox';cb.checked=id in pins;fixed.append(cb,'Фикс.');
                cb.onchange=()=>{const p={...pins};if(cb.checked)p[id]=r.share;else delete p[id];change(active,p).catch(e=>{notify(e.message);draw();});};
                const b=action('Убрать',async()=>{const p={...pins};delete p[id];await change(active.filter(i=>i!==id),p);});b.disabled=active.length===1;
                line.append(share,fixed,b);
            }else{line.append('0 %',action('Вернуть',()=>change([...active,id],pins)));}
            box.append(line);
        }
        const bar=document.createElement('div');bar.className='actions';
        const select=document.createElement('select');select.setAttribute('aria-label','Добавляемый оффер');
        choices(select,options.offers.filter(o=>!active.includes(o.id)));
        const add=action('Добавить',()=>change([...active,Number(select.value)],pins));add.disabled=!select.length;
        bar.append(select,add);
        if(rows.some(r=>r.state==='disabled'))bar.append(action(reveal?'Скрыть снятые':'Показать снятые',async()=>{reveal=!reveal;draw();}));
        if(dirty)bar.append(action('Сохранить',()=>change(active,pins,true)),action('Отменить',async()=>{rows=flow.offers;pins={...flow.pinned};dirty=false;draw();notify('Черновик отменён.');}));
        box.append(bar);
    }
    draw();
}
el('refresh').onclick=el('campaign').onchange=()=>load().catch(e=>notify(e.message));
form.onsubmit=async event=>{
    event.preventDefault();
    const btn=form.querySelector('button');btn.disabled=true;
    try{
        const data=Object.fromEntries(new FormData(form));
        const query=data.country_code.trim().toLowerCase();
        const country=options.countries.find(c=>[c.code,c.name].some(v=>v.toLowerCase()===query));
        if(!country)throw Error('Выберите страну из списка.');
        data.country_code=country.code;
        for(const key of ['offer_id','domain_id','group_id','traffic_source_id'])data[key]=Number(data[key]);
        const body=JSON.stringify(data);
        let pending=JSON.parse(sessionStorage.getItem('adrobot.pending')||'null');
        if(!pending||pending.body!==body){pending={key:crypto.randomUUID(),body};sessionStorage.setItem('adrobot.pending',JSON.stringify(pending));}
        const result=await api('campaigns','POST',data,{'Idempotency-Key':pending.key});
        sessionStorage.removeItem('adrobot.pending');form.elements.name.value='';
        show(true);await list(result.keitaro_campaign_id);notify('Создана кампания №'+result.keitaro_campaign_id);
    }catch(e){notify(e.message);}finally{btn.disabled=false;}
};
async function start(){
    options=await api('campaign-options');
    el('mode').textContent=options.mode==='demo'?'Демо · локальные данные':'Keitaro';
    for(const [field,key] of Object.entries({domain_id:'domains',group_id:'groups',traffic_source_id:'traffic_sources',offer_id:'offers'})) {
        const select = form.elements[field];
        choices(select, options[key]);
        if (options[key].some(row => row.id === options.defaults[field])) select.value = options.defaults[field];
    }
    el('countries').replaceChildren(...options.countries.map(c=>new Option(c.name,c.code)));
    await list();
}
start().catch(e=>notify(e.message));
