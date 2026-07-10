// Dosyalarım (Tümü) — masaüstü DosyalarimGenelPanel'in web eşi.
// İŞ MANTIĞI sunucudaki dosya_core'dadır. İcra Dosyalarım'ın AKSİNE bu ekran
// canlı UYAP sorgusu yapmaz (yalnız DB okur); "Yenile" arka planda
// DosyaSorgu.calistir'i tetikler.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var filtreleBtn = $("dg-filtrele");
  if (!filtreleBtn) return;
  var temizleBtn = $("dg-temizle"), yenileBtn = $("dg-yenile"), detayBtn = $("dg-detay");
  var statusEl = $("dg-status");
  var turSel = $("dg-tur"), birimSel = $("dg-birim"), dosyaTurSel = $("dg-dosya-tur"), durumSel = $("dg-durum");
  var tarihBas = $("dg-tarih-bas"), tarihBit = $("dg-tarih-bit");
  var headEl = $("dg-head"), bodyEl = $("dg-body"), logEl = $("dg-log");

  var KOLONLAR = [
    ["yargi_turu_adi", "Yargı Türü"], ["birimAdi", "Yargı Birimi / Mahkeme"],
    ["dosyaNo", "Dosya No"], ["dosyaTur", "Dosya Türü"],
    ["dosyaDurum", "Durum"], ["acilisTarihi", "Açılış Tarihi"]
  ];

  var turKod = {}, dosyaTurKod = {}, durumKod = {};
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
      fillSelect(dosyaTurSel, dosyaTurKod, d.dosya_turleri);
      fillSelect(durumSel, durumKod, d.durumlar);
    }).catch(function () {});
  }

  function birimYukle() {
    var birimKod = {};
    birimSel.innerHTML = "";
    var o0 = document.createElement("option"); o0.textContent = "Tümü"; birimSel.appendChild(o0);
    if (turSel.value === "Tümü") return;
    var kod = turKod[turSel.value];
    fetch("api/dosyalarim/birimler?yargi_turu=" + encodeURIComponent(kod))
      .then(function (r) { return r.json(); }).then(function (d) {
        (d.birimler || []).forEach(function (b) {
          birimKod[b.ad || b.kod] = b.kod;
          var o = document.createElement("option"); o.textContent = b.ad || b.kod; birimSel.appendChild(o);
        });
        birimSel._kodMap = birimKod;
      }).catch(function () {});
  }

  function filtreleriTopla() {
    var body = {};
    if (turSel.value !== "Tümü") body.yargi_turu = turKod[turSel.value];
    if (birimSel.value !== "Tümü" && birimSel._kodMap) body.yargi_birimi_kod = birimSel._kodMap[birimSel.value];
    if (dosyaTurSel.value !== "Tümü") body.tur_kod = dosyaTurKod[dosyaTurSel.value];
    if (durumSel.value !== "Tümü") body.durum_kod = durumKod[durumSel.value];
    if (tarihBas.value.trim()) body.tarih_baslangic = tarihBas.value.trim();
    if (tarihBit.value.trim()) body.tarih_bitis = tarihBit.value.trim();
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
    dosyaTurSel.value = "Tümü"; durumSel.value = "Tümü";
    tarihBas.value = ""; tarihBit.value = "";
    filtrele();
  }

  var yenilePolling = false, yenileSinceLog = 0;
  function yenile() {
    yenileBtn.disabled = true;
    statusEl.textContent = "UYAP'tan güncelleniyor…";
    post("api/dosyalarim/yenile", {}).then(function (d) {
      if (!d.ok) { yenileBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + (d.msg || "")); return; }
      yenileSinceLog = 0; yenilePoll();
    }).catch(function (e) { yenileBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }
  function yenilePoll() {
    fetch("api/dosyalarim/yenile-durum?log=" + yenileSinceLog)
      .then(function (r) { return r.json(); }).then(function (s) {
        if (!s || !s.loaded) { yenileBtn.disabled = false; return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); yenileSinceLog = s.log_n; }
        if (s.running) { setTimeout(yenilePoll, 1000); return; }
        yenileBtn.disabled = false;
        if (s.sonuc && s.sonuc.hata) { statusEl.textContent = ""; log("[HATA] " + s.sonuc.hata); }
        else if (s.sonuc) {
          statusEl.textContent = "✔ Güncellendi (" + s.sonuc.toplam + " kayıt, " +
            (s.sonuc.sonuclar || []).length + " kapsam).";
          filtrele();
        }
      }).catch(function () { yenileBtn.disabled = false; });
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
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }

  turSel.addEventListener("change", function () { birimYukle(); filtrele(); });
  [birimSel, dosyaTurSel, durumSel].forEach(function (el) {
    el.addEventListener("change", filtrele);
  });
  [tarihBas, tarihBit].forEach(function (el) {
    el.addEventListener("keydown", function (e) { if (e.key === "Enter") filtrele(); });
  });
  filtreleBtn.addEventListener("click", filtrele);
  temizleBtn.addEventListener("click", temizle);
  yenileBtn.addEventListener("click", yenile);
  detayBtn.addEventListener("click", dosyaGoruntule);

  buildHead();
  loadFields().then(filtrele);
})();
