(function(root){
  'use strict';
  function validPoint(lat,lng){
    return typeof lat==='number' && typeof lng==='number' && Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat)<=90 && Math.abs(lng)<=180;
  }
  function distanceKm(a,b,c,d){
    var rad=Math.PI/180,dy=(c-a)*rad,dx=(d-b)*rad;
    var h=Math.sin(dy/2)**2+Math.cos(a*rad)*Math.cos(c*rad)*Math.sin(dx/2)**2;
    return 6371*2*Math.asin(Math.sqrt(Math.min(1,Math.max(0,h))));
  }
  function nearestEvent(events,lat,lng,airports,now){
    if(!validPoint(lat,lng))return null;
    now=now==null?Date.now():now;
    var best=null;
    (events||[]).forEach(function(event,index){
      if(!event || event.status!=='active' || event.assessment==='official_warning')return;
      var expired=['expires','until','ended'].some(function(key){
        return event[key] && Number.isFinite(Date.parse(event[key])) && Date.parse(event[key])<=now;
      });
      if(expired)return;
      var locations=[];
      if(event.hazard==='airport_closure'){
        ((event.air||{}).airports||[]).forEach(function(item){
          var airport=(airports||[]).find(function(row){return row[0]===item.iata;});
          if(airport && validPoint(airport[1],airport[2]))locations.push({lat:airport[1],lng:airport[2],label:'affected airport '+airport[0]});
        });
      }else if(validPoint(event.lat,event.lng)){
        locations.push({lat:event.lat,lng:event.lng,label:'reported event location'});
      }
      locations.forEach(function(point){
        var distance=distanceKm(lat,lng,point.lat,point.lng);
        if(!best || distance<best.distanceKm)best={event:event,index:index,distanceKm:distance,locationLabel:point.label};
      });
    });
    return best;
  }
  function node(tag,cls,text){
    var el=document.createElement(tag);if(cls)el.className=cls;if(text!=null)el.textContent=text;return el;
  }
  function sourceUrl(value){
    try{var url=new URL(value);return /^https?:$/.test(url.protocol)?url.href:null;}catch(error){return null;}
  }
  function stamp(value){
    var time=Date.parse(value);
    return Number.isFinite(time)?new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jakarta',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(time))+' WIB':null;
  }
  function nearestSection(options){
    var section=node('section','nearest-sec');
    section.appendChild(node('h4',null,'Nearest force-majeure event'));
    var nearest=nearestEvent(options.events,options.lat,options.lng,options.airports,options.now);
    if(!nearest){
      section.appendChild(node('p',null,options.loading?'Event data is loading. Reopen this facility after it finishes.':'No active confirmed event with a mapped location is available.'));
    }else{
      section.appendChild(node('strong','nearest-name',nearest.event.name||'Mapped event'));
      var km=nearest.distanceKm<1?nearest.distanceKm.toFixed(1):nearest.distanceKm.toFixed(0);
      section.appendChild(node('p','nearest-distance',km+' km · straight-line to '+nearest.locationLabel));
      section.appendChild(node('p',null,nearest.event.source||'Source not recorded'));
      var date=stamp(options.snapshotGeneratedAt);
      if(date)section.appendChild(node('p',null,'Event snapshot: '+date));
      section.appendChild(node('p',null,'Proximity does not confirm a disruption at this facility. News leads and weather warnings are excluded.'));
      var url=sourceUrl(nearest.event.source_url);
      if(url){var link=node('a',null,'Open event source');link.href=url;link.target='_blank';link.rel='noopener noreferrer';section.appendChild(link);}
      if(options.onSelect){
        var button=node('button','nearest-action','Show event on map');button.type='button';
        button.addEventListener('click',function(){options.onSelect(nearest.event);});section.appendChild(button);
      }
    }
    var snapshotTime=Date.parse(options.snapshotGeneratedAt);
    if(Number.isFinite(snapshotTime) && (options.now==null?Date.now():options.now)-snapshotTime>45*60*1000){
      section.appendChild(node('p','stuck-warning','Event snapshot is overdue. The nearest result may be outdated.'));
    }
    return section;
  }
  root.SPXFacilityInsights={validPoint:validPoint,distanceKm:distanceKm,nearestEvent:nearestEvent,nearestSection:nearestSection};
})(typeof window!=='undefined'?window:globalThis);
