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
  fmt: String, selectEvent() {}, drawNewsZones() {}, drawWeatherWarnings() {}, listEl: new Element(),
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
const warningStart = source.indexOf('  function isActiveWarning(');
const warningEnd = source.indexOf('  function drawWeatherWarnings(', warningStart);
vm.runInContext(source.slice(warningStart, warningEnd), context);
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
vm.runInContext(source.slice(source.indexOf('  function geometryContains('), proximityStart), context);
const points = [{lat:0,lng:100,screenable:true}, {lat:0,lng:100.01,screenable:true}];
const stations = [['Near station',0,100.02],['Far station',1,101]];
assert.equal(context.nearbyNewsFacilities(points,10,stations).length, 1, 'overlapping zones count a facility once');
assert.equal(context.nearbyNewsFacilities(points,1,stations).length, 0, 'radius changes screening candidates');
assert.equal(context.nearbyNewsFacilities([{lat:0,lng:100,screenable:false}],50,stations).length, 0, 'province markers cannot imply local exposure');
assert.equal(context.nearbyNewsFacilities(points,10,stations)[0].name, 'Near station');
vm.runInContext(fs.readFileSync('turf.min.js','utf8'),context);
const outer=[[100,0],[102,0],[102,2],[100,2],[100,0]];
const hole=[[100.5,.5],[101.5,.5],[101.5,1.5],[100.5,1.5],[100.5,.5]];
const polygon={type:'Polygon',coordinates:[outer,hole]};
assert.equal(context.geometryContains(polygon,1,101),false,'polygon holes exclude facilities');
assert.equal(context.geometryContains({type:'MultiPolygon',coordinates:[[outer,hole]]},.25,100.25),true);
const boundaryPoints=[{lat:.25,lng:100.25,screenable:true,boundary:{year:2019,geometry:polygon}}];
context.prepareNewsAreas(boundaryPoints,'boundary',0);
assert.equal(context.nearbyNewsFacilities(boundaryPoints,1,[['Inside',1.75,101.75]]).length,1,'boundary controls count beyond center radius');
assert.equal(context.nearbyNewsFacilities(boundaryPoints,50,[['Hole',1,101]]).length,0);
assert.equal(context.nearbyNewsFacilities(boundaryPoints,50,[['Outside',2.02,101]]).length,0);
context.prepareNewsAreas(boundaryPoints,'boundary',5);
assert.equal(context.nearbyNewsFacilities(boundaryPoints,1,[['Buffered',2.02,101]]).length,1,'buffer affects the drawn geometry and spatial count');
context.prepareNewsAreas(boundaryPoints,'radius',0);
assert.equal(context.nearbyNewsFacilities(boundaryPoints,1,[['Far',1.75,101.75]]).length,0);
context.EVENTS.push({name:'Active weather warning',hazard:'weather',assessment:'official_warning',status:'active',expires:'2099-01-01T00:00:00Z',updated:'2026-11-06T00:00:00Z'},
                    {name:'Expired warning',assessment:'official_warning',status:'active',expires:'2020-01-01T00:00:00Z'});
els.eventScope.value='warnings'; context.renderList();
assert.equal(context.listEl.children.length,1,'expired warnings disappear from warning list');
els.eventScope.value='mapped'; context.renderList();
assert.equal(context.listEl.children.length,1,'weather warnings do not become confirmed events');
context.EVENTS=Array.from({length:9},(_,i)=>({name:'Update '+i,hazard:'other',status:'active',updated:'2026-11-06T00:00:00Z'}));
els.updateCount=new Element();els.updatesMore=new Element();
els.eventScope.value='all';context.updateLimit=4;context.renderList();
assert.equal(context.listEl.children.length,4,'overview shows a short readable list');
assert.equal(els.updateCount.textContent,'4 of 9');
assert.equal(els.updatesMore.hidden,false);
context.updateLimit+=4;context.renderList();
assert.equal(context.listEl.children.length,8,'more updates keeps earlier rows available');
context.updateLimit+=4;context.renderList();
assert.equal(context.listEl.children.length,9);
assert.equal(els.updatesMore.hidden,true,'pagination ends at the available data');
console.log('News UI: date sorting, filters, WIB labels, source links and unsafe URL rejection passed.');
require('./test_navigation.cjs');
require('./test_stuck_tracker.cjs');
require('./test_facility_insights.cjs');
// Use the actual SOC visibility functions, including facility index zero.
const focusContext={Set,NET_OUT:{0:[1,2,2],2:[3]},NET_IN:{0:[2,4]},
  NET_FOCUS:null,NET_VISIBLE:null,on:[true,true,true],typ:[2,0,2,0,1,2]};
vm.createContext(focusContext);
vm.runInContext(source.slice(source.indexOf('  function directNetworkNodes('),source.indexOf('  function clearNetworkLines(')),focusContext);
assert.deepEqual(Array.from(focusContext.focusableNetwork(0)).sort(),[0,1,2,4],
  'SOC scope deduplicates inbound/outbound neighbors and excludes second-hop facility 3');
focusContext.NET_VISIBLE=focusContext.focusableNetwork(0);focusContext.NET_FOCUS=0;
assert.equal(focusContext.stationVisible(1),true);
assert.equal(focusContext.stationVisible(3),false,'unrelated and transitive facilities are hidden');
focusContext.on[2]=false;
assert.equal(focusContext.stationVisible(0),true,'selected SOC stays visible when its type filter is off');
assert.equal(focusContext.stationVisible(2),false,'other connected facilities respect type filters');
assert.equal(focusContext.focusableNetwork(1),null,'hubs do not replace the selected SOC focus');
assert.equal(focusContext.focusableNetwork(5),null,'SOC without recorded links does not create an empty focus');
focusContext.NET_VISIBLE=null;focusContext.NET_FOCUS=null;focusContext.on[2]=true;
assert.equal(focusContext.stationVisible(3),true,'clearing focus restores unrelated facilities');
focusContext.NET_OUT=null;
assert.equal(focusContext.focusableNetwork(0),null,'General map has no network focus');
console.log('SOC focus: direct-neighbor scope, deduplication, type filters, selected SOC and restoration passed.');
focusContext.GENERAL_VIEW=true;focusContext.mode='heat';focusContext.THRESHOLD_INDICES=new Set([0,2]);
assert.equal(focusContext.renderedStationVisible(0),true);
assert.equal(focusContext.renderedStationVisible(1),false,'General density excludes non-listed facilities');
assert.equal(focusContext.renderedStationVisible(3),false);
focusContext.THRESHOLD_INDICES.clear();
assert.equal(focusContext.renderedStationVisible(0),false,'empty threshold list must never fall back to all facilities');
focusContext.mode='points';assert.equal(focusContext.renderedStationVisible(1),true,'Locations restores the full facility scope');
focusContext.mode='heat';focusContext.GENERAL_VIEW=false;
assert.equal(focusContext.renderedStationVisible(1),true,'SOC account keeps its existing facility density');
console.log('Threshold density: authoritative list membership, empty list and General/SOC mode separation passed.');
