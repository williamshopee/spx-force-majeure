const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
  appendChild(el){this.children.push(el);return el;}
  replaceChildren(){this.children=[];}
}
const context={document:{createElement:tag=>new Element(tag)},Date,Intl};
vm.createContext(context);
vm.runInContext(fs.readFileSync('stuck_tracker.js','utf8'),context);
const api=context.SPXStuck;
const snapshot={status:'ok',fetched_at:new Date().toISOString(),checked_at:new Date().toISOString(),
 source_url:'https://docs.google.com/spreadsheets/d/example/edit#gid=1',matched:1,rows:{0:{absolute:0,
 inbound_pct:146.41,alive_pct:null,aging_lt16:null,last_status:'<img onerror=alert(1)>',last_status_pct:0}}};
assert.equal(api.createSection(snapshot,0,0),null,'LM excluded');
assert.equal(api.createSection(snapshot,0,1),null,'FM excluded');
assert.equal(api.createSection(snapshot,1,2),null,'unmatched SOC has no made-up report');
const section=api.createSection(snapshot,0,2);
const grid=section.children[2];
assert.equal(grid.children[1].textContent,'0','zero stays zero');
assert.equal(grid.children[3].textContent,'146.41%','preserve inbound values above 100%');
assert.equal(grid.children[5].textContent,'Not available','errors are not zeros');
assert.equal(grid.children[9].textContent,'<img onerror=alert(1)>','sheet content is rendered as plain text');
assert.equal(grid.children[9].children.length,0);
assert.equal(section.children.at(-1).href,snapshot.source_url);
snapshot.status='cached';snapshot.message='Refresh failed';
assert.match(api.createSection(snapshot,0,2).children[1].textContent,/Cached data/);
snapshot.source_url='javascript:alert(1)';
assert.notEqual(api.createSection(snapshot,0,2).children.at(-1).tag,'a');
const host=new Element('section');
api.renderStatus({status:'unconfigured',rows:{},message:'Setup required'},host);
assert.equal(host.hidden,false);
assert.equal(host.children[1].textContent,'Connection setup required');
assert.match(api.stamp('2026-11-10T00:00:00Z'),/07:00 WIB/);
snapshot.status='ok';snapshot.message='Google Sheets checked successfully.';
snapshot.fetched_at=new Date(Date.now()-19*60*60*1000).toISOString();
assert.match(api.createSection(snapshot,0,2).children[1].textContent,/^Fetched/,'19-hour snapshot remains current under the 20-hour cadence');
api.renderStatus(snapshot,host);
assert.equal(host.children[1].textContent,'Google Sheets connected');
assert.ok(host.children.some(el=>/every 20 hours/.test(el.textContent)),'refresh cadence is visible');
snapshot.fetched_at=new Date(Date.now()-22*60*60*1000).toISOString();
const overdue=api.createSection(snapshot,0,2);
assert.match(overdue.children[1].textContent,/^Cached data/);
assert.ok(overdue.children.some(el=>/older than 21 hours/.test(el.textContent)),'overdue notice does not repeat the previous success message');
snapshot.fetched_at=new Date().toISOString();
snapshot.threshold={status:'ok',fetched_at:snapshot.fetched_at,matched:1,unmatched:2,ambiguous:0,rows:{0:{absolute:10,aging_lt16:5},1:{absolute:999},999:{absolute:1000}}};
assert.deepEqual(Array.from(api.thresholdIndices(snapshot,[['Example DC',0,100,2],['Hub',0,100,0]])),[0],'density is limited to mapped SOCs in the authoritative table');
assert.match(api.thresholdSummary(snapshot),/1 mapped SOCs.*2 unmatched/);
assert.ok(api.createSection(snapshot,0,2).children.some(el=>/Listed above threshold/.test(el.textContent)));
snapshot.threshold.rows={};
assert.equal(api.thresholdIndices(snapshot,[['Example DC',0,100,2]]).size,0,'healthy empty list is empty density');
assert.equal(api.thresholdIndices(null,[]).size,0,'unconfigured data never creates a fallback layer');
snapshot.threshold.status='cached';snapshot.threshold.message='Table changed';
assert.match(api.thresholdSummary(snapshot),/Cached/);
console.log('L&D tracker UI: SOC-only rows, zeros, unavailable cells, safe text/links, timestamps and setup status passed.');
