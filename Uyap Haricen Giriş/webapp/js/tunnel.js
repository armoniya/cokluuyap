// UYAP Tarayıcı İstemcisi — "ev istemcisi"nin (home_client.py) tarayıcı içi karşılığı.
//
// Bu sayfa (ana/üst pencere) WebRTC tünelini AÇIK TUTAR ve hiç gezinmez. UYAP içeriği
// alttaki tam ekran <iframe> içinde yüklenir; iframe'in tüm istekleri Service Worker
// tarafından yakalanıp bu sayfadaki DataChannel üzerinden DOĞRUDAN ofise tünellenir.
// Böylece gezinme iframe içinde olur, üst sayfa (ve WebRTC) yaşamaya devam eder.
//
// Satıcı sunucusu yalnızca bu statik dosyaları + SDP'yi taşır; UYAP verisi P2P akar.

import { encodeFrames, Reassembler, finalize } from "./wire.js";

const REQUEST_TIMEOUT = 120000; // ms — büyük UDF/PDF indirmeleri
const READY_TIMEOUT = 30000;    // ms — kanal açılması için bekleme
const HEARTBEAT_INTERVAL = 15000; // ms — boştayken kanalı/NAT eşlemesini canlı tutan ping
// P2P (WebRTC) bu süre içinde AÇILMAZSA sunucu üzerinden aktarmaya (WS-relay) düşülür.
// CGNAT/simetrik NAT arkasında STUN tek başına yetmez; TURN da yoksa P2P hiç kurulamaz.
// Ofis ajanı relay_hello/relay yolunu zaten destekler — fallback olmadan kullanıcı
// sonsuza kadar "bağlantı kuruluyor…" ekranında kalıyordu.
const P2P_FALLBACK_TIMEOUT = 12000; // ms

// ---- Yapılandırma (config.js -> window.UYAP_CONFIG, ?room/?signaling ile ezilebilir) ----
const cfg = window.UYAP_CONFIG || {};
const params = new URLSearchParams(location.search);
// v2: giriş artık KULLANICI ADI + parola ile yapılır (oda anahtarı kullanıcıya gösterilmez).
// 'room' tel alanı geriye-uyum için kullanıcı adını taşır. ?room= (dev) verilmemişse
// index.html bir giriş formu gösterir ve __uyapStart ile bu değerleri doldurur.
let ROOM = params.get("user") || params.get("room") || cfg.room || "";
// İki-alanlı login: ayrı ofis kodu. Kullanıcı 'kullanici@ofis' tam kimliği girdiyse boş
// kalır; sunucu (_login_id) office_code varsa ve kullanıcıda '@' yoksa birleştirir.
let OFFICE_CODE = params.get("office") || cfg.office_code || "";
// signaling verilmemişse AYNI origin'in /ws'inden türet (birleşik vendor_server).
function defaultSignaling() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return proto + "//" + location.host + "/ws";
}
let SIGNALING = params.get("signaling") || cfg.signaling || defaultSignaling();
const ICE = cfg.ice || [];
// Parola: URL (?pw=) ya da config; yoksa katılımda bir kez sorulur. Sunucuda hesap
// doğrulaması açıksa oda anahtarı + parola eşleşmezse bağlantı reddedilir.
let PASSWORD = params.get("pw") || cfg.password || "";

// index.html'in giriş formundan çağrılır: kullanıcı adı + parola (+ ofis kodu) gelir, tünel başlar.
window.__uyapStart = function (username, password, office_code) {
  ROOM = (username || "").trim();
  PASSWORD = password || "";
  OFFICE_CODE = (office_code || "").trim();
  if (!ROOM) { status("Kullanıcı adı gerekli.", "err"); return; }
  authRejected = false;  // yeni deneme: yeniden bağlanmayı tekrar etkinleştir
  backoff = 1000;
  p2pFailed = false;     // taze girişte P2P'ye yeniden şans ver
  connectSignaling();
};

// Pano (ana sayfa): sunucudan taze bağlantı durumu iste (ofis çevrimiçi mi + sunucunun
// en son görülme zamanı). Yanıt window.__uyapStatusReply kancasına düşer. ws açıksa true.
window.__uyapQueryStatus = function () {
  try {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "status" }));
      return true;
    }
  } catch (_) {}
  return false;
};

function status(text, level) {
  if (typeof window.__uyapStatus === "function") window.__uyapStatus(text, level);
  console.log("[tünel]", text);
}

