// Dosyalarım (Tümü) — masaüstü DosyalarimGenelPanel'in web eşi.
// İŞ MANTIĞI sunucudaki dosya_core'dadır. İcra Dosyalarım'ın AKSİNE bu ekran
// canlı UYAP sorgusu yapmaz (yalnız DB okur); "Yenile" arka planda
// DosyaSorgu.calistir'i tetikler.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var filtreleBtn = $("dg-filtrele");
  if (!filtreleBtn) return;
  var temizleBtn = $("dg-temizle"), yenileBtn = $("dg-yenile"), yenileTumuBtn = $("dg-yenile-tumu"), detayBtn = $("dg-detay");
  var statusEl = $("dg-status");
  var turSel = $("dg-tur"), birimSel = $("dg-birim"), mahkemeSel = $("dg-mahkeme"), dosyaTurSel = $("dg-dosya-tur"), durumSel = $("dg-durum");
  var tarihBas = $("dg-tarih-bas"), tarihBit = $("dg-tarih-bit"), tarafAdi = $("dg-taraf-adi");
  var headEl = $("dg-head"), bodyEl = $("dg-body"), logEl = $("dg-log");

  var KOLONLAR = [
    ["yargi_turu_adi", "Yargı Türü"], ["birimAdi", "Yargı Birimi / Mahkeme"],
    ["dosyaNo", "Dosya No"], ["dosyaTur", "Dosya Türü"],
    ["dosyaDurum", "Durum"], ["acilisTarihi", "Açılış Tarihi"],
    ["taraf1", "Taraf 1"], ["taraf2", "Taraf 2"], ["taraf3", "Taraf 3"], ["taraf4", "Taraf 4"]
  ];

  var turKod = {}, dosyaTurKod = {}, durumKod = {};
  var dosyaTurleriTum = [];   // "Tümü" (filtresiz) tam dosya türü listesi — loadFields'ten
  var kayitlar = [];      // sunucudan gelen ham kayıtlar (dosyaId dahil)
  var selectedIdx = -1;

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
    headEl.innerHTML = "";
    KOLONLAR.forEach(function (c) {
      var th = document.createElement("th"); th.textContent = c[1]; headEl.appendChild(th);
    });
  }

  function renderRows() {
    bodyEl.innerHTML = "";
    kayitlar.forEach(function (rec, idx) {
      var tr = document.createElement("tr");
      if (idx === selectedIdx) tr.className = "icra-row-selected";
      tr.addEventListener("click", function () { selectedIdx = idx; renderRows(); });
      KOLONLAR.forEach(function (c) {
        var td = document.createElement("td");
        td.textContent = rec[c[0]] == null ? "" : String(rec[c[0]]);
        tr.appendChild(td);
      });
      bodyEl.appendChild(tr);
    });
    statusEl.textContent = kayitlar.length + " dosya";
  }

  function loadFields() {
    return fetch("api/dosyalarim/fields").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) { log("[HATA] Dosyalarım modülü hazır değil: " + (d && d.err || "")); return; }
      fillSelect(turSel, turKod, d.yargi_turleri);
      dosyaTurleriTum = d.dosya_turleri || [];
      fillSelect(dosyaTurSel, dosyaTurKod, dosyaTurleriTum);
      fillSelect(durumSel, durumKod, d.durumlar);
    }).catch(function () {});
  }

  function birimYukle() {
    var birimKod = {};
    birimSel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; birimSel.appendChild(o0);
    dosyaTurSel.value = "Tümü";
    dosyaTurKod = {};
    mahkemeYukle();
    if (turSel.value === "Tümü") {
      // Filtresiz: dosya türü listesi de loadFields'teki tam listeye döner.
      fillSelect(dosyaTurSel, dosyaTurKod, dosyaTurleriTum);
      return;
    }
    var kod = turKod[turSel.value];
    fetch("api/dosyalarim/birimler?yargi_turu=" + encodeURIComponent(kod))
      .then(function (r) { return r.json(); }).then(function (d) {
        (d.birimler || []).forEach(function (b) {
          birimKod[b.ad || b.kod] = b.kod;
          var o = document.createElement("option"); o.textContent = b.ad || b.kod; birimSel.appendChild(o);
        });
        birimSel._kodMap = birimKod;
        fillSelect(dosyaTurSel, dosyaTurKod, d.dosya_turleri);
      }).catch(function () {});
  }

  // "Yargı Birimi" mahkeme TÜRÜNÜ süzer (ör. Asliye Hukuk Mahkemesi);
  // "Mahkeme" ise aynı türdeki BELİRLİ mahkemeyi süzer (ör. "ANKARA 4.
  // ASLİYE HUKUK MAHKEMESİ") — kullanıcı bulgusu, 2026-07-12: "yargı türü
  // ve yargı birimi var fakat mahkeme ile filtreleme yok". Yargı Türü/Yargı
  // Birimi seçimine göre kademeli doldurulur.
  var mahkemeKod = {};
  function mahkemeYukle() {
    mahkemeKod = {};
    mahkemeSel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; mahkemeSel.appendChild(o0);
    var params = [];
    if (turSel.value !== "Tümü") params.push("yargi_turu=" + encodeURIComponent(turKod[turSel.value]));
    if (birimSel.value !== "Tümü" && birimSel._kodMap) params.push("yargi_birimi_kod=" + encodeURIComponent(birimSel._kodMap[birimSel.value]));
    fetch("api/dosyalarim/mahkemeler" + (params.length ? "?" + params.join("&") : ""))
      .then(function (r) { return r.json(); }).then(function (d) {
        (d.mahkemeler || []).forEach(function (m) {
          mahkemeKod[m.ad || m.birimId] = m.birimId;
          var o = document.createElement("option"); o.textContent = m.ad || m.birimId; mahkemeSel.appendChild(o);
        });
      }).catch(function () {});
  }

  function filtreleriTopla() {
    var body = {};
    if (turSel.value !== "Tümü") body.yargi_turu = turKod[turSel.value];
    if (birimSel.value !== "Tümü" && birimSel._kodMap) body.yargi_birimi_kod = birimSel._kodMap[birimSel.value];
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
      kayitlar = d.kayitlar || []; selectedIdx = -1; renderRows();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function temizle() {
    turSel.value = "Tümü"; birimYukle();
    mahkemeSel.value = "Tümü";
    dosyaTurSel.value = "Tümü"; durumSel.value = "Tümü";
    tarihBas.value = ""; tarihBit.value = ""; tarafAdi.value = "";
    filtrele();
  }

  var yenileSinceLog = 0;
  function yenileBaslat(body) {
    yenileBtn.disabled = true; yenileTumuBtn.disabled = true;
    statusEl.textContent = "UYAP'tan güncelleniyor…";
    post("api/dosyalarim/yenile", body).then(function (d) {
      if (!d.ok) { yenileBtn.disabled = false; yenileTumuBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); return; }
      yenileSinceLog = 0; yenilePoll();
    }).catch(function (e) { yenileBtn.disabled = false; yenileTumuBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }
  function yenile() {
    // Ekonomik: yalnız seçili Yargı Türü/Birim taranır. Hiçbiri seçili
    // değilse (Tümü/Tümü) SenkronKapsami'ye döner (arka plan senkronuyla
    // aynı kapsam) — TÜM türleri/birimleri taramak için "Tüm Dosyaları
    // Güncelle" kullanılmalı.
    var body = {};
    if (turSel.value !== "Tümü") {
      body.yargi_turu = turKod[turSel.value];
      if (birimSel.value !== "Tümü" && birimSel._kodMap) body.yargi_birimi_kod = birimSel._kodMap[birimSel.value];
    }
    yenileBaslat(body);
  }
  function yenileTumu() {
    yenileBaslat({ tum_turler: true });
  }
  function yenilePoll() {
    fetch("api/dosyalarim/yenile-durum?log=" + yenileSinceLog)
      .then(function (r) { return r.json(); }).then(function (s) {
        if (!s || !s.loaded) { yenileBtn.disabled = false; yenileTumuBtn.disabled = false; return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); yenileSinceLog = s.log_n; }
        if (s.running) { setTimeout(yenilePoll, 1000); return; }
        yenileBtn.disabled = false; yenileTumuBtn.disabled = false;
        if (s.sonuc && s.sonuc.hata) { statusEl.textContent = ""; log("[HATA] " + s.sonuc.hata); }
        else if (s.sonuc) {
          statusEl.textContent = "✔ Güncellendi (" + s.sonuc.toplam + " kayıt, " +
            (s.sonuc.sonuclar || []).length + " kapsam).";
          filtrele();
        }
      }).catch(function () { yenileBtn.disabled = false; yenileTumuBtn.disabled = false; });
  }

  function dosyaGoruntule() {
    if (selectedIdx < 0) { statusEl.textContent = "Önce listeden bir dosya seçin."; return; }
    var rec = kayitlar[selectedIdx];
    detayBtn.disabled = true;
    statusEl.textContent = "Dosya ayrıntısı alınıyor…";
    post("api/dosyalarim/detay", {
      dosyaId: rec.dosyaId, birimId: rec.birimId, dosyaNo: rec.dosyaNo, dosyaTurKod: rec.dosyaTurKod
    }).then(function (d) {
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
          if (t.vekil) satir += " — Vekil: " + t.vekil.replace(/^\[|\]$/g, "");
          satirlar.push(satir);
        });
      }
      alert(satirlar.join("\n") + (d.kaydedildi ? "\n\n(Yerel veritabanına kaydedildi.)" : ""));
      // Kullanıcı bulgusu (2026-07-12): kaydedilen yeni taraf/ayrıntı verisi
      // tabloya hiç yansımıyordu — elle "Filtrele"ye basmadan görünmüyordu.
      if (d.kaydedildi) filtrele();
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }

  turSel.addEventListener("change", function () { birimYukle(); filtrele(); });
  birimSel.addEventListener("change", function () { mahkemeYukle(); filtrele(); });
  [mahkemeSel, dosyaTurSel, durumSel].forEach(function (el) {
    el.addEventListener("change", filtrele);
  });
  [tarihBas, tarihBit, tarafAdi].forEach(function (el) {
    el.addEventListener("keydown", function (e) { if (e.key === "Enter") filtrele(); });
  });
  filtreleBtn.addEventListener("click", filtrele);
  temizleBtn.addEventListener("click", temizle);
  yenileBtn.addEventListener("click", yenile);
  yenileTumuBtn.addEventListener("click", yenileTumu);
  detayBtn.addEventListener("click", dosyaGoruntule);

  buildHead();
  mahkemeYukle();
  loadFields().then(filtrele);
})();
