// İcra · Dosyalarım — masaüstü IcraDosyalarimPanel'in web eşi.
// İŞ MANTIĞI sunucudaki icra_core'dadır (orijinal SorguMotoru._post → 127.0.0.1:8800
// ofis → canlı e-imza UYAP). Burada yalnızca sunum: kolon başlığı altındaki kutular
// hem sunucu kriteri (Sorgula) hem de dönen sonuçları anında daraltan filtredir.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var sorgulaBtn = $("icra-sorgula");
  if (!sorgulaBtn) return;
  var temizleBtn = $("icra-temizle"), statusEl = $("icra-status"), sayacEl = $("icra-sayac");
  var detayBtn = $("icra-detay");
  var durumSel = $("icra-durum"), headEl = $("icra-head"), filterRow = $("icra-filter-row"),
      bodyEl = $("icra-body"), logEl = $("icra-log");

  var columns = [];        // [{key,label}]
  var rows = [];           // [[değer...], ...] sunucudan
  var filters = {};        // colKey -> filtre kutusu <input>
  var sinceLog = 0, sinceRev = 0, running = false, ready = false, polling = false;
  var selectedIdx = -1;    // "Dosya Görüntüle" için seçili satırın rows[] içindeki (HAM, filtrelenmemiş) sırası

  // Türkçe-duyarsız küçük harf (icra_core.tr_lower eşi) — canlı filtre eşleşmesi için.
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

  // ── Alanları yükle: kolon başlıkları + durum seçenekleri ──
  function loadFields() {
    return fetch("api/icra/fields").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) { log("[HATA] İcra modülü hazır değil: " + (d && d.err || "")); return; }
      columns = d.columns || [];
      durumSel.innerHTML = "";
      (d.durumlar || []).forEach(function (du) {
        var o = document.createElement("option");
        o.value = du.kod; o.textContent = du.label;
        if (du.kod === d.durum_default) o.selected = true;
        durumSel.appendChild(o);
      });
      buildHead();
      ready = true;
    }).catch(function () {});
  }

  // ── Başlık şeridi + filtre/kriter kutuları (her kolon için bir <input>) ──
  function buildHead() {
    headEl.innerHTML = ""; filterRow.innerHTML = ""; filters = {};
    columns.forEach(function (c) {
      var th = document.createElement("th"); th.textContent = c.label; headEl.appendChild(th);
      var ftd = document.createElement("th");
      var inp = document.createElement("input");
      inp.type = "text"; inp.className = "icra-colbox"; inp.placeholder = "süz / ara";
      inp.addEventListener("input", renderRows);                 // canlı süzme
      inp.addEventListener("keydown", function (e) {             // Enter → UYAP'tan çek
        if (e.key === "Enter") sorgula();
      });
      filters[c.key] = inp;
      ftd.appendChild(inp); filterRow.appendChild(ftd);
    });
  }

  // ── Sonuç çizimi (canlı filtre uygulanmış) ──
  function visibleRows() {
    var aktif = {};
    columns.forEach(function (c, i) {
      var q = trLower(filters[c.key] ? filters[c.key].value.trim() : "");
      if (q) aktif[i] = q;
    });
    var keys = Object.keys(aktif);
    // Her satırla birlikte ORİJİNAL (ham, filtrelenmemiş) index'i taşı — "Dosya
    // Görüntüle" sunucuya bu index'i gönderir, sunucudaki job.records de AYNI ham
    // sırada tutulur (bkz. server.py IcraJob._set_rows).
    var withIdx = rows.map(function (row, i) { return [i, row]; });
    if (!keys.length) return withIdx;
    return withIdx.filter(function (pair) {
      var row = pair[1];
      return keys.every(function (i) { return trLower(row[i]).indexOf(aktif[i]) >= 0; });
    });
  }

  function renderRows() {
    var göster = visibleRows();
    bodyEl.innerHTML = "";
    göster.forEach(function (pair) {
      var idx = pair[0], row = pair[1];
      var tr = document.createElement("tr");
      tr.dataset.idx = idx;
      if (idx === selectedIdx) tr.className = "icra-row-selected";
      tr.addEventListener("click", function () {
        selectedIdx = idx;
        renderRows();
      });
      row.forEach(function (v) {
        var td = document.createElement("td");
        td.textContent = v == null ? "" : String(v);
        tr.appendChild(td);
      });
      bodyEl.appendChild(tr);
    });
    if (!rows.length) sayacEl.textContent = "";
    else if (göster.length !== rows.length) sayacEl.textContent = göster.length + " / " + rows.length + " dosya";
    else sayacEl.textContent = rows.length + " dosya";
  }

  // ── Dosya Görüntüle (Dosya Bilgileri ayrıntısı) ──
  function dosyaGoruntule() {
    if (selectedIdx < 0) { statusEl.textContent = "Önce listeden bir dosya seçin."; return; }
    detayBtn.disabled = true;
    statusEl.textContent = "Dosya ayrıntısı alınıyor…";
    post("api/icra/detay", { index: selectedIdx }).then(function (d) {
      detayBtn.disabled = false;
      if (!d.ok) { statusEl.textContent = "Dosya ayrıntısı alınamadı"; log("[HATA] " + (d.msg || "")); alert(d.msg || "Dosya ayrıntısı alınamadı."); return; }
      statusEl.textContent = "Dosya ayrıntısı kaydedildi";
      var ham = d.ham || {}, satirlar = [];
      if (d.aile === "icra") {
        satirlar = [
          "Takibin Türü: " + (ham.takibinTuru || "—"),
          "Takibin Şekli: " + (ham.takibinSekli || "—"),
          "Takibin Yolu: " + (ham.takibinYolu || "—"),
          "Alacak Kalemi Toplam: " + (ham.alacakKalemToplamTutar || "—"),
          "Vekalet Ücreti: " + (ham.vekaletUcreti || "—"),
          "Tahsil Harcı: " + (ham.tahsilHarci || "—")
        ];
      } else if (d.aile === "hukuk") {
        satirlar = [
          "Dava Açılış Türü: " + (ham.davaAcilisTuru || "—"),
          "Dava Türleri: " + (ham.davaTurleriStr || "—"),
          "İlgili Dava Listesi: " + (ham.ilgiliDavaListesiStr || "—"),
          "Duruşma Tarihi: " + (ham.durusmaTarihi || "—")
        ];
      } else {
        satirlar = ["Bu yargı türü için henüz ayrıntı görüntüleme desteklenmiyor."];
      }
      var taraflar = d.taraflar || [];
      if (taraflar.length) {
        satirlar.push("");
        satirlar.push("Taraf Bilgileri:");
        taraflar.forEach(function (t) {
          var satir = "  " + (t.rol || "") + ": " + (t.adi || "");
          if (t.vekil) satir += " — Vekil: " + t.vekil;
          satirlar.push(satir);
        });
      }
      alert(satirlar.join("\n") + (d.kaydedildi ? "\n\n(Yerel veritabanına kaydedildi.)" : ""));
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }
  if (detayBtn) detayBtn.addEventListener("click", dosyaGoruntule);

  // ── Taraf türü değişimi (Gerçek Kişi ↔ Kurum) ──
  function tarafTur() {
    var el = document.querySelector('input[name="icra-taraf-tur"]:checked');
    return el ? el.value : "gercek";
  }
  Array.prototype.forEach.call(document.querySelectorAll('input[name="icra-taraf-tur"]'), function (r) {
    r.addEventListener("change", function () {
      var kurum = tarafTur() === "kurum";
      $("icra-taraf-kisi").hidden = kurum;
      $("icra-taraf-kurum").hidden = !kurum;
    });
  });

  // ── Sorgula (sunucuya kriter gönder; iş mantığı icra_core'da) ──
  function sorgula() {
    if (!ready || running) return;
    var body = {
      durum_kod: parseInt(durumSel.value, 10) || 0,
      dosyaAcilisTarihiStart: $("icra-tarih-bas").value.trim(),
      dosyaAcilisTarihiEnd: $("icra-tarih-bit").value.trim(),
      taraf_tur: tarafTur(),
      taraf_ad: $("icra-taraf-ad").value.trim(),
      taraf_soyad: $("icra-taraf-soyad").value.trim(),
      taraf_tckn: $("icra-taraf-tckn").value.trim(),
      taraf_kurum: $("icra-taraf-kurum-ad").value.trim(),
      taraf_vergi: $("icra-taraf-vergi").value.trim(),
      taraf_mersis: $("icra-taraf-mersis").value.trim()
    };
    // Kolon kutuları hem sunucu kriteri hem süzgeçtir (masaüstü ile aynı).
    columns.forEach(function (c) {
      body[c.key] = filters[c.key] ? filters[c.key].value.trim() : "";
    });
    statusEl.textContent = "● Sorgulanıyor…";
    post("api/icra/search", body).then(function (d) {
      if (!d.ok) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "başlatılamadı")); return; }
      running = true; ensurePoll();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function temizle() {
    durumSel.selectedIndex = 0;
    ["icra-tarih-bas", "icra-tarih-bit", "icra-taraf-ad", "icra-taraf-soyad",
     "icra-taraf-tckn", "icra-taraf-kurum-ad", "icra-taraf-vergi", "icra-taraf-mersis"]
      .forEach(function (id) { var e = $(id); if (e) e.value = ""; });
    columns.forEach(function (c) { if (filters[c.key]) filters[c.key].value = ""; });
    selectedIdx = -1;
    renderRows();
    statusEl.textContent = "";
  }

  sorgulaBtn.addEventListener("click", sorgula);
  temizleBtn.addEventListener("click", temizle);

  // ── Durum polling (sunucu DB-önce → canlı-sonra akışını yansıtır) ──
  function ensurePoll() {
    if (polling) return;
    polling = true;
    poll();
  }

  function poll() {
    fetch("api/icra/status?log=" + sinceLog + "&rev=" + sinceRev)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.loaded) { polling = false; return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); sinceLog = s.log_n; }
        if (s.rows) { rows = s.rows; sinceRev = s.rev; selectedIdx = -1; renderRows(); }
        else if (typeof s.rev === "number") sinceRev = s.rev;
        if (s.status) statusEl.textContent = "● " + s.status;
        if (s.yeni && s.yeni.length) {
          var liste = s.yeni.map(function (y) { return "- " + y.dosya_no + " (" + y.birim_adi + ")"; }).join("\n");
          log("Veritabanına " + s.yeni.length + " yeni dosya eklendi:\n" + liste);
        }
        running = !!s.running;
        if (!running) { polling = false; return; }   // iş bitti → polling durur
        setTimeout(poll, 1000);
      })
      .catch(function () { polling = false; });
  }

  // ── Açılış: alanları + birim listesini yükle ──
  loadFields().then(function () {
    // İcra daireleri açılır listesi (datalist) — masaüstü _combolari_doldur birimAdi eşi.
    fetch("api/icra/units").then(function (r) { return r.json(); }).then(function (d) {
      var units = (d && d.units) || [];
      if (!units.length) return;
      var dl = document.createElement("datalist"); dl.id = "icra-units";
      units.forEach(function (u) { var o = document.createElement("option"); o.value = u; dl.appendChild(o); });
      document.body.appendChild(dl);
      if (filters.birimAdi) filters.birimAdi.setAttribute("list", "icra-units");
    }).catch(function () {});
  });
})();
