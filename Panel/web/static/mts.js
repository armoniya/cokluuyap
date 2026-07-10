// MTS Takip Açma — masaüstü MtsTakipPanel'in web eşi.
// İŞ MANTIĞI sunucudadır: kaynak ayrıştırma uyap_core.mts.parse, açma işi is_kuyrugu
// üzerinden ofise (127.0.0.1:8800 iş kuyruğu) gönderilir. Burada yalnızca sunum +
// ilerleme/onay akışı vardır; tarayıcı/Playwright YOK, yeni iş mantığı YOK.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var fileInput = $("mts-file");
  if (!fileInput) return;

  var takipler = [], alacaklilar = [];
  var vekaletMap = {}, dayanakMap = {}, secili = {}, durum = {}, hataMesaj = {};
  var seciliDosyaNo = null, logSayac = 0, running = false, onayAktif = false, polling = false;

  function log(line) {
    var el = $("mts-log");
    el.appendChild(document.createTextNode(line + "\n"));
    el.scrollTop = el.scrollHeight;
  }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
  }
  function readB64(file) {
    return new Promise(function (res, rej) {
      var rd = new FileReader();
      rd.onload = function () { res(rd.result.split(",")[1] || ""); };
      rd.onerror = rej;
      rd.readAsDataURL(file);
    });
  }
  function takipBul(dn) {
    for (var i = 0; i < takipler.length; i++) if (String(takipler[i].dosya_no) === String(dn)) return takipler[i];
    return null;
  }
  function borcluMetni(t, kisa) {
    var adlar = (t.borclular || []).map(function (b) {
      return (((b.ad || "") + " " + (b.soyad || "")).trim()) || (b.kimlik || "?");
    });
    if (!adlar.length) return "(borçlu yok)";
    if (kisa && adlar.length > 2) return adlar[0] + ", " + adlar[1] + " +" + (adlar.length - 2);
    return adlar.join(", ");
  }

  // ── bağlantı kontrol ──
  function connCheck() {
    fetch("api/mts/conn").then(function (r) { return r.json(); }).then(function (d) {
      var el = $("mts-conn");
      if (d.ok) { el.textContent = "● Ofis bağlantısı hazır (iş kuyruğu erişilebilir)."; el.style.color = ""; }
      else { el.textContent = "● Ofis bağlantısı yok — UYAP Bağlantısı (Paylaş/Al) başlatın."; el.style.color = "var(--clay)"; }
    }).catch(function () {});
  }

  // ── kaynak seç → ayrıştır ──
  fileInput.addEventListener("change", function () {
    var f = fileInput.files[0];
    if (!f) return;
    $("mts-kaynak").textContent = "Ayrıştırılıyor…";
    readB64(f).then(function (b64) {
      return post("api/mts/parse", { filename: f.name, data_b64: b64 });
    }).then(function (d) {
      if (!d.ok) { $("mts-kaynak").textContent = d.msg || "Ayrıştırma hatası."; log("❌ " + (d.msg || "")); return; }
      takipler = d.takipler || []; alacaklilar = d.alacaklilar || [];
      vekaletMap = {}; dayanakMap = {}; durum = {}; hataMesaj = {}; secili = {};
      takipler.forEach(function (t) { secili[String(t.dosya_no)] = true; });   // varsayılan: hepsi
      $("mts-kaynak").textContent = d.filename;
      $("mts-ozet").textContent = takipler.length + " takip · " + alacaklilar.length + " alacaklı";
      log("✓ " + takipler.length + " takip ayrıştırıldı (" + d.filename + ").");
      cizListeler(); butonGuncelle();
    }).catch(function (e) { $("mts-kaynak").textContent = "Hata."; log("❌ " + e); });
    fileInput.value = "";
  });

  $("mts-kaldir").addEventListener("click", function () {
    if (running) return;
    post("api/mts/clear", {});
    takipler = []; alacaklilar = []; vekaletMap = {}; dayanakMap = {}; secili = {}; durum = {}; hataMesaj = {};
    $("mts-kaynak").textContent = "Henüz dosya seçilmedi."; $("mts-ozet").textContent = "";
    $("mts-dayanak").textContent = ""; seciliDosyaNo = null;
    cizListeler(); detayGoster(null); butonGuncelle();
  });

  // ── vekalet ata (alacaklı satırına tıkla) ──
  var vekInput = document.createElement("input");
  vekInput.type = "file"; vekInput.accept = ".udf,.pdf,.xml"; vekInput.hidden = true;
  document.body.appendChild(vekInput);
  var vekAlacakli = null;
  vekInput.addEventListener("change", function () {
    var f = vekInput.files[0];
    if (!f || !vekAlacakli) return;
    var al = vekAlacakli;
    readB64(f).then(function (b64) {
      return post("api/mts/vekalet", { alacakli: al, filename: f.name, data_b64: b64 });
    }).then(function (d) {
      if (!d.ok) { log("❌ " + (d.msg || "vekalet")); return; }
      vekaletMap[al] = d.filename;
      log("📎 " + al + " → " + d.filename);
      cizAlacakli();
    });
    vekInput.value = "";
  });

  // ── dayanak PDF'leri ──
  $("mts-dayanak-file").addEventListener("change", function () {
    var fs = Array.prototype.slice.call(this.files);
    if (!fs.length) return;
    $("mts-dayanak").textContent = "PDF'ler taranıyor…";
    log("📁 " + fs.length + " PDF taranıyor…");
    Promise.all(fs.map(function (f) {
      return readB64(f).then(function (b64) { return { filename: f.name, data_b64: b64 }; });
    })).then(function (files) {
      return post("api/mts/dayanak", { files: files });
    }).then(function (d) {
      if (!d.ok) { $("mts-dayanak").textContent = d.msg || "Tarama hatası."; log("❌ " + (d.msg || "")); return; }
      dayanakMap = d.matched || {};
      var n = Object.keys(dayanakMap).length;
      $("mts-dayanak").textContent = n + "/" + (d.total || takipler.length) + " eşleşti";
      log("📁 Dayanak: " + n + "/" + (d.total || takipler.length) + " takip eşleşti." + (d.msg ? " " + d.msg : ""));
      cizAlacakli();
    }).catch(function (e) { $("mts-dayanak").textContent = "Hata."; log("❌ " + e); });
    this.value = "";
  });

  // ── liste çizimleri ──
  function cizListeler() { cizAlacakli(); cizBekleyen(); cizAcilan(); }

  function cizAlacakli() {
    var tb = $("mts-alacakli"); tb.innerHTML = "";
    var sayim = {};
    takipler.forEach(function (t) { sayim[t.alacakli] = (sayim[t.alacakli] || 0) + 1; });
    alacaklilar.forEach(function (al) {
      var vk = vekaletMap[al] ? "✓ " + vekaletMap[al] : "— seçilmedi";
      var daySay = takipler.filter(function (t) { return t.alacakli === al && dayanakMap[String(t.dosya_no)]; }).length;
      var tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = "<td></td><td></td><td></td><td></td>";
      tr.children[0].textContent = al; tr.children[1].textContent = sayim[al] || 0;
      tr.children[2].textContent = vk; tr.children[3].textContent = daySay ? "✓ " + daySay : "—";
      tr.addEventListener("click", function () {
        if (running) return; vekAlacakli = al; vekInput.click();
      });
      tb.appendChild(tr);
    });
  }

  function cizBekleyen() {
    var tb = $("mts-bekleyen"); tb.innerHTML = "";
    var bekleyen = takipler.filter(function (t) {
      var d = durum[String(t.dosya_no)] || "bekleyen"; return d === "bekleyen" || d === "aktif";
    });
    var secSay = 0;
    bekleyen.forEach(function (t) {
      var dn = String(t.dosya_no);
      var isaretli = !!secili[dn]; if (isaretli) secSay++;
      var tr = document.createElement("tr");
      if (durum[dn] === "aktif") tr.style.color = "var(--clay)";
      tr.innerHTML = "<td style='text-align:center;cursor:pointer'></td><td style='cursor:pointer'></td>";
      tr.children[0].textContent = isaretli ? "☑" : "☐";
      tr.children[1].textContent = " " + borcluMetni(t, true) + "  ·  Dosya " + dn;
      tr.children[0].addEventListener("click", function (e) {
        e.stopPropagation(); if (running) return;
        secili[dn] = !secili[dn]; cizBekleyen(); butonGuncelle();
      });
      tr.children[1].addEventListener("click", function () { seciliDosyaNo = dn; detayGoster(dn); });
      tb.appendChild(tr);
    });
    $("mts-bekleyen-baslik").textContent = "⏳ Bekleyen (" + bekleyen.length + ") — ☑ " + secSay;
  }

  function cizAcilan() {
    var tb = $("mts-acilan"); tb.innerHTML = "";
    var rozet = { tamam: "✓ Açıldı", hata: "✗ Hata", atlandi: "⤼ Atlandı" };
    var nTamam = 0, nHata = 0;
    takipler.forEach(function (t) {
      var dn = String(t.dosya_no), d = durum[dn];
      if (d !== "tamam" && d !== "hata" && d !== "atlandi") return;
      if (d === "tamam") nTamam++; else if (d === "hata") nHata++;
      var tr = document.createElement("tr");
      tr.style.cursor = "pointer";
      tr.innerHTML = "<td></td><td></td>";
      tr.children[0].textContent = rozet[d] || d;
      tr.children[1].textContent = borcluMetni(t, true) + " · Dosya " + dn;
      if (d === "hata") tr.style.color = "var(--clay)";
      tr.addEventListener("click", function () { seciliDosyaNo = dn; detayGoster(dn); });
      tb.appendChild(tr);
    });
    var bas = "✓ Açılan (" + nTamam + ")"; if (nHata) bas += " — ⚠ " + nHata + " hata";
    $("mts-acilan-baslik").textContent = bas;
  }

  function detayGoster(dn) {
    var bt = $("mts-borclu"), kt = $("mts-kalem");
    bt.innerHTML = ""; kt.innerHTML = "";
    var t = dn ? takipBul(dn) : null;
    if (!t) {
      $("mts-detay-baslik").textContent = "Takip Ayrıntısı";
      $("mts-detay-bilgi").textContent = "Bir takip seçin."; return;
    }
    var rozet = { bekleyen: "⏳ Bekliyor", aktif: "▶ Açılıyor", tamam: "✓ Açıldı",
                  hata: "✗ Hata", atlandi: "⤼ Atlandı" }[durum[String(dn)] || "bekleyen"] || "";
    $("mts-detay-baslik").textContent = "Takip Ayrıntısı — Dosya " + dn + "   " + rozet;
    var vk = vekaletMap[t.alacakli] ? "📎 " + vekaletMap[t.alacakli] : "📎 vekalet seçilmedi";
    var bilgi = "Borçlu(lar): " + borcluMetni(t) + "\n" +
      "Alacaklı: " + (t.alacakli || "") + "        Vekalet: " + vk + "\n" +
      "IBAN: " + (t.iban || "-") + "        Abone No: " + (t.abone_no || t.hizmet_abone_no || "-") +
      "        İlamsız Tutar: " + (t.ilamsiz_tutar || "-") + "\n" +
      "Talep: " + (t.aciklama || "-");
    if (durum[String(dn)] === "hata" && hataMesaj[String(dn)]) bilgi += "\n\n⚠ Hata nedeni: " + hataMesaj[String(dn)];
    $("mts-detay-bilgi").textContent = bilgi;
    (t.borclular || []).forEach(function (b) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td></td><td></td><td></td>";
      tr.children[0].textContent = b.ad || ""; tr.children[1].textContent = b.soyad || "";
      tr.children[2].textContent = b.kimlik || ""; bt.appendChild(tr);
    });
    (t.alacak_kalemleri || []).forEach(function (k) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td></td><td></td><td></td><td></td>";
      tr.children[0].textContent = k.ad || ""; tr.children[1].textContent = k.tutar || "";
      tr.children[2].textContent = k.faiz_oran || ""; tr.children[3].textContent = k.faiz_tur || "";
      kt.appendChild(tr);
    });
  }

  // ── seçim butonları ──
  $("mts-hepsi").addEventListener("click", function () {
    if (running) return;
    takipler.forEach(function (t) {
      var d = durum[String(t.dosya_no)] || "bekleyen";
      if (d === "bekleyen" || d === "aktif") secili[String(t.dosya_no)] = true;
    });
    cizBekleyen(); butonGuncelle();
  });
  $("mts-hicbiri").addEventListener("click", function () {
    if (running) return; secili = {}; cizBekleyen(); butonGuncelle();
  });

  function butonGuncelle() {
    var secVar = Object.keys(secili).some(function (k) { return secili[k]; });
    $("mts-baslat").disabled = running || !takipler.length || !secVar;
    $("mts-durdur").disabled = !running;
    $("mts-kaldir").disabled = running;
  }

  function mod() {
    var el = document.querySelector('input[name="mts-mod"]:checked');
    return el ? el.value : "yok";
  }

  // ── başlat ──
  $("mts-baslat").addEventListener("click", function () {
    if (running || !takipler.length) return;
    var seciliList = takipler.filter(function (t) {
      return secili[String(t.dosya_no)] && (durum[String(t.dosya_no)] || "bekleyen") === "bekleyen";
    }).map(function (t) { return String(t.dosya_no); });
    if (!seciliList.length) { log("Açılacak takip işaretlenmedi."); return; }
    $("mts-durum").textContent = "İş gönderiliyor…";
    log("\n=== MTS ÇOKLU TAKİP AÇMA (" + seciliList.length + " takip) ===");
    post("api/mts/start", {
      il: $("mts-il").value.trim(), adliye: $("mts-adliye").value.trim(),
      onay_modu: mod(), secili: seciliList
    }).then(function (d) {
      if (!d.ok) { $("mts-durum").textContent = "Başlatılamadı."; log("❌ " + (d.msg || "")); return; }
      running = true; logSayac = 0; $("mts-durum").textContent = "Çalışıyor…";
      log("İş kuyruğuna alındı (id=" + d.job_id + ").");
      butonGuncelle(); ensurePoll();
    }).catch(function (e) { $("mts-durum").textContent = "Hata."; log("❌ " + e); });
  });

  $("mts-durdur").addEventListener("click", function () {
    if (!running) return; $("mts-durum").textContent = "Durduruluyor…"; post("api/mts/cancel", {});
  });

  // ── durum polling ──
  function ensurePoll() { if (!polling) { polling = true; poll(); } }
  function poll() {
    fetch("api/mts/status").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.loaded) { polling = false; return; }
        if (s.error) { log("⚠️ Durum alınamadı: " + s.error); setTimeout(poll, 2000); return; }
        var job = s.job || {};
        var loglar = job.logs || [];
        for (var i = logSayac; i < loglar.length; i++) log(loglar[i].line || "");
        logSayac = loglar.length;
        var prog = job.progress || {};
        if (prog.message) $("mts-durum").textContent = prog.message;
        var sonuc = job.result || {};
        (sonuc.sonuclar || []).forEach(function (x) {
          var dn = String(x.dosya_no);
          var d = { "tamam": "tamam", "hata": "hata", "atlandı": "atlandi", "atlandi": "atlandi" }[x.durum];
          if (d) { durum[dn] = d; if (x.mesaj) hataMesaj[dn] = x.mesaj; }
        });
        var status = job.status;
        if (status === "awaiting_approval" && !onayAktif) onayGoster(job.pending_approval || {});
        cizBekleyen(); cizAcilan();
        if (status === "done" || status === "error" || status === "cancelled") { isBitti(job); return; }
        setTimeout(poll, 900);
      }).catch(function () { polling = false; });
  }

  function isBitti(job) {
    running = false; polling = false; onayGizle();
    var status = job.status, sonuc = job.result || {};
    if (status === "done") {
      $("mts-durum").textContent = "Bitti: " + (sonuc.basari || 0) + " tamam, " +
        (sonuc.atlanan || 0) + " atlandı, " + (sonuc.hata || 0) + " hata.";
      log("✓ İş tamamlandı.");
    } else if (status === "cancelled") { $("mts-durum").textContent = "Durduruldu."; log("⏹ İş durduruldu."); }
    else { $("mts-durum").textContent = "Hata."; log("❌ İş hatası: " + (job.error || "bilinmeyen")); }
    butonGuncelle();
  }

  // ── onay çubuğu ──
  function ozetMetni(o) {
    var borc = (o.borclular || []).map(function (b) { return ((b.ad || "") + " " + (b.soyad || "")).trim(); }).join(", ") || "-";
    var harc = (o.harclar || []).map(function (h) { return h.ad + ": " + h.miktar; }).join("; ");
    return "Dosya " + o.dosya_no + " · " + (o.alacakli || "") + "\nBorçlu: " + borc +
      "  ·  Toplam: " + (o.toplam || "-") + " TL" + (harc ? "\nMasraf: " + harc : "");
  }
  function onayBtn(metin, fn) {
    var b = document.createElement("button"); b.className = "conn-btn inline"; b.textContent = metin;
    b.addEventListener("click", fn); $("mts-onay-btns").appendChild(b);
  }
  function onayGoster(pending) {
    onayAktif = true;
    $("mts-onay-btns").innerHTML = "";
    var modp = pending.mod;
    if (modp === "tek_tek") {
      $("mts-onay-mesaj").textContent = ozetMetni(pending.takip || {}) + "\nBu takip açılsın mı?";
      onayBtn("✓ Onayla", function () { onayVer({ decision: "approve" }); });
      onayBtn("⤼ Atla", function () { onayVer({ decision: "skip" }); });
      onayBtn("⏹ Durdur", function () { onayVer({ decision: "cancel" }); });
    } else if (modp === "toplu") {
      var n = (pending.takipler || []).length;
      $("mts-onay-mesaj").textContent = n + " takip hazırlandı. Bekleyen listede ☑ işaretlediklerinizi açmak için Onayla'ya basın.";
      onayBtn("✓ Seçilenleri Aç", function () {
        var sel = Object.keys(secili).filter(function (k) { return secili[k]; });
        onayVer({ selection: sel });
      });
      onayBtn("⏹ Durdur", function () { onayVer({ decision: "cancel" }); });
    } else { onayVer({ decision: "approve" }); return; }
    $("mts-onay").hidden = false;
  }
  function onayGizle() { onayAktif = false; $("mts-onay").hidden = true; }
  function onayVer(karar) {
    onayGizle(); $("mts-durum").textContent = "Onay gönderiliyor…";
    post("api/mts/approve", { karar: karar }).then(function (d) {
      if (!d.ok) log("⚠️ Onay gönderilemedi: " + (d.msg || ""));
    });
  }

  // ── açılış ──
  connCheck();
  setInterval(connCheck, 8000);
  butonGuncelle();
  // Sayfa yenilenirse süren işi yakala.
  fetch("api/mts/status").then(function (r) { return r.json(); }).then(function (s) {
    if (s && s.loaded && s.job && (s.job.status === "running" || s.job.status === "awaiting_approval")) {
      running = true; ensurePoll();
    }
  }).catch(function () {});
})();
