// Barkod Sorgu (Kapalı Tebligat — PTT) — masaüstü BarkodSorguPanel'in web eşi.
// İki bölüm: (1) yerel DB'deki İcra dosyalarını filtrelenip sıralanabilir bir
// tabloda listeler ("Dosyalarım (Tümü)" ile AYNI /api/dosyalarim/* uçları,
// yalnız yargi_turu=İcra ile daraltılmış — bkz. dosyalarim_genel.js'teki AYNI
// desen); kullanıcı satır(lar) seçip "Seçilenleri Sorgula" ile barkod_sorgu'yu
// tetikler (uzun sürebileceğinden dosyalarim_genel'in "Yenile"siyle AYNI
// job-token+poll deseni). (2) DB'ye daha önce yazılmış TebligatBarkod
// sonuçlarının geçmiş raporu.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var filtreleBtn = $("bk-filtrele");
  if (!filtreleBtn) return;
  var temizleBtn = $("bk-temizle"), sorgulaBtn = $("bk-sorgula");
  var pauseBtn = $("bk-pause"), stopBtn = $("bk-stop");
  var statusEl = $("bk-status");
  var birimSel = $("bk-birim"), durumSel = $("bk-durum");
  var tarihBas = $("bk-tarih-bas"), tarihBit = $("bk-tarih-bit");
  var alacakliAdi = $("bk-alacakli"), borcluAdi = $("bk-borclu");
  var alacakliList = $("bk-alacakli-list"), borcluList = $("bk-borclu-list");
  var headEl = $("bk-head"), filterRowEl = $("bk-filter-row"), bodyEl = $("bk-body"), logEl = $("bk-log");
  var gecmisYenileBtn = $("bk-gecmis-yenile"), gecmisStatusEl = $("bk-gecmis-status");
  var gecmisHeadEl = $("bk-gecmis-head"), gecmisBodyEl = $("bk-gecmis-body");

  var KOLONLAR = [
    ["birimAdi", "İcra Dairesi"], ["dosyaNo", "Dosya No"], ["dosyaTur", "Dosya Türü"],
    ["dosyaDurum", "Durum"], ["acilisTarihi", "Açılış Tarihi"],
    ["alacakli", "Alacaklı"], ["borclu", "Borçlu"]
  ];
  var GECMIS_KOLONLAR = [
    ["birimAdi", "İcra Dairesi"], ["dosyaNo", "Dosya No"], ["barkod", "Barkod"],
    ["elektronikTebligat", "Elektronik mi"], ["pttDurumu", "PTT Durumu"],
    ["sonIslemTarihi", "Son İşlem Tarihi"], ["tebligMazbatasiVar", "Tebliğ Mazbatası"],
    ["kapaliTebligMazbatasiVar", "Kapalı Mazbata"], ["sorguZamani", "Sorgu Zamanı"]
  ];

  var icraKod = null;               // yargı türü kodu ("İcra") — loadFields'ten
  var birimKodMap = {};
  var durumKod = {};
  var kayitlarHam = [];             // sunucudan gelen ham kayıtlar (DB filtreleri uygulanmış)
  var selected = {};                // hamIndex -> true (filtre/sıralamadan bağımsız kalıcı)
  var colFilters = {}, colHeadEls = {}, sortKey = null, sortReverse = false;
  var calisiyor = false, sorguSinceLog = 0, sorguPaused = false;

  var gecmisHam = [], gecmisSortKey = null, gecmisSortReverse = false;

  function trLower(s) {
    s = s == null ? "" : String(s);
    var map = { "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
                "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c" };
    return s.replace(/[İIıŞşĞğÜüÖöÇç]/g, function (c) { return map[c]; }).toLowerCase();
  }

  function log(line) {
    logEl.appendChild(document.createTextNode(line + "\n"));
    logEl.scrollTop = logEl.scrollHeight;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function fillSelect(sel, kodMap, secenekler) {
    sel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; sel.appendChild(o0);
    (secenekler || []).forEach(function (s) {
      kodMap[s.ad] = s.kod;
      var o = document.createElement("option"); o.textContent = s.ad; sel.appendChild(o);
    });
  }

  function fillDatalist(dl, degerler) {
    dl.innerHTML = "";
    (degerler || []).forEach(function (v) {
      var o = document.createElement("option"); o.value = v; dl.appendChild(o);
    });
  }

  // ── Bölüm 1: başlık şeridi (canlı filtre + sıralama) — dosyalarim_genel.js ile AYNI desen ──
  function buildHead() {
    headEl.innerHTML = ""; filterRowEl.innerHTML = ""; colFilters = {}; colHeadEls = {};

    var thSec = document.createElement("th");
    var allCb = document.createElement("input");
    allCb.type = "checkbox";
    allCb.title = "Görünenlerin tümünü seç/kaldır";
    allCb.addEventListener("change", function () {
      visibleRows().forEach(function (pair) { selected[pair[0]] = allCb.checked; });
      renderRows();
    });
    thSec.appendChild(allCb);
    headEl.appendChild(thSec);
    filterRowEl.appendChild(document.createElement("th"));

    KOLONLAR.forEach(function (c) {
      var th = document.createElement("th");
      th.textContent = c[1];
      th.style.cursor = "pointer";
      th.title = "Sıralamak için tıklayın";
      th.addEventListener("click", function () { sirala(c[0]); });
      colHeadEls[c[0]] = th;
      headEl.appendChild(th);

      var ftd = document.createElement("th");
      var inp = document.createElement("input");
      inp.type = "text"; inp.className = "icra-colbox"; inp.placeholder = "süz / ara";
      inp.addEventListener("input", renderRows);
      colFilters[c[0]] = inp;
      ftd.appendChild(inp); filterRowEl.appendChild(ftd);
    });
    sortEtiketleriGuncelle();
  }

  function sortEtiketleriGuncelle() {
    KOLONLAR.forEach(function (c) {
      var th = colHeadEls[c[0]];
      if (!th) return;
      var ok = "";
      if (c[0] === sortKey) ok = sortReverse ? "  ▼" : "  ▲";
      th.textContent = c[1] + ok;
    });
  }

  function sirala(key) {
    if (sortKey === key) sortReverse = !sortReverse;
    else { sortKey = key; sortReverse = false; }
    sortEtiketleriGuncelle();
    renderRows();
  }

  function visibleRows() {
    var kriter = {};
    KOLONLAR.forEach(function (c) {
      var inp = colFilters[c[0]];
      var q = inp ? trLower(inp.value.trim()) : "";
      if (q) kriter[c[0]] = q;
    });
    var out = kayitlarHam.map(function (rec, i) { return [i, rec]; });
    var keys = Object.keys(kriter);
    if (keys.length) {
      out = out.filter(function (pair) {
        var rec = pair[1];
        return keys.every(function (k) {
          return trLower(rec[k] == null ? "" : String(rec[k])).indexOf(kriter[k]) >= 0;
        });
      });
    }
    if (sortKey) out = siraliDiz(out, sortKey, sortReverse);
    return out;
  }

  function siraliDiz(pairs, key, reverse) {
    var gecerliler = [], gecersizler = [];
    var sayiRe = /^-?\d+([.,]\d+)?$/;
    pairs.forEach(function (pair) {
      var v = pair[1][key];
      var s = v == null ? "" : String(v);
      if (key === "acilisTarihi") {
        var ta = tarihAnahtar(s);
        if (ta === null) gecersizler.push(pair);
        else gecerliler.push([ta, pair]);
        return;
      }
      if (key === "sonIslemTarihi") {
        var tsa = tarihSaatAnahtar(s);
        if (tsa === null) gecersizler.push(pair);
        else gecerliler.push([tsa, pair]);
        return;
      }
      var t = s.trim();
      if (!t) { gecersizler.push(pair); return; }
      if (sayiRe.test(t)) gecerliler.push([[0, parseFloat(t.replace(",", "."))], pair]);
      else gecerliler.push([[1, trLower(t)], pair]);
    });
    gecerliler.sort(function (a, b) {
      var ka = a[0], kb = b[0], cmp;
      if (key === "acilisTarihi" || key === "sonIslemTarihi") {
        cmp = ka < kb ? -1 : (ka > kb ? 1 : 0);
      } else if (ka[0] !== kb[0]) {
        cmp = ka[0] - kb[0];
      } else if (ka[0] === 0) {
        cmp = ka[1] - kb[1];
      } else {
        cmp = ka[1].localeCompare(kb[1]);
      }
      return reverse ? -cmp : cmp;
    });
    return gecerliler.map(function (p) { return p[1]; }).concat(gecersizler);
  }

  function tarihAnahtar(s) {
    var m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(String(s).trim());
    return m ? (m[3] + m[2] + m[1]) : null;
  }

  // 'GG/AA/YYYY[ SS:DD]' ya da 'GG.AA.YYYY[ SS:DD]' -> sıralanabilir
  // 'YYYYAAGGSSDD'. barkod_sorgu.py'nin ürettiği "Tebliğ/İade Tarihi"/"Son
  // İşlem Tarihi"/"Sorgu Zamanı" sütunları GG/AA/YYYY (gün önce) biçiminde
  // geldiğinden düz metin sıralaması AY'ı yok sayıyordu (kullanıcı bulgusu,
  // 2026-08-03: eskiden-yeniye sıralamada '01/07', '25/02'den önceymiş gibi
  // görünüyordu). `exec` (anchor'sız) kullanır — sondaki fazladan metni de
  // tolere eder; Panel/modules/barkod_sorgu_panel.py `_tarih_saat_anahtari`
  // ile AYNI mantık.
  function tarihSaatAnahtar(s) {
    var m = /(\d{2})[./](\d{2})[./](\d{4})(?:[ T](\d{2}):(\d{2}))?/.exec(String(s || ""));
    if (!m) return null;
    return m[3] + m[2] + m[1] + (m[4] || "00") + (m[5] || "00");
  }

  function renderRows() {
    var gorunen = visibleRows();
    bodyEl.innerHTML = "";
    gorunen.forEach(function (pair) {
      var idx = pair[0], rec = pair[1];
      var tr = document.createElement("tr");
      var tdCb = document.createElement("td");
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = !!selected[idx];
      cb.addEventListener("click", function (e) { e.stopPropagation(); });
      cb.addEventListener("change", function () { selected[idx] = cb.checked; });
      tdCb.appendChild(cb);
      tr.appendChild(tdCb);
      KOLONLAR.forEach(function (c) {
        var td = document.createElement("td");
        td.textContent = rec[c[0]] == null ? "" : String(rec[c[0]]);
        tr.appendChild(td);
      });
      bodyEl.appendChild(tr);
    });
    if (!kayitlarHam.length) statusEl.textContent = "";
    else if (gorunen.length !== kayitlarHam.length) statusEl.textContent = gorunen.length + " / " + kayitlarHam.length + " dosya";
    else statusEl.textContent = kayitlarHam.length + " dosya";
  }

  function yerelFiltreleriSifirla() {
    KOLONLAR.forEach(function (c) { if (colFilters[c[0]]) colFilters[c[0]].value = ""; });
    sortKey = null; sortReverse = false;
    sortEtiketleriGuncelle();
  }

  function loadFields() {
    return fetch("api/dosyalarim/fields").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) { log("[HATA] Dosyalarım modülü hazır değil: " + (d && d.err || "")); return; }
      var icra = (d.yargi_turleri || []).filter(function (t) { return t.ad === "İcra"; })[0];
      icraKod = icra ? icra.kod : null;
      fillSelect(durumSel, durumKod, d.durumlar);
      return birimYukle();
    }).catch(function () {});
  }

  function birimYukle() {
    birimKodMap = {};
    birimSel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; birimSel.appendChild(o0);
    if (icraKod == null) return Promise.resolve();
    return fetch("api/dosyalarim/birimler?yargi_turu=" + encodeURIComponent(icraKod))
      .then(function (r) { return r.json(); }).then(function (d) {
        (d.birimler || []).forEach(function (b) {
          birimKodMap[b.ad || b.kod] = b.kod;
          var o = document.createElement("option"); o.textContent = b.ad || b.kod; birimSel.appendChild(o);
        });
      }).catch(function () {});
  }

  function taraflarSecenekleriYukle() {
    fetch("api/dosyalarim/taraf-secenekleri").then(function (r) { return r.json(); }).then(function (d) {
      fillDatalist(alacakliList, d.alacaklilar);
      fillDatalist(borcluList, d.borclular);
    }).catch(function () {});
  }

  function filtreleriTopla() {
    var body = { yargi_turu: icraKod };
    if (birimSel.value !== "Tümü") body.mahkeme_id = birimKodMap[birimSel.value];
    if (durumSel.value !== "Tümü") body.durum_kod = durumKod[durumSel.value];
    if (tarihBas.value.trim()) body.tarih_baslangic = tarihBas.value.trim();
    if (tarihBit.value.trim()) body.tarih_bitis = tarihBit.value.trim();
    if (alacakliAdi.value.trim()) body.alacakli_adi = alacakliAdi.value.trim();
    if (borcluAdi.value.trim()) body.borclu_adi = borcluAdi.value.trim();
    return body;
  }

  function filtrele() {
    statusEl.textContent = "Yükleniyor…";
    post("api/dosyalarim/list", filtreleriTopla()).then(function (d) {
      if (!d.ok) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); return; }
      kayitlarHam = d.kayitlar || []; selected = {}; renderRows();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function temizle() {
    birimSel.value = "Tümü"; durumSel.value = "Tümü";
    tarihBas.value = ""; tarihBit.value = "";
    alacakliAdi.value = ""; borcluAdi.value = "";
    yerelFiltreleriSifirla();
    filtrele();
  }

  function sorguControls() {
    sorgulaBtn.disabled = calisiyor;
    if (pauseBtn) { pauseBtn.disabled = !calisiyor; pauseBtn.textContent = sorguPaused ? "Devam" : "Duraklat"; }
    if (stopBtn) stopBtn.disabled = !calisiyor;
  }

  // ── Seçilenleri Sorgula (job-token + poll — dosyalarim_genel'in "Yenile"siyle AYNI desen) ──
  function secilenleriSorgula() {
    if (calisiyor) return;
    var secili = Object.keys(selected).filter(function (i) { return selected[i]; })
      .map(function (i) { return kayitlarHam[+i]; });
    if (!secili.length) { statusEl.textContent = "Önce listeden en az bir dosya seçin."; return; }

    statusEl.textContent = secili.length + " dosya sorgulanıyor…";
    log("▶ " + secili.length + " seçili dosya için barkod sorgusu başlıyor…");
    window.topluIs.baslat(function (extra) {
      var b = { secili: secili }; for (var k in extra) b[k] = extra[k];
      return post("api/barkod/sorgula", b);
    }, function (t) { statusEl.textContent = t; }).then(function (d) {
      if (!d.ok) { if (!d.cakisma) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); } return; }
      calisiyor = true; sorguControls();
      sorguSinceLog = 0; sorguPoll();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function sorguDuraklatToggle() {
    if (!calisiyor) return;
    post("api/barkod/sorgula-duraklat").then(function (d) { sorguPaused = !!d.paused; sorguControls(); });
  }
  function sorguDurdur() {
    if (calisiyor) post("api/barkod/sorgula-durdur");
  }
  if (pauseBtn) pauseBtn.addEventListener("click", sorguDuraklatToggle);
  if (stopBtn) stopBtn.addEventListener("click", sorguDurdur);

  function sorguPoll() {
    fetch("api/barkod/sorgula-durum?log=" + sorguSinceLog)
      .then(function (r) { return r.json(); }).then(function (s) {
        if (!s || !s.loaded) { calisiyor = false; sorguControls(); return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); sorguSinceLog = s.log_n; }
        sorguPaused = !!s.paused; sorguControls();
        if (s.running) { setTimeout(sorguPoll, 1000); return; }
        calisiyor = false; sorguControls();
        if (s.sonuc && s.sonuc.hata) { statusEl.textContent = ""; log("[HATA] " + s.sonuc.hata); }
        else if (s.sonuc) {
          statusEl.textContent = "✔ Sorgu tamamlandı (" + (s.sonuc.n || 0) + " satır sonuç).";
          log("✅ Sorgu tamamlandı — " + (s.sonuc.n || 0) + " satır sonuç.");
          gecmisYukle();   // yeni yazılan sonuçlar geçmiş bölümüne yansısın
        }
      }).catch(function () { calisiyor = false; sorguControls(); });
  }

  // ── Bölüm 2: geçmiş sonuçlar ──
  function gecmisBuildHead() {
    gecmisHeadEl.innerHTML = "";
    GECMIS_KOLONLAR.forEach(function (c) {
      var th = document.createElement("th");
      th.style.cursor = "pointer";
      th.title = "Sıralamak için tıklayın";
      th.addEventListener("click", function () { gecmisSirala(c[0]); });
      gecmisHeadEl.appendChild(th);
    });
    gecmisSortEtiketleriGuncelle();
  }

  function gecmisSortEtiketleriGuncelle() {
    var ths = gecmisHeadEl.querySelectorAll("th");
    GECMIS_KOLONLAR.forEach(function (c, i) {
      var ok = "";
      if (c[0] === gecmisSortKey) ok = gecmisSortReverse ? "  ▼" : "  ▲";
      if (ths[i]) ths[i].textContent = c[1] + ok;
    });
  }

  function gecmisSirala(key) {
    if (gecmisSortKey === key) gecmisSortReverse = !gecmisSortReverse;
    else { gecmisSortKey = key; gecmisSortReverse = false; }
    gecmisSortEtiketleriGuncelle();
    gecmisRenderRows();
  }

  function gecmisRenderRows() {
    var kayitlar = gecmisHam.slice();
    if (gecmisSortKey) {
      var key = gecmisSortKey;
      kayitlar.sort(function (a, b) {
        var av, bv;
        if (key === "sorguZamani" || key === "sonIslemTarihi") {
          av = tarihSaatAnahtar(a[key]) || ""; bv = tarihSaatAnahtar(b[key]) || "";
        } else {
          av = trLower(a[key] == null ? "" : String(a[key])); bv = trLower(b[key] == null ? "" : String(b[key]));
        }
        var cmp = av < bv ? -1 : (av > bv ? 1 : 0);
        return gecmisSortReverse ? -cmp : cmp;
      });
    }
    gecmisBodyEl.innerHTML = "";
    kayitlar.forEach(function (rec) {
      var tr = document.createElement("tr");
      GECMIS_KOLONLAR.forEach(function (c) {
        var td = document.createElement("td");
        td.textContent = rec[c[0]] == null ? "" : String(rec[c[0]]);
        tr.appendChild(td);
      });
      gecmisBodyEl.appendChild(tr);
    });
    gecmisStatusEl.textContent = kayitlar.length ? (kayitlar.length + " sonuç") : "";
  }

  function gecmisYukle() {
    gecmisStatusEl.textContent = "Yükleniyor…";
    fetch("api/barkod/gecmis").then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { gecmisStatusEl.textContent = ""; log("[HATA] " + (d.msg || "")); return; }
      gecmisHam = d.kayitlar || [];
      gecmisRenderRows();
    }).catch(function (e) { gecmisStatusEl.textContent = ""; log("[HATA] " + e); });
  }

  [tarihBas, tarihBit, alacakliAdi, borcluAdi].forEach(function (el) {
    el.addEventListener("keydown", function (e) { if (e.key === "Enter") filtrele(); });
  });
  [birimSel, durumSel].forEach(function (el) { el.addEventListener("change", filtrele); });
  filtreleBtn.addEventListener("click", filtrele);
  temizleBtn.addEventListener("click", temizle);
  sorgulaBtn.addEventListener("click", secilenleriSorgula);
  gecmisYenileBtn.addEventListener("click", gecmisYukle);

  buildHead();
  gecmisBuildHead();
  taraflarSecenekleriYukle();
  loadFields().then(filtrele);
  gecmisYukle();
})();
