"""Read selected counters from Chromium's local WebRTC diagnostics page.

No SDP, ICE credentials, endpoint addresses, URLs, transcripts or audio leave
the diagnostics page. This does not wrap or modify the site's WebRTC APIs.
"""

RTC_STATS_JS = r"""(()=>{
  const store = window.peerConnectionDataStore;
  if (!store) return {ready:false, connections:[]};
  const numeric = ['packetsReceived','packetsSent','packetsLost','bytesReceived','bytesSent',
    'jitter','jitterBufferDelay','jitterBufferEmittedCount','jitterBufferTargetDelay',
    'jitterBufferMinimumDelay','concealedSamples','silentConcealedSamples',
    'concealmentEvents','totalSamplesReceived','insertedSamplesForDeceleration',
    'removedSamplesForAcceleration','totalAudioEnergy','totalSamplesDuration',
    'currentRoundTripTime','roundTripTime','availableIncomingBitrate',
    'totalPlayoutDelay','totalSamplesCount','synthesizedSamplesDuration',
    'synthesizedSamplesEvents'];
  const enums = {
    kind:['audio','video'],mediaType:['audio','video'],
    protocol:['udp','tcp'],relayProtocol:['udp','tcp','tls'],
    candidateType:['host','srflx','prflx','relay'],
    dtlsState:['new','connecting','connected','closed','failed'],
    iceState:['new','checking','connected','completed','disconnected','failed','closed']
  };
  const names = [...numeric, ...Object.keys(enums)];
  const connections=[];
  for (const record of Object.values(store)) {
    const data=record.toJSON();
    try {if(new URL(data.url).origin!=='https://chatgpt.com')continue;} catch {continue;}
    const groups=new Map();
    for (const [key, series] of Object.entries(data.stats||{})) {
      const metric=names.find(name=>key.endsWith('-'+name));
      if(!metric || typeof series.toJSON!=='function')continue;
      const s=series.toJSON();
      if(!['inbound-rtp','outbound-rtp','candidate-pair','transport',
           'media-playout','local-candidate','remote-candidate'].includes(s.statsType))continue;
      let values;try{values=JSON.parse(s.values||'[]');}catch{continue;}
      if(!values.length)continue;
      const value=values[values.length-1];
      const id=key.slice(0,-metric.length-1);
      let group=groups.get(id);
      if(!group){group={type:s.statsType,metrics:{}};groups.set(id,group);}
      const age=(Date.now()-new Date(s.endTime))/1000;
      if(Number.isFinite(age))group.age_s=Math.max(0,age);
      if(enums[metric]){
        if(enums[metric].includes(value))group.metrics[metric]={value};
      }else if(Number.isFinite(Number(value)) && Number.isFinite(Number(values[0]))){
        group.metrics[metric]={value:Number(value),delta:Number(value)-Number(values[0]),
          span_s:Math.max(0,(new Date(s.endTime)-new Date(s.startTime))/1000)};
      }
    }
    const reports=[];
    const priority=['inbound-rtp','media-playout','outbound-rtp','transport','candidate-pair',
      'local-candidate','remote-candidate'];
    for(const group of [...groups.values()].sort((a,b)=>priority.indexOf(a.type)-priority.indexOf(b.type))){
      if(!Object.keys(group.metrics).length)continue;
      const m=group.metrics;
      const received=m.totalSamplesReceived?.delta||0;
      const packets=(m.packetsReceived?.delta||0)+Math.max(0,m.packetsLost?.delta||0);
      if(group.type==='inbound-rtp'){
        if(received>0)group.concealed_percent=100*(m.concealedSamples?.delta||0)/received;
        if(packets>0)group.lost_percent=100*Math.max(0,m.packetsLost?.delta||0)/packets;
        if(m.jitterBufferEmittedCount?.delta>0 && m.jitterBufferDelay)
          group.jitter_buffer_ms=1000*m.jitterBufferDelay.delta/m.jitterBufferEmittedCount.delta;
      }
      if(group.type==='media-playout'){
        if(m.totalSamplesCount?.delta>0 && m.totalPlayoutDelay)
          group.playout_delay_ms=1000*m.totalPlayoutDelay.delta/m.totalSamplesCount.delta;
        if(m.totalSamplesDuration?.delta>0 && m.synthesizedSamplesDuration)
          group.synthesized_percent=100*m.synthesizedSamplesDuration.delta/m.totalSamplesDuration.delta;
      }
      reports.push(group);
      if(reports.length>=30)break;
    }
    const states=['new','checking','connecting','connected','completed',
      'disconnected','failed','closed'];
    const status={};
    for(const update of data.updateLog||[]){
      if(!['iceconnectionstatechange','connectionstatechange'].includes(update.type))continue;
      const value=String(update.value).replace(/^"|"$/g,'');
      if(states.includes(value))status[update.type]=value;
    }
    connections.push({reports,states:status,series_count:Object.keys(data.stats||{}).length});
  }
  const age=c=>Math.min(...c.reports.map(r=>r.age_s??Infinity));
  connections.sort((a,b)=>age(a)-age(b));
  return {ready:true,connections:connections.slice(0,4)};
})()"""

# Chromium 149's frontend requests getStats through this WebUI message.
# Later Chromium versions moved polling to the browser process.
RTC_REQUEST_149_JS = """(()=>{
  if(window.peerConnectionDataStore && typeof chrome?.send==='function')
    chrome.send('getStandardStats');
})()"""
