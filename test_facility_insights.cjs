const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.events={};}
  appendChild(child){this.children.push(child);return child;}
  addEventListener(name,fn){this.events[name]=fn;}
}
const context={URL,Date,Intl,document:{createElement:tag=>new Element(tag)}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('facility_insights.js','utf8'),context);
const api=context.SPXFacilityInsights,now=Date.parse('2026-11-11T00:00:00Z');
const events=[
 {id:'news',status:'unverified',lat:0,lng:100},
 {id:'warning',status:'active',assessment:'official_warning',lat:0,lng:100},
 {id:'stale',status:'stale',lat:0,lng:100},
 {id:'expired',status:'active',lat:0,lng:100,expires:'2026-11-10T00:00:00Z'},
 {id:'invalid',status:'active',lat:null,lng:100},
 {id:'far',status:'active',lat:0,lng:102},
 {id:'near',status:'active',lat:0,lng:100.1,name:'<img onerror=alert(1)>',source:'Official source',source_url:'javascript:alert(1)'}
];
let nearest=api.nearestEvent(events,0,100,[],now);
assert.equal(nearest.event.id,'near');
assert.equal(nearest.index,6,'original event identity is retained despite eligibility filtering');
assert.ok(Math.abs(nearest.distanceKm-11.1195)<0.01);
assert.equal(api.nearestEvent(events,null,100,[],now),null);
assert.equal(api.nearestEvent(events.slice(0,5),0,100,[],now),null);
assert.ok(api.distanceKm(0,179.9,0,-179.9)<23,'date-line crossing takes the short path');
assert.equal(api.distanceKm(0,100,0,100),0);
const closure={id:'closure',status:'active',hazard:'airport_closure',lat:null,lng:null,air:{airports:[{iata:'FAR'},{iata:'NEAR'}]}};
nearest=api.nearestEvent([closure],0,100,[['FAR',1,101],['NEAR',0,100.01]],now);
assert.match(nearest.locationLabel,/NEAR/);
assert.ok(nearest.distanceKm<1.2);
assert.equal(api.nearestEvent([closure],0,100,[],now),null,'closures without mapped airports cannot win');
let selected=null;
const section=api.nearestSection({events,lat:0,lng:100,now,snapshotGeneratedAt:'2026-11-10T00:00:00Z',onSelect:event=>{selected=event.id;}});
assert.equal(section.children[1].textContent,'<img onerror=alert(1)>');
assert.equal(section.children[1].children.length,0,'source names are text, never HTML');
assert.equal(section.children.some(el=>el.tag==='a'),false,'unsafe source URL is excluded');
assert.ok(section.children.some(el=>/overdue/.test(el.textContent)));
section.children.find(el=>el.tag==='button').events.click();assert.equal(selected,'near');
const empty=api.nearestSection({events:[],lat:0,lng:100,loading:true});
assert.match(empty.children[1].textContent,/loading/);
console.log('Facility insights: nearest active confirmed event, airport closures, invalid/expired exclusions, geodesic distance, safe popup and source freshness passed.');
