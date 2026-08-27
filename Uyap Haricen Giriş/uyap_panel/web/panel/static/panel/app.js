"use strict";
// UYAP Ağ Geçidi — yerel panel istemci mantığı.

// ── CSRF ──
function getCookie(name){
  const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
  return m ? m.pop() : "";
}
const CSRF = getCookie("csrftoken");

async function postJSON(url, body){
  const r = await fetch(url, {method:"POST", headers:{
    "Content-Type":"application/json", "X-CSRFToken":CSRF}, body: JSON.stringify(body||{})});
  return {ok:r.ok, data: await r.json().catch(()=>({}))};
}
async function getJSON(url){
  const r = await fetch(url);
  return {ok:r.ok, data: await r.json().catch(()=>({}))};
}
async function postForm(url, formData){
  const r = await fetch(url, {method:"POST", headers:{"X-CSRFToken":CSRF}, body:formData});
  return {ok:r.ok, data: await r.json().catch(()=>({}))};
}

// ── Ağaç menü (sol bar) ──
document.querySelectorAll(".tree-item").forEach(t=>{
  t.addEventListener("click", ()=>{
    document.querySelectorAll(".tree-item").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(x=>x.classList.remove("active"));
    t.classList.add("active");
    document.querySelector(`.panel[data-tab="${t.dataset.target}"]`).classList.add("active");
  });
});

// ════════════════ BAĞLANTI ════════════════
const connLog = document.getElementById("conn-log");
let logIndex = 0;

function appendConnLog(lines){
  if(!lines || !lines.length) return;
  connLog.textContent += lines.join("\n") + "\n";
  connLog.scrollTop = connLog.scrollHeight;
}

async function pollLogs(){
  try{
    const {data} = await getJSON(`/api/logs?since=${logIndex}`);
    if(data.lines){ appendConnLog(data.lines); logIndex = data.total; }
  }catch(e){}
}

function applyStatus(s){
  if(!s) return;
  const sb = document.getElementById("share-btn");
  const ss = document.getElementById("share-status");
  const sbr = document.getElementById("share-browser");
  if(s.sharing){
    sb.textContent="Paylaşımı Durdur"; sb.classList.remove("ok"); sb.classList.add("danger");
    ss.textContent="● Paylaşım aktif"; ss.className="status-dot on"; sbr.disabled=false;
  }else{
    sb.textContent="Bağlantıyı Paylaş"; sb.classList.add("ok"); sb.classList.remove("danger");
    ss.textContent="● Paylaşım durduruldu"; ss.className="status-dot off"; sbr.disabled=true;
  }
  const rb = document.getElementById("recv-btn");
  const rs = document.getElementById("recv-status");
  const rbr = document.getElementById("recv-browser");
  if(s.receiving){
    rb.textContent="Bağlantıyı Kes"; rb.classList.remove("ok"); rb.classList.add("danger");
    rs.textContent="● Bağlantı aktif"; rs.className="status-dot on"; rbr.disabled=false;
  }else{
    rb.textContent="Bağlantıyı Al"; rb.classList.add("ok"); rb.classList.remove("danger");
    rs.textContent="● Bağlantı yok"; rs.className="status-dot off"; rbr.disabled=true;
  }
}

async function pollStatus(){
  try{ const {data} = await getJSON("/api/status"); applyStatus(data); }catch(e){}
}

document.getElementById("share-btn").addEventListener("click", async (e)=>{
  e.target.disabled = true;
  const {data} = await getJSON("/api/status");
  if(data.sharing){ await postJSON("/api/share/stop"); }
  else{
    const r = await postJSON("/api/share/start", {
      pin: document.getElementById("pin").value.trim(),
      cert_id: document.getElementById("cert_id").value.trim()});
    if(!r.ok && r.data.error) alert(r.data.error);
    if(r.data.message) appendConnLog([`[SİSTEM] ${r.data.message}`]);
  }
  e.target.disabled = false;
  pollStatus();
});

document.getElementById("recv-btn").addEventListener("click", async (e)=>{
  e.target.disabled = true;
  const {data} = await getJSON("/api/status");
  if(data.receiving){ await postJSON("/api/receive/stop"); }
  else{
    const r = await postJSON("/api/receive/start");
    if(!r.ok && r.data.error) alert(r.data.error);
    if(r.data.message) appendConnLog([`[SİSTEM] ${r.data.message}`]);
  }
  e.target.disabled = false;
  pollStatus();
});

