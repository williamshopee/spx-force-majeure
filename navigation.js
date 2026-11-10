/* Facility-to-facility navigation. No routing API keys belong in this file. */
(function(root){
  'use strict';
  function validStation(row){
    return Array.isArray(row) && typeof row[0]==='string' && Number.isFinite(row[1]) &&
      Number.isFinite(row[2]) && row[1]>=-90 && row[1]<=90 && row[2]>=-180 && row[2]<=180;
  }
  function googleDirections(origin,destination){
    if(!validStation(origin)||!validStation(destination))throw new Error('Choose two valid facilities.');
    var url=new URL('https://www.google.com/maps/dir/');
    url.search=new URLSearchParams({api:'1',origin:origin[1]+','+origin[2],
      destination:destination[1]+','+destination[2],travelmode:'driving',dir_action:'navigate'}).toString();
    return url.href;
  }
  function directKm(a,b){
    if(!validStation(a)||!validStation(b))throw new Error('Invalid facility coordinates.');
    var rad=Math.PI/180,deltaLat=(b[1]-a[1])*rad,deltaLng=(b[2]-a[2])*rad;
    var value=Math.sin(deltaLat/2)**2+Math.cos(a[1]*rad)*Math.cos(b[1]*rad)*Math.sin(deltaLng/2)**2;
    return 12742.0176*Math.asin(Math.min(1,Math.sqrt(value)));
  }
  function searchStations(query,rows,limit){
    var normalized=String(query||'').trim().toLocaleLowerCase('id');
    if(normalized.length<2)return [];
    return rows.map(function(row,index){return {row:row,index:index};}).filter(function(item){
      return validStation(item.row)&&item.row[0].toLocaleLowerCase('id').includes(normalized);
    }).sort(function(a,b){
      return Number(!a.row[0].toLocaleLowerCase('id').startsWith(normalized))-
        Number(!b.row[0].toLocaleLowerCase('id').startsWith(normalized)) || a.row[0].localeCompare(b.row[0]);
    }).slice(0,limit||8);
  }
  function create(options){
    var doc=options.document||document,rows=options.stations,L=options.L,map=options.map;
    var selected={origin:null,destination:null},layers=L.layerGroup().addTo(map);
    var form=doc.getElementById('navigationForm'),status=doc.getElementById('navigationStatus');
    var result=doc.getElementById('navigationResult'),preview=doc.getElementById('navigationPreview');
    var directions=doc.getElementById('navigationGoogle');
    var fields={};
    function announce(message){status.textContent=message;}
    function hasPair(){return selected.origin!==null&&selected.destination!==null;}
    function clearPreview(){
      layers.clearLayers();result.hidden=true;directions.removeAttribute('href');
      directions.setAttribute('aria-disabled','true');preview.disabled=true;
    }
    function closeResults(kind){
      var field=fields[kind];field.list.hidden=true;field.input.setAttribute('aria-expanded','false');
    }
    function refresh(){
      clearPreview();
      if(!hasPair()){announce('Choose a start and destination facility.');return;}
      if(selected.origin===selected.destination){announce('Choose a different destination facility.');return;}
      var a=rows[selected.origin],b=rows[selected.destination];
      if(directKm(a,b)<0.01){announce('These facilities share the same location. Check their entrance coordinates in Google Maps.');}
      else announce('Ready. Open Google Maps for road distance and driving time.');
      directions.href=googleDirections(a,b);directions.setAttribute('aria-disabled','false');preview.disabled=false;
    }
    function select(kind,index){
      if(!Number.isInteger(index)||!validStation(rows[index]))return;
      selected[kind]=index;fields[kind].input.value=rows[index][0];
      fields[kind].hint.textContent=rows[index][1].toFixed(5)+', '+rows[index][2].toFixed(5);
      closeResults(kind);refresh();
    }
    ['origin','destination'].forEach(function(kind){
      var prefix=kind==='origin'?'navigationOrigin':'navigationDestination';
      fields[kind]={input:doc.getElementById(prefix),list:doc.getElementById(prefix+'Results'),hint:doc.getElementById(prefix+'Hint')};
      var field=fields[kind];
      field.input.addEventListener('input',function(){
        selected[kind]=null;field.hint.textContent='Select an exact facility from the suggestions.';refresh();
        var matches=searchStations(field.input.value,rows);
        field.list.innerHTML='';
        if(field.input.value.trim().length<2){closeResults(kind);return;}
        matches.forEach(function(item){
          var li=doc.createElement('li'),button=doc.createElement('button');button.type='button';button.setAttribute('role','option');
          var name=doc.createElement('span');name.textContent=item.row[0];button.appendChild(name);
          var detail=doc.createElement('small');detail.textContent=item.row[1].toFixed(5)+', '+item.row[2].toFixed(5);button.appendChild(detail);
          button.addEventListener('click',function(){select(kind,item.index);field.input.focus();});
          li.appendChild(button);field.list.appendChild(li);
        });
        if(!matches.length){var li=doc.createElement('li');li.textContent='No facilities match this name.';li.className='navigation-no-match';field.list.appendChild(li);}
        field.list.hidden=false;field.input.setAttribute('aria-expanded','true');
      });
      field.input.addEventListener('keydown',function(event){
        if(event.key==='Escape')closeResults(kind);
        if(event.key==='ArrowDown'){
          var first=field.list.querySelector('button');if(first){event.preventDefault();first.focus();}
        }
      });
      field.list.addEventListener('keydown',function(event){
        var buttons=Array.from(field.list.querySelectorAll('button')),position=buttons.indexOf(doc.activeElement);
        if(event.key==='Escape'){closeResults(kind);field.input.focus();}
        if(event.key==='ArrowDown'&&position<buttons.length-1){event.preventDefault();buttons[position+1].focus();}
        if(event.key==='ArrowUp'){event.preventDefault();(position>0?buttons[position-1]:field.input).focus();}
      });
    });
    form.addEventListener('submit',function(event){event.preventDefault();showPreview();});
    directions.addEventListener('click',function(event){
      if(directions.getAttribute('aria-disabled')==='true')event.preventDefault();
    });
    doc.getElementById('navigationSwap').addEventListener('click',function(){
      var prior=selected.origin;selected.origin=selected.destination;selected.destination=prior;
      ['origin','destination'].forEach(function(kind){
        var row=selected[kind]===null?null:rows[selected[kind]];
        fields[kind].input.value=row?row[0]:'';
        fields[kind].hint.textContent=row?row[1].toFixed(5)+', '+row[2].toFixed(5):'Type at least two characters to search.';
        closeResults(kind);
      });refresh();
    });
    doc.getElementById('navigationClear').addEventListener('click',function(){
      selected.origin=selected.destination=null;
      ['origin','destination'].forEach(function(kind){fields[kind].input.value='';fields[kind].hint.textContent='Type at least two characters to search.';closeResults(kind);});
      refresh();fields.origin.input.focus();
    });
    doc.addEventListener('pointerdown',function(event){
      ['origin','destination'].forEach(function(kind){
        if(!fields[kind].input.parentElement.contains(event.target))closeResults(kind);
      });
    });
    function showPreview(){
      if(!hasPair()||selected.origin===selected.destination)return;
      var a=rows[selected.origin],b=rows[selected.destination];
      if(options.onPreview)options.onPreview();
      layers.clearLayers();
      [a,b].forEach(function(row,index){
        var label=doc.createElement('div'),letter=doc.createElement('b'),name=doc.createElement('span');label.className='navigation-endpoint-popup';
        letter.textContent=index?'B':'A';name.textContent=row[0];label.appendChild(letter);label.appendChild(name);
        L.marker([row[1],row[2]],{pane:'crater',icon:L.divIcon({className:'navigation-marker '+(index?'destination':'origin'),html:index?'B':'A',iconSize:[30,30],iconAnchor:[15,15]})})
          .bindPopup(label).addTo(layers);
      });
      doc.getElementById('navigationPair').textContent=a[0]+' → '+b[0];
      doc.getElementById('navigationDirect').textContent=directKm(a,b).toLocaleString('en-GB',{maximumFractionDigits:1})+' km';
      result.hidden=false;map.closePopup();map.fitBounds(L.latLngBounds([[a[1],a[2]],[b[1],b[2]]]).pad(.25),{maxZoom:13});
      announce('A and B are marked on the map. Open Google Maps to calculate the driving route.');
    }
    refresh();
    return {select:function(kind,index){if(kind!=='origin'&&kind!=='destination')return;select(kind,index);options.activate();},
      showPreview:showPreview,hidePreview:function(){layers.clearLayers();result.hidden=true;}};
  }
  var api={validStation:validStation,googleDirections:googleDirections,directKm:directKm,searchStations:searchStations,create:create};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.SPXNavigation=api;
})(typeof window!=='undefined'?window:this);
