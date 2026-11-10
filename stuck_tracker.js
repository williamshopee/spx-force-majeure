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
    return snapshot.status!=='ok' || !snapshot.fetched_at || Date.now()-Date.parse(snapshot.fetched_at)>20*60*1000;
  }
  function value(raw, pct){
    return typeof raw==='number' && Number.isFinite(raw)?(pct?raw.toFixed(2)+'%':raw.toLocaleString('en-US')):'Not available';
  }
  function sourceLink(snapshot){
    if(!/^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]+\/edit#gid=\d+$/.test(snapshot.source_url||''))return null;
    var a=node('a',null,'Open tracker');a.href=snapshot.source_url;a.target='_blank';a.rel='noopener noreferrer';return a;
  }
  function createSection(snapshot, index, type){
    if(!snapshot || type!==2 || !snapshot.rows || !snapshot.rows[index])return null;
    var row=snapshot.rows[index], sec=node('section','stuck-sec');
    sec.appendChild(node('h4',null,'L&D Stuck'));
    sec.appendChild(node('p','stuck-stamp',(old(snapshot)?'Cached data · ':'Fetched · ')+stamp(snapshot.fetched_at)));
    var dl=node('dl','prof-grid stuck-grid');
    [['Stuck parcels',value(row.absolute)],['% of inbound',value(row.inbound_pct,true)],
     ['Still alive',value(row.alive_pct,true)],['Stuck aging < 16 days',value(row.aging_lt16)],
     ['Most last status',row.last_status||'Not available'],['% of last status',value(row.last_status_pct,true)]].forEach(function(pair,k){
       dl.appendChild(node('dt',null,pair[0]));dl.appendChild(node('dd',k===0?'big':null,pair[1]));
     });
    sec.appendChild(dl);
    sec.appendChild(node('p','stuck-note','Tracker values as reported. Unavailable cells are not treated as zero.'));
    if(old(snapshot))sec.appendChild(node('p','stuck-warning',snapshot.message||'This snapshot is older than 20 minutes. Refresh the app to check Sheets.'));
    var link=sourceLink(snapshot);if(link)sec.appendChild(link);
    return sec;
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
    host.appendChild(node('p',old(snapshot)?'stuck-warning':'muted-note',snapshot.message||'Checks every 10 minutes while this app is open.'));
    host.appendChild(node('p','muted-note','Fetch time is when the app read Sheets; it does not verify when the tracker owner updated the source.'));
    var link=sourceLink(snapshot);if(link)host.appendChild(link);
  }
  root.SPXStuck={createSection:createSection,renderStatus:renderStatus,stamp:stamp};
})(typeof window!=='undefined'?window:globalThis);