// --------------------------------------------------------------------------------------
// Tünel durumu
// --------------------------------------------------------------------------------------
let channel = null;
const reasm = new Reassembler();
const pending = new Map();      // id -> {resolve, reject}
let readyWaiters = [];

function flushReady() {
  const ws = readyWaiters;
  readyWaiters = [];
  for (const w of ws) w();
}

function failAll(reason) {
  for (const { reject } of pending.values()) reject(new Error(reason));
  pending.clear();
}

function ensureReady() {
  if (channel && channel.readyState === "open") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      readyWaiters = readyWaiters.filter((w) => w !== onReady);
      reject(new Error("Ofis bağlantısı kurulamadı (kanal hazır değil)."));
    }, READY_TIMEOUT);
    function onReady() { clearTimeout(t); resolve(); }
    readyWaiters.push(onReady);
  });
}

async function drain(ch, threshold = 1_000_000) {
  while (ch.bufferedAmount > threshold) {
    await new Promise((r) => setTimeout(r, 10));
  }
}

async function sendMessage(ch, id, kind, meta, body) {
  for (const frame of encodeFrames(id, kind, meta, body)) {
    await drain(ch);
    ch.send(frame);
  }
}

function randomId() {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function headersToObject(pairs) {
  const o = {};
  for (const [k, v] of pairs) o[k] = v;
  return o;
}

// SW'den gelen mantıksal isteği ofise tüneller; {status, headers, body(Uint8Array)} döner.
async function tunnelRequest(req) {
  await ensureReady();
  const id = randomId();
  const meta = {
    method: req.method,
    path: req.path,                 // başında "/" var; ofis lstrip yapıyor
    query: req.query || "",
    headers: headersToObject(req.headers || []),
    proxy_base: location.origin,    // ofis UYAP URL'lerini bu origin'e yeniden yazar
    client: "sw",                   // ofis: enjekte script GÖMME (SW zaten yakalıyor)
  };
  const body = req.body ? new Uint8Array(req.body) : new Uint8Array(0);

  const result = new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
  });
  const timer = setTimeout(() => {
    const p = pending.get(id);
    if (p) { pending.delete(id); p.reject(new Error("Ofis zamanında yanıt vermedi.")); }
  }, REQUEST_TIMEOUT);

  try {
    await sendMessage(channel, id, "req", meta, body);
    const rec = await result;
    return {
      status: (rec.meta && rec.meta.status) || 502,
      headers: (rec.meta && rec.meta.headers) || {},
      body: rec.body,
    };
  } finally {
    clearTimeout(timer);
    pending.delete(id);
  }
}

// --------------------------------------------------------------------------------------
// WebRTC (offerer) + signaling
// --------------------------------------------------------------------------------------
// --- Bağlantı teşhisi: aktif ICE adayı tipi (host/srflx/relay) + RTT. Sekme başlığına
// ve konsola yazar. relay görürsek P2P kurulamamış (TURN'e düşmüş) demektir; yüksek RTT
// ise yolun kendisi yavaş demektir. window.__uyapStats ile de okunabilir.
let statsTimer = null;
function startStatsMonitor() {
  if (statsTimer) clearInterval(statsTimer);
  statsTimer = setInterval(async () => {
    if (!pc) return;
    try {
      const stats = await pc.getStats();
      let pair = null, local = null;
      stats.forEach((r) => {
        if (r.type === "candidate-pair" && r.state === "succeeded" &&
            (r.nominated || !pair)) pair = r;
      });
      if (!pair) return;
      stats.forEach((r) => { if (r.id === pair.localCandidateId) local = r; });
      const type = local ? local.candidateType : "?";           // host|srflx|prflx|relay
      const rtt = pair.currentRoundTripTime != null
        ? Math.round(pair.currentRoundTripTime * 1000) : "?";
      const label = type === "relay" ? "RELAY(!)" : type === "host" ? "yerel" : "P2P";
      window.__uyapStats = { type, rttMs: rtt };
      document.title = `UYAP — ${label} ${rtt}ms`;
      console.log(`[tünel] bağlantı tipi=${type} RTT=${rtt}ms`);
    } catch (_) {}
  }, 3000);
}

