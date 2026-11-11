(function(root){
  'use strict';
  function node(tag, cls, text){
    var el=document.createElement(tag);if(cls)el.className=cls;
    if(text!=null)el.textContent=text;return el;
  }
  function stamp(value){
    var date=new Date(value);
    if(!value || !Number.isFinite(date.getTime()))return 'not recorded';
    return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jakarta',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date)+' WIB';
  }
  function old(snapshot){
    return snapshot.status!=='ok' || !snapshot.fetched_at || !Number.isFinite(Date.parse(snapshot.fetched_at)) || Date.now()-Date.parse(snapshot.fetched_at)>21*60*60*1000;
  }
  function notice(snapshot){
    if(snapshot.status==='ok' && old(snapshot))return 'This snapshot is older than 21 hours. Refresh the app to check Sheets.';
    return snapshot.message||'Checks every 20 hours while this app is open.';
  }
  function value(raw, pct){
    return typeof raw==='number' && Number.isFinite(raw)?(pct?raw.toFixed(2)+'%':raw.toLocaleString('en-US')):'Not available';
  }
  function sourceLink(snapshot){
    if(!/^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]+\/edit#gid=\d+$/.test(snapshot.source_url||''))return null;
    var a=node('a',null,'Open tracker');a.href=snapshot.source_url;a.target='_blank';a.rel='noopener noreferrer';return a;
  }
  function createSection(snapshot, index, type){
    if(!snapshot || type!==2)return null;
    var threshold=snapshot.threshold||{}, listed=threshold.rows && threshold.rows[index];
    var primary=(snapshot.rows||{})[index], row=primary||listed, rowSource=primary?snapshot:threshold;
    if(!row)return null;
    var sec=node('section','stuck-sec');
    sec.appendChild(node('h4',null,'L&D Stuck'));
    sec.appendChild(node('p','stuck-stamp',(old(rowSource)?'Cached data · ':'Fetched · ')+stamp(rowSource.fetched_at)));
    if(listed)sec.appendChild(node('p','stuck-listed',(old(threshold)?'Cached threshold list · ':'')+'Listed above threshold in the tracker'));
    var dl=node('dl','prof-grid stuck-grid');
    [['Stuck parcels',value(row.absolute)],['% of inbound',value(row.inbound_pct,true)],
     ['Still alive',value(row.alive_pct,true)],['Stuck aging < 16 days',value(row.aging_lt16)],
     ['Most last status',row.last_status||'Not available'],['% of last status',value(row.last_status_pct,true)]].forEach(function(pair,k){
       dl.appendChild(node('dt',null,pair[0]));dl.appendChild(node('dd',k===0?'big':null,pair[1]));
     });
    sec.appendChild(dl);
    sec.appendChild(node('p','stuck-note','Tracker values as reported. Unavailable cells are not treated as zero.'));
    if(old(rowSource))sec.appendChild(node('p','stuck-warning',notice(rowSource)));
    var link=sourceLink(snapshot);if(link)sec.appendChild(link);
    return sec;
  }
  function thresholdIndices(snapshot,stations){
    var rows=((snapshot||{}).threshold||{}).rows||{};
    return new Set(Object.keys(rows).filter(function(key){
      var index=Number(key);return Number.isInteger(index) && index>=0 && stations[index] && stations[index][3]===2;
    }).map(Number));
  }
  function thresholdSummary(snapshot){
    var threshold=(snapshot||{}).threshold;
    if(!threshold)return 'Threshold list is unavailable. Refresh data after connecting the tracker.';
    if(!threshold.fetched_at)return threshold.message||'Threshold list is unavailable.';
    var text=(threshold.matched||0)+' mapped SOCs from the prefiltered list · '+(threshold.unmatched||0)+' unmatched · '+(threshold.ambiguous||0)+' ambiguous';
    text+=' · '+(old(threshold)?'Cached · ':'Fetched · ')+stamp(threshold.fetched_at);
    if(old(threshold))text+=' · '+notice(threshold);
    return text;
  }
  function renderStatus(snapshot, host){
    if(!snapshot || !host)return;
    host.hidden=false;host.replaceChildren();
    host.appendChild(node('h3',null,'L&D Stuck · SOC'));
    host.appendChild(node('p','muted-note',snapshot.status==='unconfigured'?'Connection setup required':old(snapshot)?'Latest check unavailable or snapshot overdue':'Google Sheets connected'));
    if(snapshot.fetched_at){
      host.appendChild(node('p','muted-note','Fetched: '+stamp(snapshot.fetched_at)));
      host.appendChild(node('p','muted-note',(snapshot.matched||0)+' SOCs matched · '+(snapshot.unmatched||0)+' names unmatched · '+(snapshot.ambiguous||0)+' ambiguous names skipped'));
    }
    if(snapshot.checked_at)host.appendChild(node('p','muted-note','Last check: '+stamp(snapshot.checked_at)));
    if(snapshot.threshold)host.appendChild(node('p','muted-note','Threshold list: '+thresholdSummary(snapshot)));
    host.appendChild(node('p',old(snapshot)?'stuck-warning':'muted-note',notice(snapshot)));
    host.appendChild(node('p','muted-note','Auto-check: every 20 hours while the app is open. Refresh data checks immediately.'));
    host.appendChild(node('p','muted-note','Fetch time is when the app read Sheets; it does not verify when the tracker owner updated the source.'));
    var link=sourceLink(snapshot);if(link)host.appendChild(link);
  }
  function renderTopStuck(snapshot,host,stations,onSelect){
    if(!snapshot || !host)return;
    host.hidden=false;host.replaceChildren();
    host.appendChild(node('summary',null,'Top 10 stuck SOCs'));
    host.appendChild(node('p','muted-note','By total stuck parcels (# absolute) · all SOC summary rows.'));
    if(snapshot.fetched_at)host.appendChild(node('p','stuck-ranking-stamp',(old(snapshot)?'Cached · ':'Fetched · ')+stamp(snapshot.fetched_at)));
    if(old(snapshot))host.appendChild(node('p','stuck-warning',notice(snapshot)));
    if(!Array.isArray(snapshot.top_stuck)){
      host.appendChild(node('p','muted-note','Ranking is unavailable. Use Account → Refresh data to check Sheets.'));
    }else{
      var rows=snapshot.top_stuck.filter(function(row){return row && typeof row.name==='string' && Number.isInteger(row.absolute) && row.absolute>=0;}).slice(0,10);
      if(!rows.length)host.appendChild(node('p','muted-note','No SOCs with valid stuck-parcel counts are available.'));
      var list=node('ol','stuck-ranking-list');
      rows.forEach(function(row,rank){
        var index=row.station_index;
        var mapped=Number.isInteger(index) && index>=0 && stations[index] && stations[index][3]===2 &&
          stations[index][0].trim().replace(/\s+/g,' ').toLowerCase()===row.name.trim().replace(/\s+/g,' ').toLowerCase();
        var item=node('li'),content=node(mapped?'button':'div','stuck-ranking-row');
        if(mapped){content.type='button';content.title='Show '+row.name+' on map';content.addEventListener('click',function(){onSelect(index);});}
        content.appendChild(node('span','stuck-ranking-rank',String(rank+1)));
        var label=node('span','stuck-ranking-label');label.appendChild(node('span',null,row.name));
        label.appendChild(node('small',null,mapped?'View on map':'No map location'));
        content.appendChild(label);content.appendChild(node('strong','stuck-ranking-count',value(row.absolute)));
        item.appendChild(content);list.appendChild(item);
      });
      host.appendChild(list);
    }
    var link=sourceLink(snapshot);if(link)host.appendChild(link);
  }
  root.SPXStuck={createSection:createSection,renderStatus:renderStatus,renderTopStuck:renderTopStuck,stamp:stamp,thresholdIndices:thresholdIndices,thresholdSummary:thresholdSummary};
})(typeof window!=='undefined'?window:globalThis);
