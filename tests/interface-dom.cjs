// npm install --no-save jsdom@30.1.1 ; node tests/interface-dom.cjs out/index.html
// DOM behavior tests; these do not validate actual CSS rendering.
const {JSDOM, VirtualConsole} = require('jsdom');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2] || 'out/index.html', 'utf8');
const errors = []; let downloads = 0; let width = 650;
const vc = new VirtualConsole(); vc.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(html, {runScripts:'dangerously', pretendToBeVisual:true, virtualConsole:vc,
  beforeParse(window) {
    window.ResizeObserver = class { constructor(cb) {this.cb=cb;} observe() {this.cb();} };
    Object.defineProperty(window.HTMLElement.prototype, 'clientWidth', {get(){return this.id==='graphSurface'?width:300;}});
    Object.defineProperty(window.HTMLElement.prototype, 'clientHeight', {get(){return 620;}});
    window.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
    window.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
    window.URL.createObjectURL=()=> 'blob:test'; window.URL.revokeObjectURL=()=>{};
    window.HTMLAnchorElement.prototype.click=()=>{downloads++;};
  },
});
const w=dom.window, d=w.document, $=id=>d.getElementById(id);
assert.deepEqual(errors, []);
const api=w.MoneyGraph, data=JSON.parse(JSON.stringify(api.getData()));
const click=selector=>{const target=d.querySelector(selector);assert(target,selector);target.click();};
const search=value=>{$('searchInput').value=value;$('searchInput').dispatchEvent(new w.Event('input',{bubbles:true}));};
const change=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('change',{bubbles:true}));};
assert(data.top.length>=20); assert.equal(d.querySelectorAll('.node-row').length,data.top.length);
assert.deepEqual([...d.querySelectorAll('.node-row')].map(el=>el.dataset.gid),data.top.map(node=>node.gid));
assert.equal(d.querySelector('.client-id').textContent,data.top[0].gid);
assert(data.nodes.every(node=>typeof node.gid==='string'));
assert(data.edges.every(edge=>typeof edge.src==='string'&&typeof edge.dst==='string'));

// Search must find clients outside top, including isolates, with all digits intact.
const outside=data.nodes.find(node=>!data.top.some(top=>top.gid===node.gid)&&node.in_deg+node.out_deg>0);
const isolated=data.nodes.find(node=>node.in_deg+node.out_deg===0);
const boundary=data.nodes.find(node=>node.truncated_by_depth);
const heavy=data.nodes.reduce((a,b)=>a.in_deg+a.out_deg>b.in_deg+b.out_deg?a:b);
for(const node of [outside, isolated, boundary, heavy]) {
  change('clusterFilter',String(data.clusters.find(c=>c.cluster_id!==node.cluster_id).cluster_id));
  search(node.gid);
  assert.equal(d.querySelectorAll('.node-row').length,1);
  assert.equal(d.querySelector('.client-id').textContent,node.gid);
  assert.equal($('clusterFilter').value,'all');
}
search('999999999999999999');assert.equal(d.querySelectorAll('.node-row').length,0);assert(!$('graphEmpty').hidden);
click('#resetButton');search(isolated.gid);assert.equal(d.querySelectorAll('.graph-node').length,1);assert(d.querySelector('.limits').textContent.includes('связи отсутствуют'));
search(boundary.gid);assert(d.querySelector('.limits').textContent.includes('4-е колено'));

// Every direct counterparty and edge is reachable through pages; no hidden cap.
search(heavy.gid);
const seen=new Set(), seenEdges=new Set(); let pageCount=0;
do {
  for(const el of d.querySelectorAll('.graph-node')) seen.add(el.dataset.gid);
  for(const el of d.querySelectorAll('path[data-src]')) seenEdges.add(el.dataset.src+':'+el.dataset.dst);
  assert(d.querySelectorAll('.graph-node').length<=11);
  pageCount++; if($('nextPage').disabled) break; click('#nextPage');
} while(pageCount<500);
const expected=new Set([heavy.gid]);
for(const edge of data.edges) if(edge.src===heavy.gid||edge.dst===heavy.gid) {
  expected.add(edge.src); expected.add(edge.dst); assert(seenEdges.has(edge.src+':'+edge.dst));
}
assert.deepEqual(seen,expected);assert(pageCount>1);
click('#previousPage');assert.equal(api.getState().page,pageCount-2);
click('#resetButton');change('listMode','all');change('clusterFilter',String(outside.cluster_id));
assert([...d.querySelectorAll('.node-row')].every(el=>data.nodes.find(node=>node.gid===el.dataset.gid).cluster_id===outside.cluster_id));
change('roleFilter',outside.role);
assert([...d.querySelectorAll('.node-row')].every(el=>data.nodes.find(node=>node.gid===el.dataset.gid).role===outside.role));

api.selectNode(heavy.gid);const other=d.querySelector(`.graph-node:not([data-gid="${heavy.gid}"])`);const otherId=other.dataset.gid;
other.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));assert.equal(d.querySelector('.client-id').textContent,otherId);
change('colorMode','cluster');assert($('legend').textContent.includes('Кластер'));
for(const size of [650,400,294]) {
  width=size;api.selectNode(heavy.gid);
  for(const el of d.querySelectorAll('.graph-node')) {
    const [x,y]=el.getAttribute('transform').match(/[\d.]+/g).map(Number);assert(x>=0&&x<=width);assert(y>=0&&y<=620);
  }
}
click('#aboutButton');assert($('aboutDialog').open);click('#downloadTop');assert.equal(downloads,1);click('#closeAboutPrimary');assert(!$('aboutDialog').open);
assert.equal(d.querySelectorAll('script[src],link[rel="stylesheet"]').length,0);assert.deepEqual(errors,[]);
console.log(`PASS: top ${data.top.length}, exact GID search, filters, all ${seen.size-1} neighbors across ${pageCount} pages, directions, keyboard, download; no JS errors.`);
w.close();
