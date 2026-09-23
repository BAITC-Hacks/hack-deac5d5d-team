(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('graph-data').textContent);
  const {nodes, edges, top, clusters, meta} = data;
  const $ = id => document.getElementById(id);
  const roles = {
    consolidator: {label:'Консолидатор', color:'#177454', light:'#e7f3ec'},
    transit: {label:'Транзит', color:'#447ca7', light:'#eaf1f8'},
    distributor: {label:'Распределитель', color:'#be7525', light:'#fbf1e2'},
    terminal: {label:'Конечный получатель', color:'#647383', light:'#edf0f4'},
    coordinator: {label:'Координатор', color:'#b85876', light:'#f8ebef'},
    peripheral: {label:'Периферия / неопределён', color:'#7e8a81', light:'#eff2ef'},
  };
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits:2});
  const fmt = value => number.format(value);
  const score = value => value.toFixed(3).replace('.', ',');
  const money = value => fmt(value) + ' ₸';
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // GIDs are opaque strings everywhere, never Number()/parseInt().
  const byId = new Map(nodes.map(node => [node.gid, node]));
  const ranked = [...nodes].sort((a,b) => b.priority_score-a.priority_score || a.gid.localeCompare(b.gid));
  const ranks = new Map(ranked.map((node,index) => [node.gid, index+1]));
  const topIds = new Set(top.map(node => node.gid));
  for (const entry of top) ranks.set(entry.gid, entry.rank);
  const incident = new Map(nodes.map(node => [node.gid, []]));
  for (const edge of edges) {
    incident.get(edge.src).push(edge);
    if (edge.dst !== edge.src) incident.get(edge.dst).push(edge);
  }
  const pairKeys = new Set(edges.map(edge => edge.src + ':' + edge.dst));
  const state = {selected:top[0]?.gid || ranked[0]?.gid || null, query:'', cluster:'all', role:'all', mode:'top', color:'role', page:0, zoom:1};
  const PAGE_SIZE = 10;
  // The shortest unique suffix is a compact label, never the actual identity.
  let suffixLength = 6;
  while (suffixLength < Math.max(...nodes.map(node => node.gid.length)) && new Set(nodes.map(node => node.gid.slice(-suffixLength))).size < nodes.length) suffixLength++;
  const short = gid => (gid.length > suffixLength ? '…' : '') + gid.slice(-suffixLength);

  for (const [key, role] of Object.entries(roles)) {
    const option = document.createElement('option'); option.value = key; option.textContent = role.label; $('roleFilter').append(option);
  }
  for (const cluster of clusters) {
    const option = document.createElement('option'); option.value = String(cluster.cluster_id);
    option.textContent = `Кластер ${cluster.cluster_id} · ${cluster.n_nodes}`; $('clusterFilter').append(option);
  }
  $('totalNodes').textContent = fmt(nodes.length);
  $('totalEdges').textContent = fmt(edges.length);
  $('totalClusters').textContent = fmt(clusters.length);
  $('totalTurnover').textContent = fmt(Math.round(edges.reduce((sum,edge) => sum+edge.sum_kzt,0)/1e4)/100) + ' млн';
  $('period').textContent = meta.period_start ? `${meta.period_start} — ${meta.period_end}` : 'Период не указан';
  $('sourceNote').textContent = meta.analysis_source === 'baseline_rules_v1' ? 'Анализ по базовым правилам.' : 'Загружены расчёты команды.';
  $('listMode').options[0].textContent = `Топ ${top.length}`;
  $('methodNote').textContent = meta.analysis_source === 'baseline_rules_v1'
    ? 'Приоритет: 35% оборот + 25% число связей + 25% достижимость от исходных клиентов + 15% посредничество. Первые три признака нормированы через log(1+x), посредничество — по максимуму. Правила ролей и кластеризации описаны в README интерфейса.'
    : 'Роли, оценки, кластеры и топ взяты из проверенных CSV команды. Наблюдаемые суммы и число связей рассчитаны заново из исходных данных.';

  function filtered() {
    const source = state.query || state.mode === 'all' ? ranked : top.map(entry => byId.get(entry.gid));
    return source.filter(node => (state.query || state.mode === 'all' || topIds.has(node.gid))
      && (state.cluster === 'all' || String(node.cluster_id) === state.cluster)
      && (state.role === 'all' || node.role === state.role)
      && node.gid.includes(state.query));
  }

  function renderList() {
    const visible = filtered();
    $('listTitle').textContent = state.query ? 'Результаты поиска' : 'Приоритетные клиенты';
    $('resultCount').textContent = visible.length;
    $('listFooter').textContent = state.query ? `Поиск среди всех ${nodes.length} клиентов. Полный gid снимает фильтры.`
      : `По убыванию приоритета. Показано ${visible.length}; поиск доступен по всем ${nodes.length} клиентам.`;
    $('priorityList').innerHTML = visible.length ? visible.map(node => {
      const role = roles[node.role];
      return `<button type="button" class="node-row ${node.gid === state.selected ? 'selected' : ''}" data-gid="${esc(node.gid)}" aria-pressed="${node.gid === state.selected}" aria-label="Клиент ${esc(node.gid)}, ${role.label}, приоритет ${score(node.priority_score)}">
        <span class="rank mono">${ranks.get(node.gid)}</span><span class="row-center"><span class="row-id mono" style="display:block">${esc(node.gid)}</span>
        <span class="role-line"><i class="role-dot" style="background:${role.color}"></i>${role.label}</span></span><span class="row-score mono">${score(node.priority_score)}<span class="score-track"><i style="width:${node.priority_score*100}%"></i></span></span></button>`;
    }).join('') : '<div class="empty-list">Клиенты не найдены.<br>Измените поиск или сбросьте фильтры.</div>';
  }

  function allNeighbors(gid) {
    const sums = new Map();
    for (const edge of incident.get(gid) || []) {
      const other = edge.src === gid ? edge.dst : edge.src;
      if (other !== gid) sums.set(other, (sums.get(other) || 0) + edge.sum_kzt);
    }
    return [...sums].sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0])).map(([id]) => id);
  }

  function renderInspector() {
    const node = byId.get(state.selected);
    if (!node) { $('inspector').innerHTML = '<div class="empty-inspector">Выберите клиента в списке или измените фильтры.</div>'; return; }
    const role = roles[node.role];
    let limit = 'Показаны внутрибанковские переводы от 5 000 ₸ в пределах периода и четырёх колен. Полный баланс и внешние поступления неизвестны.';
    if (node.truncated_by_depth) limit = 'Достигнуто 4-е колено. Дальнейшие переводы не представлены. Отсутствие исходящих не доказывает, что деньги остались у клиента.';
    else if (node.in_deg + node.out_deg === 0) limit = 'В этой выгрузке связи отсутствуют. По этому набору нельзя оценить поведение клиента.';
    const scoreBlock = (title, value) => `<div><div class="metric-label">${title}</div><div class="metric-value">${score(value)}<span class="metric-max">/ 1</span></div><div class="metric-bar"><i style="width:${value*100}%"></i></div></div>`;
    const neighbors = allNeighbors(node.gid);
    $('inspector').innerHTML = `<div class="inspector-body"><div class="client-block"><div class="client-id mono">${esc(node.gid)}</div><div class="client-meta">Кластер ${node.cluster_id} · Колено ${node.depth}${node.is_seed ? ' · Исходный клиент' : ''}</div>
      <div class="role-pill" style="color:${role.color};background:${role.light}"><i class="role-dot" style="background:${role.color}"></i>${role.label}</div><div class="score-grid">${scoreBlock('Приоритет проверки',node.priority_score)}${scoreBlock('Оценка роли',node.role_score)}</div></div>
      <div class="analysis-block"><div class="evidence-box"><div class="evidence-title">Основание роли</div><p class="evidence-text">${esc(node.evidence)}</p></div>
      <div class="evidence-box priority-explanation"><div class="evidence-title">Почему такой приоритет</div><p class="evidence-text">${esc(node.priority_reason)}</p></div><div class="score-note">Гипотезы требуют проверки аналитиком. Оценки не являются вероятностями нарушения.</div></div>
      <div class="flow-block"><div class="detail-divider"></div><div class="section-title">НАБЛЮДАЕМЫЕ ПОТОКИ</div><div class="flow-grid"><div><div class="flow-title">↙ Входящие</div><div class="flow-value">${money(node.in_kzt)}</div><div class="flow-sub">Отправителей: ${node.in_deg}<br>Операций: ${node.in_tx}</div></div><div><div class="flow-title">↗ Исходящие</div><div class="flow-value">${money(node.out_kzt)}</div><div class="flow-sub">Получателей: ${node.out_deg}<br>Операций: ${node.out_tx}</div></div></div></div>
      <div class="limits-block"><div class="limits"><div class="limits-title">Ограничения данных</div><p>${limit}</p></div><div class="selected-neighbors"><div class="section-title">КОНТРАГЕНТЫ · ${neighbors.length}</div><div class="neighbor-buttons">${neighbors.map(gid => `<button type="button" data-gid="${esc(gid)}" title="${esc(gid)}">${esc(short(gid))}</button>`).join('') || '<span class="source-note">Связи отсутствуют</span>'}</div></div></div></div>`;
  }

  function svgEl(tag, attrs = {}, text) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key,value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function graphPage() {
    if (!state.selected) return {ns:[], es:[], neighbors:[], pages:1};
    const neighbors = allNeighbors(state.selected);
    const pages = Math.max(1, Math.ceil(neighbors.length / PAGE_SIZE));
    state.page = Math.max(0, Math.min(state.page, pages-1));
    const ids = new Set([state.selected, ...neighbors.slice(state.page*PAGE_SIZE, (state.page+1)*PAGE_SIZE)]);
    return {ns:[...ids].map(gid => byId.get(gid)), es:edges.filter(edge => ids.has(edge.src) && ids.has(edge.dst)), neighbors, pages};
  }

  function nodeColor(node) {
    return state.color === 'role' ? roles[node.role] : {
      color:`hsl(${(node.cluster_id*137.508)%360} 48% 38%)`, light:`hsl(${(node.cluster_id*137.508)%360} 40% 94%)`, label:`Кластер ${node.cluster_id}`,
    };
  }

  function renderGraph() {
    const svg = $('network'), surface = $('graphSurface');
    const width = Math.max(surface.clientWidth, 280), height = Math.max(surface.clientHeight, 620);
    svg.replaceChildren(); svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const {ns,es,neighbors,pages} = graphPage();
    $('graphEmpty').hidden = ns.length > 0;
    $('previousPage').disabled = state.page === 0 || !ns.length;
    $('nextPage').disabled = state.page+1 >= pages || !ns.length;
    $('pageStatus').textContent = ns.length ? `Страница ${state.page+1} из ${pages}` : 'Нет результата';
    $('graphContext').textContent = ns.length ? `${ns.length-1} из ${neighbors.length} контрагентов · ${es.length} связей на экране` : '';
    $('graphHint').textContent = !ns.length ? '' : !neighbors.length ? 'Клиент присутствует в данных, но контрагенты не представлены' : 'Нажмите на узел, чтобы перейти к его связям';
    const relevant = es.filter(edge => edge.src === state.selected || edge.dst === state.selected);
    $('edgeTable').innerHTML = relevant.length ? relevant.map(edge => {
      const incoming = edge.dst === state.selected, other = incoming ? edge.src : edge.dst;
      return `<tr><td>${incoming ? '← Вход' : '→ Выход'}<br><button type="button" data-gid="${esc(other)}">${esc(other)}</button></td><td>${edge.n_tx}</td><td>${fmt(edge.sum_kzt)}</td></tr>`;
    }).join('') : '<tr><td colspan="3">На этой странице нет переводов выбранного клиента.</td></tr>';
    const legend = state.color === 'role' ? Object.values(roles) : [...new Set(ns.map(node => node.cluster_id))].map(cluster => nodeColor({cluster_id:cluster}));
    $('legend').innerHTML = legend.map(item => `<span><i style="background:${item.color}"></i>${item.label}</span>`).join('');
    if (!ns.length) return;
    const positions = new Map([[state.selected,{x:width/2,y:height/2}]]);
    const incoming = ns.filter(node => node.gid !== state.selected && pairKeys.has(node.gid+':'+state.selected));
    const outgoing = ns.filter(node => node.gid !== state.selected && !incoming.includes(node));
    for (const [group,x] of [[incoming,width*.18],[outgoing,width*.82]]) {
      group.forEach((node,index) => positions.set(node.gid, {x,y:group.length === 1 ? height/2 : 90+index*(height-200)/(group.length-1)}));
    }
    const defs = svgEl('defs');
    for (const [kind,color] of [['incoming','#69a28a'],['outgoing','#7299b4'],['other','#bac8be']]) {
      const marker = svgEl('marker',{id:'arrow-'+kind,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto'});
      marker.append(svgEl('path',{d:'M1 1 L9 5 L1 9 Z',fill:color})); defs.append(marker);
    }
    svg.append(defs);
    const layer = svgEl('g',{transform:`translate(${width/2} ${height/2}) scale(${state.zoom}) translate(${-width/2} ${-height/2})`}); svg.append(layer);
    for (const edge of es) {
      const src = positions.get(edge.src), dst = positions.get(edge.dst);
      const dx=dst.x-src.x, dy=dst.y-src.y, length=Math.hypot(dx,dy)||1;
      const sr=edge.src===state.selected?28:17, dr=edge.dst===state.selected?33:21;
      const x1=src.x+dx/length*sr, y1=src.y+dy/length*sr, x2=dst.x-dx/length*dr, y2=dst.y-dy/length*dr;
      const bend=pairKeys.has(edge.dst+':'+edge.src)?18:0;
      const kind=edge.dst===state.selected?'incoming':edge.src===state.selected?'outgoing':'other';
      const pathData=edge.src===edge.dst?`M${src.x-10},${src.y-12} C${src.x-50},${src.y-70} ${src.x+50},${src.y-70} ${src.x+10},${src.y-12}`
        :`M${x1},${y1} Q${(x1+x2)/2-dy/length*bend},${(y1+y2)/2+dx/length*bend} ${x2},${y2}`;
      const line=svgEl('path',{d:pathData,fill:'none',stroke:kind==='incoming'?'#69a28a':kind==='outgoing'?'#7299b4':'#c9d5cc','stroke-width':kind==='other'?1:1.8,'marker-end':`url(#arrow-${kind})`,'data-src':edge.src,'data-dst':edge.dst});
      line.append(svgEl('title',{},`${edge.src} → ${edge.dst}: ${money(edge.sum_kzt)}, операций ${edge.n_tx}`)); layer.append(line);
      const hit=svgEl('path',{d:pathData,fill:'none',stroke:'transparent','stroke-width':12});
      hit.addEventListener('pointermove',event=>showTooltip(event,`${esc(edge.src)} → ${esc(edge.dst)}<br>${money(edge.sum_kzt)} · операций ${edge.n_tx}`));
      hit.addEventListener('pointerleave',hideTooltip); layer.append(hit);
    }
    for (const node of ns) {
      const pos=positions.get(node.gid), active=node.gid===state.selected, color=nodeColor(node), role=roles[node.role];
      const group=svgEl('g',{class:'graph-node',transform:`translate(${pos.x} ${pos.y})`,role:'button',tabindex:0,'aria-label':`Клиент ${node.gid}, ${role.label}`,'data-gid':node.gid});
      group.append(svgEl('title',{},`${node.gid} · ${role.label} · кластер ${node.cluster_id}`));
      if(active) group.append(svgEl('circle',{r:36,fill:color.light}));
      group.append(svgEl('circle',{r:active?26:15,class:'node-disc',fill:active?color.color:color.light,stroke:color.color,'stroke-width':1.5}));
      group.append(svgEl('circle',{r:active?5:3,fill:active?'white':color.color}));
      if(node.is_seed) { group.append(svgEl('circle',{cx:12,cy:-13,r:7,fill:'#253f32'})); group.append(svgEl('text',{x:12,y:-10,'text-anchor':'middle',fill:'white',style:'font-size:9px'},'S')); }
      if(node.truncated_by_depth) group.append(svgEl('circle',{r:active?31:20,fill:'none',stroke:'#b69044','stroke-dasharray':'3 3'}));
      const labelY=active?50:33;
      group.append(svgEl('text',{y:labelY,'text-anchor':'middle',fill:'#365843',style:`font-size:${width<400?'9':'10'}px`},short(node.gid)));
      group.addEventListener('click',()=>selectNode(node.gid));
      group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectNode(node.gid);}});
      group.addEventListener('pointermove',event=>showTooltip(event,`${esc(node.gid)}<br>${role.label} · кластер ${node.cluster_id}`)); group.addEventListener('pointerleave',hideTooltip);
      layer.append(group);
    }
  }

  function showTooltip(event, html) {
    const box=$('graphSurface').getBoundingClientRect(), tip=$('graphTooltip'); tip.innerHTML=html; tip.hidden=false;
    tip.style.left=Math.max(8,Math.min(event.clientX-box.left+12,box.width-tip.offsetWidth-8))+'px';
    tip.style.top=Math.max(8,Math.min(event.clientY-box.top+12,box.height-tip.offsetHeight-8))+'px';
  }
  function hideTooltip() { $('graphTooltip').hidden=true; }
  function render() { renderList(); renderInspector(); hideTooltip(); renderGraph(); $('announcer').textContent=state.selected?`Выбран клиент ${state.selected}.`:'Клиенты не найдены.'; }
  function filterChanged() {
    const visible=filtered(); if(!visible.some(node=>node.gid===state.selected)) state.selected=visible[0]?.gid || null;
    state.page=0; state.zoom=1; render();
  }
  function selectNode(gid) {
    if(!byId.has(gid)) return;
    state.selected=gid; state.page=0; state.zoom=1;
    state.query=''; state.cluster='all'; state.role='all';
    if(!topIds.has(gid)) state.mode='all';
    syncInputs(); render();
  }
  function syncInputs() {
    $('searchInput').value=state.query; $('clearSearch').hidden=!state.query;
    $('clusterFilter').value=state.cluster; $('roleFilter').value=state.role; $('listMode').value=state.mode; $('colorMode').value=state.color;
  }
  function reset() { Object.assign(state,{selected:top[0]?.gid || ranked[0]?.gid || null,query:'',cluster:'all',role:'all',mode:'top',color:'role',page:0,zoom:1}); syncInputs(); render(); }
  $('searchInput').addEventListener('input',event=>{
    state.query=event.target.value.trim();
    if(byId.has(state.query)){state.cluster='all';state.role='all';state.selected=state.query;$('clusterFilter').value='all';$('roleFilter').value='all';}
    $('clearSearch').hidden=!state.query; filterChanged();
  });
  $('searchInput').addEventListener('keydown',event=>{if(event.key==='Escape'){$('clearSearch').click();}});
  $('clearSearch').addEventListener('click',()=>{state.query='';syncInputs();filterChanged();$('searchInput').focus();});
  for(const [id,key] of [['clusterFilter','cluster'],['roleFilter','role'],['listMode','mode']]) $(id).addEventListener('change',event=>{state[key]=event.target.value;filterChanged();});
  $('resetButton').addEventListener('click',reset);
  $('colorMode').addEventListener('change',event=>{state.color=event.target.value;renderGraph();});
  for(const id of ['priorityList','inspector','edgeTable']) $(id).addEventListener('click',event=>{const target=event.target.closest('[data-gid]');if(target)selectNode(target.dataset.gid);});
  $('previousPage').addEventListener('click',()=>{state.page--;hideTooltip();renderGraph();});
  $('nextPage').addEventListener('click',()=>{state.page++;hideTooltip();renderGraph();});
  $('zoomIn').addEventListener('click',()=>{state.zoom=Math.min(1.6,state.zoom+.15);renderGraph();});
  $('zoomOut').addEventListener('click',()=>{state.zoom=Math.max(.65,state.zoom-.15);renderGraph();});
  $('zoomReset').addEventListener('click',()=>{state.zoom=1;renderGraph();});
  const scenarios={priority:top[0]?.gid,boundary:ranked.find(node=>node.truncated_by_depth)?.gid,isolated:ranked.find(node=>node.in_deg+node.out_deg===0)?.gid};
  document.querySelectorAll('[data-scenario]').forEach(button=>{button.disabled=!scenarios[button.dataset.scenario];button.addEventListener('click',()=>{reset();selectNode(scenarios[button.dataset.scenario]);});});
  $('aboutButton').addEventListener('click',()=>$('aboutDialog').showModal());
  for(const id of ['closeAbout','closeAboutPrimary']) $(id).addEventListener('click',()=>$('aboutDialog').close());
  $('downloadTop').addEventListener('click',()=>{
    const headers=['rank','gid','role','priority_score','why'];
    const csvCell=value=>'"'+String(value).replace(/"/g,'""')+'"';
    const csv=headers.join(',')+'\n'+top.map(row=>headers.map(key=>csvCell(row[key])).join(',')).join('\n');
    const url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='top_nodes.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  if(window.ResizeObserver) new ResizeObserver(renderGraph).observe($('graphSurface'));
  else window.addEventListener('resize',renderGraph);
  // Read-only snapshots plus the same selection action used by the visible UI.
  window.MoneyGraph={getData:()=>JSON.parse(JSON.stringify(data)),getState:()=>({...state}),selectNode,reset};
  render();
})();
