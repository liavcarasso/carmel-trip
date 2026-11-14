const V='carmel-v2';
const FILES=['./','index.html','manifest.webmanifest','icon-180.png','icon-192.png','icon-512.png','day1.gpx','day2.gpx','carmel-full.gpx','data/map.json','data/relief.jpg'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(V).then(c=>c.addAll(FILES)).then(()=>self.skipWaiting()));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==V).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const req=e.request; if(req.method!=='GET') return;
  if(req.mode==='navigate'){
    // newest app when online, cached copy when offline
    e.respondWith(fetch(req).then(res=>{ const cl=res.clone(); caches.open(V).then(c=>c.put('index.html',cl)); return res; }).catch(()=>caches.match('index.html')));
    return;
  }
  e.respondWith(caches.match(req,{ignoreSearch:true}).then(r=>r||fetch(req).then(res=>{
    if(res.ok && new URL(req.url).origin===location.origin){ const cl=res.clone(); caches.open(V).then(c=>c.put(req,cl)); }
    return res;
  })));
});
