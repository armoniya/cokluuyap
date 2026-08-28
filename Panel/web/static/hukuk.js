// Hukuk Dosyalarım — masaüstü HukukDosyalarimPanel'in web eşi.
// dosyalarim_genel.js'in Hukuk'a (yargı türü=1) SABİT kılınmış, sadeleştirilmiş
// hâli — İcra'ya özgü alanlar (Alacaklı/Borçlu, Kesinleşme/Tebliğ Durumu,
// Barkod Sorgu, "Tüm Dosyaları Güncelle") BİLEREK YOK. İŞ MANTIĞI sunucudaki
// dosya_core'dadır; bu ekran de canlı UYAP sorgusu yapmaz (yalnız DB okur).
(function () {
  "use strict";
  var YARGI_TURU_HUKUK = 1;
  var $ = function (id) { return document.getElementById(id); };
  var filtreleBtn = $("hd-filtrele");
  if (!filtreleBtn) return;
  var temizleBtn = $("hd-temizle"), yenileBtn = $("hd-yenile"), detayBtn = $("hd-detay");
  var pauseBtn = $("hd-pause"), stopBtn = $("hd-stop");
  var statusEl = $("hd-status");
  var birimSel = $("hd-birim"), mahkemeSel = $("hd-mahkeme"), dosyaTurSel = $("hd-dosya-tur"), durumSel = $("hd-durum");
  var tarihBas = $("hd-tarih-bas"), tarihBit = $("hd-tarih-bit"), tarafAdi = $("hd-taraf-adi");
  var headEl = $("hd-head"), filterRowEl = $("hd-filter-row"), bodyEl = $("hd-body"), logEl = $("hd-log");

  var KOLONLAR = [
    ["birimAdi", "Mahkeme"], ["dosyaNo", "Dosya No"], ["dosyaTur", "Dosya Türü"],
    ["dosyaDurum", "Durum"], ["acilisTarihi", "Açılış Tarihi"],
    ["davaci", "Davacı"], ["davali", "Davalı"]
  ];

  var birimKod = {}, mahkemeKod = {}, dosyaTurKod = {}, durumKod = {};
  var kayitlarHam = [];
  var selectedIdx = -1;
  var colFilters = {}, colHeadEls = {};
  var sortKey = null, sortReverse = false;

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

  function buildHead() {
    headEl.innerHTML = ""; filterRowEl.innerHTML = ""; colFilters = {}; colHeadEls = {};
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
      var t = s.trim();
      if (!t) { gecersizler.push(pair); return; }
      if (sayiRe.test(t)) gecerliler.push([[0, parseFloat(t.replace(",", "."))], pair]);
      else gecerliler.push([[1, trLower(t)], pair]);
    });
    gecerliler.sort(function (a, b) {
      var ka = a[0], kb = b[0];
      var cmp;
      if (key === "acilisTarihi") {
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

  function renderRows() {
    var gorunen = visibleRows();
    bodyEl.innerHTML = "";
    gorunen.forEach(function (pair) {
      var idx = pair[0], rec = pair[1];
      var tr = document.createElement("tr");
      if (idx === selectedIdx) tr.className = "icra-row-selected";
      tr.addEventListener("click", function () { selectedIdx = idx; renderRows(); });
      tr.addEventListener("dblclick", function () { selectedIdx = idx; dosyaGoruntule(); });
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
    return fetch("api/dosyalarim/birimler?yargi_turu=" + YARGI_TURU_HUKUK)
      .then(function (r) { return r.json(); }).then(function (d) {
        fillSelect(birimSel, birimKod, d.birimler);
        fillSelect(dosyaTurSel, dosyaTurKod, d.dosya_turleri);
      }).catch(function () {});
  }

  function loadDurumlar() {
    fetch("api/dosyalarim/fields").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) { log("[HATA] Dosyalarım modülü hazır değil: " + (d && d.err || "")); return; }
      fillSelect(durumSel, durumKod, d.durumlar);
    }).catch(function () {});
  }

  function mahkemeYukle() {
    mahkemeKod = {};
    mahkemeSel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; mahkemeSel.appendChild(o0);
    var params = ["yargi_turu=" + YARGI_TURU_HUKUK];
    if (birimSel.value !== "Tümü") params.push("yargi_birimi_kod=" + encodeURIComponent(birimKod[birimSel.value]));
    fetch("api/dosyalarim/mahkemeler?" + params.join("&"))
      .then(function (r) { return r.json(); }).then(function (d) {
        (d.mahkemeler || []).forEach(function (m) {
          mahkemeKod[m.ad || m.birimId] = m.birimId;
          var o = document.createElement("option"); o.textContent = m.ad || m.birimId; mahkemeSel.appendChild(o);
        });
      }).catch(function () {});
  }

  function filtreleriTopla() {
    var body = { yargi_turu: YARGI_TURU_HUKUK };
    if (birimSel.value !== "Tümü") body.yargi_birimi_kod = birimKod[birimSel.value];
    if (mahkemeSel.value !== "Tümü") body.mahkeme_id = mahkemeKod[mahkemeSel.value];
    if (dosyaTurSel.value !== "Tümü") body.tur_kod = dosyaTurKod[dosyaTurSel.value];
    if (durumSel.value !== "Tümü") body.durum_kod = durumKod[durumSel.value];
    if (tarihBas.value.trim()) body.tarih_baslangic = tarihBas.value.trim();
    if (tarihBit.value.trim()) body.tarih_bitis = tarihBit.value.trim();
    if (tarafAdi.value.trim()) body.taraf_adi = tarafAdi.value.trim();
    return body;
  }

  function filtrele() {
    statusEl.textContent = "Yükleniyor…";
    post("api/dosyalarim/list", filtreleriTopla()).then(function (d) {
      if (!d.ok) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); return; }
      kayitlarHam = d.kayitlar || []; selectedIdx = -1; renderRows();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function temizle() {
    birimSel.value = "Tümü"; mahkemeYukle();
    dosyaTurSel.value = "Tümü"; durumSel.value = "Tümü";
    tarihBas.value = ""; tarihBit.value = ""; tarafAdi.value = "";
    yerelFiltreleriSifirla();
    filtrele();
  }

  var yenileSinceLog = 0, yenileCalisiyor = false, yenilePaused = false;
  function yenileControls() {
    yenileBtn.disabled = yenileCalisiyor;
    if (pauseBtn) { pauseBtn.disabled = !yenileCalisiyor; pauseBtn.textContent = yenilePaused ? "Devam" : "Duraklat"; }
    if (stopBtn) stopBtn.disabled = !yenileCalisiyor;
  }
  function yenileBaslat(body) {
    statusEl.textContent = "UYAP'tan güncelleniyor…";
    window.topluIs.baslat(function (extra) {
      var b = {}; for (var k in body) b[k] = body[k]; for (var k2 in extra) b[k2] = extra[k2];
      return post("api/dosyalarim/yenile", b);
    }, function (t) { statusEl.textContent = t; }).then(function (d) {
      if (!d.ok) { if (!d.cakisma) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); } return; }
      yenileCalisiyor = true; yenileControls();
      yenileSinceLog = 0; yenilePoll();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }
  function yenileDuraklatToggle() {
    if (!yenileCalisiyor) return;
    post("api/dosyalarim/yenile-duraklat").then(function (d) { yenilePaused = !!d.paused; yenileControls(); });
  }
  function yenileDurdur() {
    if (yenileCalisiyor) post("api/dosyalarim/yenile-durdur");
  }
  if (pauseBtn) pauseBtn.addEventListener("click", yenileDuraklatToggle);
  if (stopBtn) stopBtn.addEventListener("click", yenileDurdur);
  function yenile() {
    // Ekonomik: yalnız seçili Yargı Birimi taranır — Tümü ise dosya_core
    // Hukuk'un Senkron Kapsamı'ndaki TÜM birimlerini genişletir (bkz.
    // dosya_core.dosyalarim_yenile).
    var body = { yargi_turu: YARGI_TURU_HUKUK };
    if (birimSel.value !== "Tümü") body.yargi_birimi_kod = birimKod[birimSel.value];
    yenileBaslat(body);
  }
  function yenilePoll() {
    fetch("api/dosyalarim/yenile-durum?log=" + yenileSinceLog)
      .then(function (r) { return r.json(); }).then(function (s) {
        if (!s || !s.loaded) { yenileCalisiyor = false; yenileControls(); return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); yenileSinceLog = s.log_n; }
        yenilePaused = !!s.paused; yenileControls();
        if (s.running) { setTimeout(yenilePoll, 1000); return; }
        yenileCalisiyor = false; yenileControls();
        if (s.sonuc && s.sonuc.hata) { statusEl.textContent = ""; log("[HATA] " + s.sonuc.hata); }
        else if (s.sonuc) {
          statusEl.textContent = "✔ Güncellendi (" + s.sonuc.toplam + " kayıt, " +
            (s.sonuc.sonuclar || []).length + " kapsam).";
          filtrele();
        }
      }).catch(function () { yenileCalisiyor = false; yenileControls(); });
  }

  function dosyaGoruntule() {
    if (selectedIdx < 0) { statusEl.textContent = "Önce listeden bir dosya seçin."; return; }
    var rec = kayitlarHam[selectedIdx];
    detayBtn.disabled = true;
    statusEl.textContent = "Dosya ayrıntısı alınıyor…";
    post("api/dosyalarim/detay", {
      dosyaId: rec.dosyaId, birimId: rec.birimId, dosyaNo: rec.dosyaNo, dosyaTurKod: rec.dosyaTurKod
    }).then(function (d) {
      detayBtn.disabled = false;
      if (!d.ok) { statusEl.textContent = "Dosya ayrıntısı alınamadı"; log("[HATA] " + (d.msg || "")); alert(d.msg || "Dosya ayrıntısı alınamadı."); return; }
      statusEl.textContent = "Dosya ayrıntısı kaydedildi";
      var ham = d.ham || {};
      var satirlar = [
        "Mahkeme: " + (rec.birimAdi || "—"),
        "Dosya No: " + (rec.dosyaNo || "—"),
        "Dosya Türü: " + (rec.dosyaTur || "—"),
        "Durum: " + (rec.dosyaDurum || "—"),
        "Açılış Tarihi: " + (rec.acilisTarihi || "—"),
        ""
      ];
      if (d.aile === "hukuk") {
        satirlar = satirlar.concat([
          "Dava Açılış Türü: " + (ham.davaAcilisTuru || "—"),
          "Dava Türleri: " + (ham.davaTurleriStr || "—"),
          "İlgili Dava Listesi: " + (ham.ilgiliDavaListesiStr || "—"),
          "Duruşma Tarihi: " + (ham.durusmaTarihi || "—")
        ]);
      } else {
        satirlar.push("Bu dosya için ek ayrıntı görüntüleme desteklenmiyor.");
      }
      var taraflar = d.taraflar || [];
      if (taraflar.length) {
        satirlar.push("");
        satirlar.push("Taraf Bilgileri:");
        taraflar.forEach(function (t) {
          var satir = "  " + (t.rol || "") + ": " + (t.adi || "");
          if (t.vekil) satir += " — Vekil: " + t.vekil.replace(/^\[|\]$/g, "");
          satirlar.push(satir);
        });
      }
      alert(satirlar.join("\n") + (d.kaydedildi ? "\n\n(Yerel veritabanına kaydedildi.)" : ""));
      if (d.kaydedildi) filtrele();
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }

  birimSel.addEventListener("change", function () { mahkemeYukle(); yerelFiltreleriSifirla(); filtrele(); });
  [mahkemeSel, dosyaTurSel, durumSel].forEach(function (el) {
    el.addEventListener("change", filtrele);
  });
  [tarihBas, tarihBit, tarafAdi].forEach(function (el) {
    el.addEventListener("keydown", function (e) { if (e.key === "Enter") filtrele(); });
  });
  filtreleBtn.addEventListener("click", filtrele);
  temizleBtn.addEventListener("click", temizle);
  yenileBtn.addEventListener("click", yenile);
  detayBtn.addEventListener("click", dosyaGoruntule);

  buildHead();
  mahkemeYukle();
  loadDurumlar();
  loadFields().then(filtrele);
})();