let heartbeatTimer = null;
function startHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer);
  // Boştayken bile kanaldan trafik geçsin: router'ın UDP NAT eşlemesi (tipik 30-120 sn
  // timeout) düşmeden iki yönde de paket görür. Ofis bu "ping"e "pong" ile yanıtlar.
  heartbeatTimer = setInterval(async () => {
    if (!channel || channel.readyState !== "open") return;
    try {
      await sendMessage(channel, randomId(), "ping", {}, new Uint8Array(0));
    } catch (_) {}
  }, HEARTBEAT_INTERVAL);
}

function attachChannel(ch) {
  channel = ch;
  ch.binaryType = "arraybuffer";
  ch.onopen = () => {
    if (channel !== ch) return; // bu arada relay'e/yeni kanala geçildiyse eskisi karışmasın
    if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
    status("Ofis bağlantısı kuruldu — UYAP açılıyor.", "ok");
    flushReady();
    startStatsMonitor();
    startHeartbeat();
    if (typeof window.__uyapTunnelOpen === "function") window.__uyapTunnelOpen();
  };
  ch.onclose = () => {
    if (channel !== ch) return; // eski (değiştirilen) kanalın kapanışı yenisini bozmasın
    if (statsTimer) { clearInterval(statsTimer); statsTimer = null; }
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null; }
    failAll("Kanal kapandı.");
    if (typeof window.__uyapTunnelClosed === "function") window.__uyapTunnelClosed();
    status("Bağlantı koptu, yeniden bağlanılıyor…", "warn");
  };
  ch.onmessage = async (ev) => {
    if (channel !== ch) return;
    let rec;
    try {
      rec = reasm.feed(ev.data);
    } catch (e) {
      console.error("[tünel] çerçeve çözme hatası", e);
      return;
    }
    if (!rec || rec.kind !== "res") return;
    const p = pending.get(rec.id);
    if (!p) return;
    pending.delete(rec.id);
    try {
      await finalize(rec);             // gerekiyorsa zlib aç
      p.resolve(rec);
    } catch (e) {
      p.reject(e);
    }
  };
}

function waitIceComplete(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
    function check() { if (pc.iceGatheringState === "complete") done(); }
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(resolve, 3000); // bir aday takılırsa elde olanla devam et
  });
}

let pc = null;
let ws = null;
let backoff = 1000;
let authRejected = false;  // kimlik reddedildiyse otomatik yeniden bağlanma yapma

// --------------------------------------------------------------------------------------
// WS-relay fallback: P2P kurulamazsa (TURN yok + CGNAT/simetrik NAT) aynı signaling
// WebSocket'i veri kanalı olarak kullanılır. Ofis ajanı relay_hello/relay protokolünü
// zaten konuşur; çerçeveler base64'lenip {"type":"relay","b64":...} olarak taşınır.
// Veri bu modda satıcı sunucusundan GEÇER (yalnızca son çare) ama her ağda çalışır.
// --------------------------------------------------------------------------------------
let relay = null;
let fallbackTimer = null;
let p2pFailed = false; // bu oturumda P2P bir kez başarısız olduysa sonraki denemeler doğrudan relay

function b64FromBytes(buf) {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = "";
  const STEP = 0x8000; // apply() argüman sınırına takılmamak için parça parça
  for (let i = 0; i < bytes.length; i += STEP) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + STEP));
  }
  return btoa(s);
}

