const assert=require('node:assert/strict');
const nav=require('./navigation.js');
const rows=[['Jakarta A',-6.2,106.8,0,1],['Bekasi B',-6.23,107,2,1],['Jakarta A',-6.3,106.9,1,1],['<img src=x onerror=alert(1)>',-7,110,0,1]];
const url=new URL(nav.googleDirections(rows[0],rows[1]));
assert.equal(url.origin,'https://www.google.com');
assert.equal(url.pathname,'/maps/dir/');
assert.equal(url.searchParams.get('origin'),'-6.2,106.8');
assert.equal(url.searchParams.get('destination'),'-6.23,107');
assert.equal(url.searchParams.get('travelmode'),'driving');
assert.equal(url.searchParams.get('api'),'1');
assert.equal(url.searchParams.get('dir_action'),'navigate');
assert.equal(nav.directKm(rows[0],rows[0]),0);
assert.ok(nav.directKm(rows[0],rows[1])>20&&nav.directKm(rows[0],rows[1])<25);
assert.deepEqual(nav.searchStations(' jakarta ',rows).map(r=>r.index),[0,2],'duplicate names retain distinct facility identities');
assert.deepEqual(nav.searchStations('j',rows),[]);
assert.throws(()=>nav.googleDirections(['Missing',null,106],rows[1]));
assert.throws(()=>nav.directKm(['Bad',Infinity,110],rows[1]));

class Element{
  constructor(document){this.doc=document;this.children=[];this.attrs={};this.events={};this.value='';this.hidden=false;this.disabled=false;this.parentElement={contains:()=>false};}
  appendChild(child){this.children.push(child);return child;}
  setAttribute(k,v){this.attrs[k]=String(v);}
  getAttribute(k){return this.attrs[k]??null;}
  removeAttribute(k){delete this.attrs[k];if(k==='href')this.href='';}
  set innerHTML(v){this.children=[];this.html=v;}
  addEventListener(k,fn){(this.events[k]||=[]).push(fn);}
  fire(k,event={}){(this.events[k]||[]).forEach(fn=>fn(Object.assign({preventDefault(){}},event)));}
  focus(){this.doc.activeElement=this;}
  querySelectorAll(selector){return this.children.flatMap(c=>(c.tag===selector?[c]:[]).concat(c.querySelectorAll(selector)));}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
const elements={},document={createElement(tag){const e=new Element(document);e.tag=tag;return e;},getElementById(id){return elements[id]||=(new Element(document));},addEventListener(){}};
const markers=[];let previews=0,activations=0;
const layer={clearLayers(){markers.length=0;},addTo(){return this;}};
const L={layerGroup:()=>layer,marker(coords,opts){const marker={coords,opts,bindPopup(p){this.popup=p;return this;},addTo(){markers.push(this);return this;}};return marker;},divIcon:x=>x,latLngBounds:x=>({pad:()=>x})};
const controller=nav.create({document,stations:rows,L,map:{closePopup(){},fitBounds(){}},activate(){activations++;},onPreview(){previews++;}});
assert.equal(elements.navigationPreview.disabled,true);
controller.select('origin',0);controller.select('destination',1);
assert.equal(activations,2);
assert.equal(elements.navigationPreview.disabled,false);
assert.equal(new URL(elements.navigationGoogle.href).searchParams.get('origin'),'-6.2,106.8');
elements.navigationForm.fire('submit');
assert.equal(markers.length,2);
assert.equal(markers[0].opts.icon.html,'A');
assert.equal(markers[1].opts.icon.html,'B');
assert.equal(elements.navigationResult.hidden,false);
assert.equal(previews,1);
elements.navigationDestination.value='another facility';elements.navigationDestination.fire('input');
assert.equal(elements.navigationGoogle.getAttribute('aria-disabled'),'true','editing a selected name invalidates the old destination');
assert.equal(elements.navigationGoogle.href,'');
assert.equal(elements.navigationResult.hidden,true,'old distance and markers disappear after edits');
assert.equal(markers.length,0);
controller.select('destination',1);elements.navigationSwap.fire('click');
assert.equal(new URL(elements.navigationGoogle.href).searchParams.get('origin'),'-6.23,107');
assert.equal(elements.navigationOrigin.value,'Bekasi B');
controller.select('destination',1);
assert.equal(elements.navigationPreview.disabled,true,'same facility cannot be routed to itself');
controller.select('destination',0);elements.navigationClear.fire('click');
assert.equal(elements.navigationOrigin.value,'');
assert.equal(elements.navigationDestination.value,'');
assert.equal(elements.navigationGoogle.getAttribute('aria-disabled'),'true');
elements.navigationOrigin.value='<img';elements.navigationOrigin.fire('input');
const name=elements.navigationOriginResults.children[0].children[0].children[0];
assert.equal(name.textContent,rows[3][0]);
assert.equal(name.html,undefined,'facility names render as text, not markup');
console.log('Navigation: coordinate URLs, search identities, endpoint previews, swap, edit invalidation and clear passed.');