document.getElementById("share-browser").addEventListener("click", ()=>postJSON("/api/open-browser"));
document.getElementById("recv-browser").addEventListener("click", ()=>postJSON("/api/open-browser"));
document.getElementById("clear-log").addEventListener("click", ()=>{ connLog.textContent=""; });

setInterval(pollLogs, 800);
setInterval(pollStatus, 1500);
pollStatus();

// ════════════════ TAKİP (İcra & MTS) ════════════════
function panelOf(kind){ return document.querySelector(`.panel[data-tab="${kind}"]`); }
function el(kind, role){ return panelOf(kind).querySelector(`[data-role="${role}"]`); }

const jobState = {}; // kind -> {jobId, logN, polling, approvalOpen}

function takipLog(kind, text){
  const box = el(kind, "log");
  box.textContent += text + "\n";
  box.scrollTop = box.scrollHeight;
}

// Dosya seçimi → ayrıştır (kaynak) ya da belge yükle (vekalet/dayanak)
document.querySelectorAll('input[type=file]').forEach(inp=>{
  inp.addEventListener("change", async ()=>{
    if(!inp.files.length) return;
    const kind = inp.dataset.kind, role = inp.dataset.role;
    const fd = new FormData();
    fd.append("file", inp.files[0]);
    fd.append("kind", kind);
    if(role !== "source") fd.append("doc", role);
    const infoEl = el(kind, role + "-info");
    infoEl.textContent = (role==="source") ? "Ayrıştırılıyor…" : "Yükleniyor…";
    const {ok, data} = await postForm("/api/takip/parse", fd);
    if(!ok){ infoEl.textContent = "Hata!"; takipLog(kind, `[HATA] ${data.error||"Yükleme başarısız"}`); return; }
    if(role === "source"){
      infoEl.textContent = `${data.filename} — ${data.count} takip`;
      takipLog(kind, `[BİLGİ] ${data.count} takip bulundu: ${data.dosya_nolar.join(", ")}${data.fazla?" …":""}`);
    }else{
      infoEl.textContent = data.filename;
    }
  });
});

// İşi başlat
document.querySelectorAll('[data-role="start"]').forEach(btn=>{
  btn.addEventListener("click", async ()=>{
    const kind = btn.dataset.kind;
    const body = {
      kind,
      il: el(kind,"il").value.trim(),
      adliye: el(kind,"adliye").value.trim(),
      onay_modu: panelOf(kind).querySelector(`input[name="onay-${kind}"]:checked`).value,
    };
    if(kind === "mts"){
      body.odeme_aktif = el(kind,"odeme-aktif").checked;
      body.odeme_onay_modu = panelOf(kind).querySelector('input[name="odeme-onay-mts"]:checked').value;
      body.tebligat_aktif = el(kind,"tebligat-aktif").checked;
      body.tebligat_onay_modu = panelOf(kind).querySelector('input[name="tebligat-onay-mts"]:checked').value;
    }
    btn.disabled = true;
    el(kind,"status").textContent = "İş gönderiliyor…";
    el(kind,"bar").style.width = "0%";
    const {ok, data} = await postJSON("/api/takip/start", body);
    if(!ok){
      btn.disabled = false;
      el(kind,"status").textContent = "Başlatılamadı.";
      takipLog(kind, `[HATA] ${data.error||"Başlatılamadı"}`);
      return;
    }
    jobState[kind] = {jobId:data.job_id, logN:0, polling:true, approvalOpen:false};
    el(kind,"cancel").disabled = false;
    takipLog(kind, `[BİLGİ] İş başlatıldı (id: ${data.job_id}).`);
    pollJob(kind);
  });
});

// İptal
document.querySelectorAll('[data-role="cancel"]').forEach(btn=>{
  btn.addEventListener("click", async ()=>{
    const kind = btn.dataset.kind, st = jobState[kind];
    if(!st || !st.jobId) return;
    await postJSON("/api/takip/cancel", {job_id: st.jobId});
    takipLog(kind, "[BİLGİ] İptal isteği gönderildi.");
  });
});