function bytesFromB64(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// Signaling ws'ini DataChannel arayüzüyle sarar; attachChannel/sendMessage değişmeden çalışır.
class RelayChannel {
  constructor(sigWs) {
    this.ws = sigWs;
    this.readyState = "connecting";
    this.binaryType = "arraybuffer";
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
  }
  get bufferedAmount() { return this.ws.bufferedAmount; } // drain() geri-basıncı ws'ten okur
  send(frame) { this.ws.send(JSON.stringify({ type: "relay", b64: b64FromBytes(frame) })); }
  markOpen() {
    if (this.readyState === "open") return;
    this.readyState = "open";
    if (this.onopen) this.onopen();
  }
  close() {
    if (this.readyState === "closed") return;
    this.readyState = "closed";
    if (this.onclose) this.onclose();
  }
}

function closeRelay() {
  if (!relay) return;
  const r = relay;
  relay = null;
  try { r.close(); } catch (_) {}
}

function startRelay() {
  if (channel && channel.readyState === "open") return;      // bağlantı zaten çalışıyor
  if (relay && relay.readyState !== "closed") return;         // el sıkışma zaten sürüyor
  if (!ws || ws.readyState !== WebSocket.OPEN) return;        // signaling yoksa relay de yok
  p2pFailed = true;
  if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
  if (pc) { try { pc.close(); } catch (_) {} pc = null; }
  relay = new RelayChannel(ws);
  attachChannel(relay);
  ws.send(JSON.stringify({ type: "relay_hello" }));
  status("P2P kurulamadı; sunucu üzerinden aktarma deneniyor…", "warn");
}

async function startOffer() {
  if (channel && channel.readyState === "open") return; // relay ya da P2P zaten açık
  if (p2pFailed) { startRelay(); return; }              // bu oturumda P2P zaten başarısız oldu
  if (pc && pc.connectionState === "connected") return;
  if (pc) { try { await pc.close(); } catch (_) {} }
  pc = new RTCPeerConnection({ iceServers: ICE });

  // ordered:false -> büyük PDF/UDF inerken arkasındaki küçük AJAX'lar SCTP sırasında
  // beklemesin (head-of-line yok); kanal yine güvenilir, Reassembler seq ile birleştirir.
  const ch = pc.createDataChannel("uyap", { ordered: false });
  attachChannel(ch);

  const thisPc = pc; // relay'e geçilince dış pc null olur; kapanış kendi pc'sini görsün
  thisPc.onconnectionstatechange = () => {
    if (pc !== thisPc) return; // terk edilen P2P denemesinin olayları yenisini bozmasın
    status("P2P durumu: " + thisPc.connectionState, thisPc.connectionState === "connected" ? "ok" : "warn");
    if (["failed", "closed", "disconnected"].includes(thisPc.connectionState)) {
      failAll("Bağlantı sıfırlandı.");
      // P2P kesin başarısız: bekçiyi bekleme, hemen sunucu aktarmasına düş.
      if (thisPc.connectionState === "failed") startRelay();
    }
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitIceComplete(pc);          // SDP "tam" olsun (aiortc gibi trickle yok)
  ws.send(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp, sdptype: pc.localDescription.type }));
  status("Ofise teklif gönderildi, yanıt bekleniyor…", "warn");
  // Bekçi: P2P bu süre içinde açılmazsa WS-relay'e düş.
  if (fallbackTimer) clearTimeout(fallbackTimer);
  fallbackTimer = setTimeout(() => {
    fallbackTimer = null;
    if (!channel || channel.readyState !== "open") startRelay();
  }, P2P_FALLBACK_TIMEOUT);
}

