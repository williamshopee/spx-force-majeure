// Exercise the actual list renderer without loading Leaflet or external tiles.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.children = []; this.parts = {}; this.value = ''; }
  set innerHTML(value) { this.children = []; this.html = value; }
  appendChild(child) { this.children.push(child); return child; }
  querySelector(selector) { return this.parts[selector] ||= new Element(); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const els = {eventScope: new Element(), eventSort: new Element()};
els.eventScope.value = 'all'; els.eventSort.value = 'latest';
const context = {
  URL, Date, EVENTS: [], EV: null, HAZF: {}, HAZC: {other: '#fff'},
  fmt: String, selectEvent() {}, drawNewsZones() {}, listEl: new Element(),
  document: {getElementById: id => els[id], createElement: () => new Element(),
             createTextNode: value => ({textContent: value})}
};
vm.createContext(context);
const source = fs.readFileSync('map_template.html', 'utf8');
for (const match of source.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)) {
  if (!match[1].includes('application/json')) new vm.Script(match[2]);
}
const start = source.indexOf('  function reportTime(');
const end = source.indexOf('  function selectEvent(', start);
vm.runInContext(source.slice(start, end), context);
context.EVENTS = [
  {name:'Older mapped event', hazard:'other', _n:50, status:'active', updated:'2026-11-04T10:00:00Z'},
  {name:'Latest BNPB lead', hazard:'other', status:'unverified', published_at:'2026-11-05T11:11:00+07:00', source_url:'https://www.bnpb.go.id/berita/banjir'},
  {name:'Unsafe link', hazard:'other', status:'unverified', discovered_at:'20261105T040000Z', source_url:'javascript:alert(1)'}
];
context.renderList();
assert.equal(context.listEl.children[0].children[0].querySelector('.t').children[0].textContent, 'Latest BNPB lead');
assert.equal(context.listEl.children[0].children[1].href, 'https://www.bnpb.go.id/berita/banjir');
assert.equal(context.listEl.children[1].children.length, 1, 'unsafe source URL must not create a link');
assert.match(context.listEl.children[0].children[0].querySelector('.t').children[1].textContent, /Published .*11:11 WIB/);
els.eventScope.value = 'news'; context.renderList();
assert.equal(context.listEl.children.length, 2);
els.eventScope.value = 'mapped'; context.renderList();
assert.equal(context.listEl.children.length, 1);
els.eventScope.value = 'all'; els.eventSort.value = 'impact'; context.renderList();
assert.equal(context.listEl.children[0].children[0].querySelector('.t').children[0].textContent, 'Older mapped event');
assert.equal(context.reportTime({discovered_at:'20261105T040000Z'}), Date.parse('2026-11-05T04:00:00Z'));
const proximityStart = source.indexOf('  function nearbyNewsFacilities(');
const proximityEnd = source.indexOf('  function drawNewsZones(', proximityStart);
vm.runInContext(source.slice(proximityStart, proximityEnd), context);
const points = [{lat:0,lng:100,screenable:true}, {lat:0,lng:100.01,screenable:true}];
const stations = [['Near station',0,100.02],['Far station',1,101]];
assert.equal(context.nearbyNewsFacilities(points,10,stations).length, 1, 'overlapping zones count a facility once');
assert.equal(context.nearbyNewsFacilities(points,1,stations).length, 0, 'radius changes screening candidates');
assert.equal(context.nearbyNewsFacilities([{lat:0,lng:100,screenable:false}],50,stations).length, 0, 'province markers cannot imply local exposure');
assert.equal(context.nearbyNewsFacilities(points,10,stations)[0].name, 'Near station');
console.log('News UI: date sorting, filters, WIB labels, source links and unsafe URL rejection passed.');