async function pollJob(kind){
  const st = jobState[kind];
  if(!st || !st.polling || !st.jobId) return;
  const {ok, data} = await getJSON(`/api/takip/status?job_id=${st.jobId}`);
  if(!ok){ takipLog(kind, `[HATA] Durum alınamadı: ${data.error||""}`); setTimeout(()=>pollJob(kind),2500); return; }
  const job = data.job || {};
  const prog = job.progress || {};
  const done = prog.done||0, total = prog.total||0;
  el(kind,"status").textContent = `Durum: ${job.status} · ${done}/${total||"?"} · ${prog.message||""}`;
  el(kind,"bar").style.width = total ? `${Math.round(done/total*100)}%` : "0%";

  const logs = job.logs || [];
  for(let i=st.logN;i<logs.length;i++) takipLog(kind, logs[i].line||"");
  st.logN = logs.length;

  if(job.status === "awaiting_approval" && !st.approvalOpen){
    st.approvalOpen = true;
    showApproval(kind, job.pending_approval || {});
  }

  if(["done","error","cancelled"].includes(job.status)){
    st.polling = false;
    panelOf(kind).querySelector('[data-role="start"]').disabled = false;
    el(kind,"cancel").disabled = true;
    const res = job.result || {};
    if(job.status==="done") takipLog(kind, `[BİTTİ] ${res.basari||0} tamam, ${res.atlanan||0} atlandı, ${res.hata||0} hata.`);
    else if(job.status==="error") takipLog(kind, `[HATA] İş hata ile bitti: ${job.error||""}`);
    else takipLog(kind, "[BİLGİ] İş iptal edildi.");
    return;
  }
  setTimeout(()=>pollJob(kind), 1500);
}

// ── Onay modalı ──
const modalBg = document.getElementById("modal-bg");
const modalTitle = document.getElementById("modal-title");
const modalBody = document.getElementById("modal-body");
const modalFooter = document.getElementById("modal-footer");

function closeModal(){ modalBg.classList.remove("show"); modalBody.innerHTML=""; modalFooter.innerHTML=""; }

async function sendApproval(kind, decision, selection){
  const st = jobState[kind];
  closeModal();
  st.approvalOpen = false;
  await postJSON("/api/takip/approve", {job_id: st.jobId, decision, selection: selection||null});
}

function ozetMetni(o){
  let s = ["="+"=".repeat(58),
    `DOSYA NO   : ${o.dosya_no??""}`,
    `ALACAKLI   : ${o.alacakli??""}`,
    `IBAN       : ${o.iban??""}`,
    `ABONE NO   : ${o.abone_no??""}`,
    "=".repeat(59), "BORÇLULAR:"];
  (o.borclular||[]).forEach((b,i)=>s.push(`  ${i+1}. ${b.ad??""} ${b.soyad??""} (TC: ${b.kimlik??""})`));
  s.push("=".repeat(59), "ALACAK KALEMLERİ:");
  (o.kalemler||[]).forEach((k,i)=>s.push(`  ${i+1}. ${k.ad??""}: ${k.tutar??""} TL`));
  s.push(`TOPLAM ALACAK : ${o.toplam??""} TL`, "=".repeat(59));
  return s.join("\n");
}

function btn(label, cls, fn){
  const b = document.createElement("button");
  b.className = "btn " + cls; b.textContent = label; b.onclick = fn; return b;
}

function harcMetni(kalem){
  let s = [`DOSYA NO   : ${kalem.dosya_no??""}`, `ALACAKLI   : ${kalem.alacakli??""}`, "-".repeat(40), "HARÇ/MASRAF:"];
  (kalem.harclar||[]).forEach(h=>s.push(`  · ${h.ad??""}: ${h.miktar??0} TL`));
  s.push("-".repeat(40), `TOPLAM     : ${kalem.toplam_harc??0} TL`);
  return s.join("\n");
}
function tebligatMetni(kalem){
  let s = [`DOSYA NO   : ${kalem.dosya_no??""}`, `ALACAKLI   : ${kalem.alacakli??""}`, "-".repeat(40), "TARAFLAR:"];
  (kalem.borclular||[]).forEach((b,i)=>s.push(`  ${i+1}. ${b.ad??""} ${b.soyad??""}`));
  return s.join("\n");
}