function connectSignaling() {
  status("Buluşturma sunucusuna bağlanılıyor…", "warn");
  ws = new WebSocket(SIGNALING);

  ws.onopen = () => {
    backoff = 1000;
    if (!PASSWORD && typeof window.prompt === "function") {
      PASSWORD = window.prompt("Bağlantı parolası:") || "";
    }
    ws.send(JSON.stringify({ role: "home", room: ROOM, office_code: OFFICE_CODE, password: PASSWORD }));
    status("Odaya katıldı, ofis bekleniyor…", "warn");
  };

  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    try {
      if (msg.type === "start") {
        // Ofis odada (ya da yeni bağlandı): teklif üret. Durum etiketini de güncelle ki
        // "joined" anında ofis yoktuysa ekranda kalan "ofis bekleniyor" yazısı asılı kalmasın.
        if (typeof window.__uyapOfficeState === "function") window.__uyapOfficeState(true);
        status("Ofis bulundu, bağlantı kuruluyor…", "warn");
        await startOffer();
      } else if (msg.type === "joined") {
        // Kimlik doğrulandı: panoya (ana sayfa) rol/ofis etiketi + sunucu son-görülme bilgisi geçir.
        if (typeof window.__uyapAuthed === "function") window.__uyapAuthed(msg);
        // peer_present yalnızca KATILIM ANINDAKİ ofis durumudur; ofis sonra bağlanırsa
        // "start" gelir ve yukarıda güncellenir. Bu yüzden burada ham false yazmıyoruz.
        status(msg.peer_present ? "Ofis hazır, bağlantı kuruluyor…"
                               : "Odaya katıldı, ofis bekleniyor…", "warn");
      } else if (msg.type === "status") {
        // Panonun {"type":"status"} sorgusuna sunucunun doğrudan yanıtı.
        if (typeof window.__uyapStatusReply === "function") window.__uyapStatusReply(msg);
      } else if (msg.type === "answer") {
        if (pc && pc.signalingState === "have-local-offer") {
          await pc.setRemoteDescription({ sdp: msg.sdp, type: msg.sdptype || "answer" });
        }
      } else if (msg.type === "relay_ready") {
        // Ofis relay el sıkışmasını kabul etti; kanal artık açık.
        if (relay && channel === relay) {
          relay.markOpen();
          status("Sunucu üzerinden aktarma kuruldu — UYAP açılıyor.", "ok");
          document.title = "UYAP — sunucu aktarımı";
        }
      } else if (msg.type === "relay") {
        // Ofisten gelen bir cevap çerçevesi (base64 ikili) — kanal işleyicisine ver.
        if (relay && relay.onmessage) relay.onmessage({ data: bytesFromB64(msg.b64).buffer });
      } else if (msg.type === "peer_left") {
        if (typeof window.__uyapOfficeState === "function") window.__uyapOfficeState(false);
        status("Ofis ayrıldı, bekleniyor…", "warn");
        failAll("Ofis ayrıldı.");
        if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
        closeRelay(); // ofis yeniden bağlanınca 'start' ile taze el sıkışma yapılır
        if (pc) { try { await pc.close(); } catch (_) {} pc = null; }
      } else if (msg.type === "error") {
        // Kimlik/parola reddi: otomatik yeniden bağlanmayı DURDUR (aynı yanlış bilgiyle
        // sonsuz döngüye girmesin) ve giriş formunu yeniden göster.
        authRejected = true;
        if (typeof window.__uyapRelogin === "function") {
          window.__uyapRelogin("Giriş reddedildi: " + msg.error);
        } else {
          status("Buluşturma reddetti: " + msg.error, "err");
        }
        try { ws.close(); } catch (_) {}
      }
    } catch (e) {
      console.error("[tünel] signaling mesaj hatası", e);
    }
  };

  ws.onclose = () => {
    if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
    closeRelay(); // relay bu ws üzerinde yaşıyordu; ws ile birlikte ölür
    failAll("Signaling koptu.");
    if (authRejected) return;  // yanlış kimlik: kullanıcı formdan yeniden denesin
    status("Buluşturma bağlantısı koptu, " + (backoff / 1000) + "s sonra yeniden…", "warn");
    setTimeout(connectSignaling, backoff);
    backoff = Math.min(backoff * 2, 30000);
  };

  ws.onerror = () => { try { ws.close(); } catch (_) {} };
}

// --------------------------------------------------------------------------------------
// Service Worker köprüsü: SW her yakaladığı isteği bize yollar, biz tünelleyip cevaplarız.
// --------------------------------------------------------------------------------------
function wireServiceWorkerBridge() {
  navigator.serviceWorker.addEventListener("message", async (event) => {
    const msg = event.data;
    if (!msg || msg.type !== "uyap-req") return;
    const port = event.ports[0];
    try {
      const res = await tunnelRequest(msg);
      const transfer = res.body && res.body.buffer ? [res.body.buffer] : [];
      port.postMessage(res, transfer);
    } catch (e) {
      port.postMessage({ error: String((e && e.message) || e) });
    }
  });
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) {
    status("Tarayıcı Service Worker desteklemiyor.", "err");
    throw new Error("no-sw");
  }
  const reg = await navigator.serviceWorker.register("/__app__/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) {
    await new Promise((resolve) => {
      navigator.serviceWorker.addEventListener("controllerchange", () => resolve(), { once: true });
      setTimeout(resolve, 2000); // güvenlik ağı
    });
  }
  return reg;
}

// --------------------------------------------------------------------------------------
// Başlat
// --------------------------------------------------------------------------------------
export async function boot() {
  if (!SIGNALING) {
    status("Yapılandırma eksik (signaling).", "err");
    return;
  }
  // Service Worker'ı önce kur (kimlik girişinden bağımsız; tünel açılınca lazım).
  try {
    await registerServiceWorker();
    wireServiceWorkerBridge();
  } catch (e) {
    return; // SW yoksa devam edemeyiz
  }
  // Kullanıcı adı (ve gerekiyorsa parola) URL/config'ten geldiyse doğrudan bağlan; aksi halde
  // index.html'in giriş formunu göster (form __uyapStart'ı çağıracak).
  if (ROOM && PASSWORD) {
    connectSignaling();
  } else if (typeof window.__uyapNeedLogin === "function") {
    window.__uyapNeedLogin();
  } else if (ROOM) {
    connectSignaling(); // parola katılımda prompt ile sorulur (dev geri-uyum)
  } else {
    status("Giriş bilgisi bekleniyor…", "warn");
  }
}

boot();