// Tek-tek onay: her kalem için "Atla"/"Onayla" — reddedilen yalnız BU AŞAMAYI atlar,
// bir önceki aşamada zaten gerçekleşmiş olan (dosya açık / ödenmiş) hiçbir şey geri alınmaz.
function tekTekModal(kind, title, metinFn, kalem){
  modalTitle.textContent = title;
  const pre = document.createElement("pre"); pre.textContent = metinFn(kalem);
  modalBody.appendChild(pre);
  modalFooter.appendChild(btn("Tümünü İptal Et (kalanları durdur)","danger",()=>sendApproval(kind,"cancel")));
  const right = document.createElement("div");
  right.appendChild(btn("Bu Dosyayı Atla","ghost",()=>sendApproval(kind,"skip")));
  const ok = btn("Onayla","ok",()=>sendApproval(kind,"approve")); ok.style.marginLeft="8px";
  right.appendChild(ok);
  modalFooter.appendChild(right);
}

// Toplu onay: tüm kalemler önizlenir, devam edecekler seçilir. reject={label,fn}: sol
// (danger) buton — takip-açmada GERÇEK iptaldir (henüz hiçbir şey oluşmadı); ödeme/tebligat
// aşamasında yalnız "bu aşamayı hiç kimseye uygulama" anlamına gelir (bkz. çağıran yorumlar)
// — bir önceki aşamada zaten gerçekleşmiş olan (dosya açık/ödenmiş) hiçbir şey geri alınmaz.
function topluModal(kind, title, satirFn, kalemler, reject){
  modalTitle.textContent = title;
  const boxes = [];
  kalemler.forEach(o=>{
    const dn = String(o.dosya_no);
    const line = document.createElement("label"); line.className="checkline";
    const cb = document.createElement("input"); cb.type="checkbox"; cb.checked=true; cb.value=dn;
    boxes.push(cb);
    line.appendChild(cb);
    const sp = document.createElement("span"); sp.textContent = satirFn(o);
    line.appendChild(sp);
    modalBody.appendChild(line);
  });
  modalFooter.appendChild(btn(reject.label,"danger",reject.fn));
  modalFooter.appendChild(btn("Seçilenlerle Devam Et","ok",()=>{
    const sel = boxes.filter(b=>b.checked).map(b=>b.value);
    sendApproval(kind,"approve",sel);
  }));
}

function showApproval(kind, pending){
  modalBg.classList.add("show");
  const mod = pending.mod;
  if(mod === "tek_tek"){
    const o = pending.takip || {};
    modalTitle.textContent = `Takip onayı — Dosya No: ${o.dosya_no??""}`;
    const pre = document.createElement("pre"); pre.textContent = ozetMetni(o);
    modalBody.appendChild(pre);
    modalFooter.appendChild(btn("Tümünü İptal Et","danger",()=>sendApproval(kind,"cancel")));
    const right = document.createElement("div");
    right.appendChild(btn("Bu Dosyayı Atla","ghost",()=>sendApproval(kind,"skip")));
    const ok = btn("Onayla ve Aç","ok",()=>sendApproval(kind,"approve")); ok.style.marginLeft="8px";
    right.appendChild(ok);
    modalFooter.appendChild(right);
  }else if(mod === "toplu"){
    const list = pending.takipler || [];
    // Takip-açmada henüz hiçbir şey OLUŞMADIĞI için sol buton GERÇEK iptaldir.
    topluModal(kind, `Toplu önizleme — ${list.length} takip. Açılacakları seçin:`,
      o=>`Dosya ${o.dosya_no} · ${o.alacakli||""} · ${(o.borclular||[]).length} borçlu · Toplam ${o.toplam||0} TL`,
      list, {label:"Tümünü İptal Et", fn:()=>sendApproval(kind,"cancel")});
  }else if(mod === "odeme_tek_tek"){
    tekTekModal(kind, `Ödeme onayı — Dosya No: ${(pending.kalem||{}).dosya_no??""}`, harcMetni, pending.kalem||{});
  }else if(mod === "odeme_toplu"){
    const list = pending.kalemler || [];
    topluModal(kind, `Toplu ödeme önizleme — ${list.length} dosya. Ödenecekleri seçin:`,
      o=>`Dosya ${o.dosya_no} · ${o.alacakli||""} · Harç toplamı ${o.toplam_harc||0} TL`,
      list, {label:"Hiçbirini Ödeme", fn:()=>sendApproval(kind,"approve",[])});
  }else if(mod === "tebligat_tek_tek"){
    tekTekModal(kind, `Tebligat onayı — Dosya No: ${(pending.kalem||{}).dosya_no??""}`, tebligatMetni, pending.kalem||{});
  }else if(mod === "tebligat_toplu"){
    const list = pending.kalemler || [];
    topluModal(kind, `Toplu tebligat önizleme — ${list.length} dosya. Gönderilecekleri seçin:`,
      o=>`Dosya ${o.dosya_no} · ${o.alacakli||""} · ${(o.borclular||[]).length} taraf`,
      list, {label:"Hiçbirine Gönderme", fn:()=>sendApproval(kind,"approve",[])});
  }else{
    modalTitle.textContent = "Onay bekleniyor";
    modalBody.textContent = "Bilinmeyen onay türü: " + mod;
    modalFooter.appendChild(btn("Kapat","ghost",()=>sendApproval(kind,"cancel")));
  }
}

// ════════════════ SGK SORGU ════════════════
(function(){
  const panel = document.querySelector('.panel[data-tab="sgk"]');
  if(!panel) return;

  const $ = id => document.getElementById(id);
  const tbody = $("sgk-tbody"), logBox = $("sgk-log"), statusEl = $("sgk-status");
  const fileInput = $("sgk-file"), fileName = $("sgk-file-name");
  const startBtn = $("sgk-start"), retryBtn = $("sgk-retry"),
        pauseBtn = $("sgk-pause"), stopBtn = $("sgk-stop");
  const progEl = $("sgk-progress"), dlTam = $("sgk-dl-tam"), dlOzet = $("sgk-dl-ozet");
  const fCol = $("sgk-filter-col"), fTxt = $("sgk-filter-txt"),
        fOlumlu = $("sgk-only-olumlu"), fInfo = $("sgk-filter-info");

  let KOLONLAR = [];
  const rows = new Map();      // r -> rowObj
  let seq = 0, logN = 0, polling = false, paused = false;

  function setRunning(on){
    startBtn.disabled = on; retryBtn.disabled = on;
    pauseBtn.disabled = !on; stopBtn.disabled = !on;
    if(!on) pauseBtn.textContent = "⏸ Duraklat";
  }
  function logLines(lines){
    if(!lines || !lines.length) return;
    logBox.textContent += lines.join("\n") + "\n";
    logBox.scrollTop = logBox.scrollHeight;
  }
  function seciliCols(){
    return [...panel.querySelectorAll(".sgk-col:checked")].map(c=>c.value);
  }
  function durumClass(d){ return d ? ("r-" + d) : ""; }

  // ── tablo ──
  function renderRow(row){
    rows.set(row.r, row);
    let tr = tbody.querySelector(`tr[data-r="${row.r}"]`);
    if(!tr){
      tr = document.createElement("tr");
      tr.dataset.r = row.r;
      tbody.appendChild(tr);
    }
    tr.className = durumClass(row.durum);
    const cells = [row.no, row.ad, row.birim, row.dosya, ...row.sonuc];
    tr.innerHTML = "";
    cells.forEach((c, i)=>{
      const td = document.createElement("td");
      td.textContent = c == null ? "" : String(c);
      td.title = td.textContent;
      td.dataset.col = i;
      tr.appendChild(td);
    });
    applyFilterRow(tr);
  }
  function fillTable(list){
    tbody.innerHTML = "";
    rows.clear();
    list.forEach(renderRow);
    applyFilter();
  }

  // ── detay ──
  tbody.addEventListener("click", e=>{
    const td = e.target.closest("td"); if(!td) return;
    const tr = td.closest("tr"); const r = +tr.dataset.r;
    const ci = +td.dataset.col;
    const row = rows.get(r); if(!row) return;
    const baslik = KOLONLAR[ci] || "";
    const deger = (ci >= 4) ? (row.tam[ci-4] || "") : td.textContent;
    $("sgk-detail-body").textContent = `【 ${baslik} 】\n\n${deger}`;
  });

  // ── filtre ──
  function rowOlumlu(row){
    const sel = new Set(seciliCols());
    // anahtar sırası sonuç sütun sırasıyla aynı; checkbox value=anahtar
    const cols = [...panel.querySelectorAll(".sgk-col")];
    for(let i=0;i<cols.length;i++){
      if(!sel.has(cols[i].value)) continue;
      const v = String(row.sonuc[i]||"").trim().toLowerCase();
      if(v.startsWith("olumlu")) return true;
    }
    return false;
  }
  function rowVisible(row){
    if(fOlumlu.checked && !rowOlumlu(row)) return false;
    const q = fTxt.value.trim().toLowerCase();
    if(q){
      const col = fCol.value;
      let hay;
      if(col){ const idx = KOLONLAR.indexOf(col);
        hay = String([row.no,row.ad,row.birim,row.dosya,...row.sonuc][idx]||"").toLowerCase(); }
      else hay = [row.no,row.ad,row.birim,row.dosya,...row.sonuc].join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  }
  function applyFilterRow(tr){
    const row = rows.get(+tr.dataset.r);
    tr.style.display = (row && rowVisible(row)) ? "" : "none";
  }
  function applyFilter(){
    let gor = 0;
    rows.forEach((row)=>{
      const tr = tbody.querySelector(`tr[data-r="${row.r}"]`);
      if(!tr) return;
      const v = rowVisible(row);
      tr.style.display = v ? "" : "none";
      if(v) gor++;
    });
    const aktif = fTxt.value.trim() || fOlumlu.checked;
    fInfo.textContent = aktif ? `${gor} / ${rows.size} satır` : "";
  }
  fTxt.addEventListener("input", applyFilter);
  fCol.addEventListener("change", applyFilter);
  fOlumlu.addEventListener("change", applyFilter);

  // ── yükleme ──
  fileInput.addEventListener("change", async ()=>{
    if(!fileInput.files.length) return;
    fileName.textContent = "Yükleniyor…";
    const fd = new FormData(); fd.append("file", fileInput.files[0]);
    const {ok, data} = await postForm("/api/sgk/upload", fd);
    if(!ok){ fileName.textContent = "Hata!"; logLines([`[HATA] ${data.error||"Yükleme başarısız"}`]); return; }
    KOLONLAR = data.kolonlar;
    fileName.textContent = `${data.filename} — ${data.rows.length} satır`;
    seq = 0; logN = 0;
    fillTable(data.rows);
    startBtn.disabled = false; retryBtn.disabled = false;
    kick();   // yükleme günlüğünü çek (tek atış; çalışmıyorsa kendi durur)
    fileInput.value = "";
  });

  function kick(){ if(!polling){ polling = true; pollSgk(); } }

  // ── başlat / kontrol ──
  async function start(mode){
    const secili = seciliCols();
    if(!secili.length){ alert("En az bir sorgu sütunu seçin."); return; }
    const {ok, data} = await postJSON("/api/sgk/start", {mode, secili});
    if(!ok){ logLines([`[HATA] ${data.error||"Başlatılamadı"}`]); return; }
    setRunning(true);
    dlTam.style.display = dlOzet.style.display = "none";
    kick();
  }
  startBtn.addEventListener("click", ()=>start("normal"));
  retryBtn.addEventListener("click", ()=>start("retry"));
  pauseBtn.addEventListener("click", async ()=>{
    paused = !paused;
    await postJSON("/api/sgk/pause", {paused});
    pauseBtn.textContent = paused ? "▶ Devam" : "⏸ Duraklat";
  });
  stopBtn.addEventListener("click", ()=>postJSON("/api/sgk/stop"));

  // ── poll (yalnızca çalışırken devam eder) ──
  async function pollSgk(){
    const {ok, data} = await getJSON(`/api/sgk/status?seq=${seq}&log=${logN}`);
    let running = false;
    if(ok && !data.error){
      seq = data.seq; logN = data.total_logs;
      logLines(data.logs);
      (data.updates||[]).forEach(renderRow);
      if(data.updates && data.updates.length) applyFilter();
      statusEl.textContent = data.status || "";
      const p = data.progress || {};
      progEl.textContent = p.total ? `${p.done||0}/${p.total} satır` : "";
      paused = data.paused;
      pauseBtn.textContent = paused ? "▶ Devam" : "⏸ Duraklat";
      setRunning(data.running);
      running = data.running;
      if(data.bitti) dlTam.style.display = dlOzet.style.display = "";
    }
    if(running){ setTimeout(pollSgk, 900); }
    else { polling = false; }
  }
})();
