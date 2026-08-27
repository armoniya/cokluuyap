// Dosyalarım (Tümü) — masaüstü DosyalarimGenelPanel'in web eşi.
// İŞ MANTIĞI sunucudaki dosya_core'dadır. İcra Dosyalarım'ın AKSİNE bu ekran
// canlı UYAP sorgusu yapmaz (yalnız DB okur); "Yenile" arka planda
// DosyaSorgu.calistir'i tetikler.
//
// Sütun başlığı canlı filtre + sıralama (2026-07-13, masaüstünden taşındı —
// bkz. Panel/modules/dosyalarim_genel.py `_yerel_uygula`/`_sirala`): üst
// filtre çubuğu (Yargı Türü/Durum/Tarih/Taraf/Alacaklı/Borçlu) sunucuya gidip
// `kayitlarHam`'ı DB'den tazeler; sütun başlıklarındaki kutular ise DB'ye
// gitmeden, yerelde `kayitlarHam`'ı daha da daraltır/sıralar (icra.js'teki
// aynı "filterRow" desenini kullanır).
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var filtreleBtn = $("dg-filtrele");
  if (!filtreleBtn) return;
  var temizleBtn = $("dg-temizle"), yenileBtn = $("dg-yenile"), yenileTumuBtn = $("dg-yenile-tumu"), detayBtn = $("dg-detay");
  var pauseBtn = $("dg-pause"), stopBtn = $("dg-stop");
  var statusEl = $("dg-status");
  var turSel = $("dg-tur"), birimSel = $("dg-birim"), mahkemeSel = $("dg-mahkeme"), dosyaTurSel = $("dg-dosya-tur"), durumSel = $("dg-durum");
  var tarihBas = $("dg-tarih-bas"), tarihBit = $("dg-tarih-bit"), tarafAdi = $("dg-taraf-adi");
  var alacakliAdi = $("dg-alacakli"), borcluAdi = $("dg-borclu"), icraExtra = $("dg-icra-extra");
  var alacakliList = $("dg-alacakli-list"), borcluList = $("dg-borclu-list");
  var headEl = $("dg-head"), filterRowEl = $("dg-filter-row"), bodyEl = $("dg-body"), logEl = $("dg-log");

  var KOLONLAR = [
    ["yargi_turu_adi", "Yargı Türü"], ["birimAdi", "Yargı Birimi / Mahkeme"],
    ["dosyaNo", "Dosya No"], ["dosyaTur", "Dosya Türü"],
    ["dosyaDurum", "Durum"], ["acilisTarihi", "Açılış Tarihi"],
    // İcra Dosyalarım'daki gibi kendi başına sütun — kullanıcı bulgusu,
    // 2026-07-13: alacaklı/borçlu taraf1..4 genel sütunlarında gömülüydü ama
    // tek bakışta ayırt edilemiyordu. İcra dışı yargı türlerinde bu roller
    // hiç oluşmadığından hücre boş kalır (bkz. dosya_core.dosyalarim_db_listele).
    ["alacakli", "Alacaklı"], ["borclu", "Borçlu"],
    ["taraf1", "Taraf 1"], ["taraf2", "Taraf 2"], ["taraf3", "Taraf 3"], ["taraf4", "Taraf 4"],
    // Kesinleşme Durumu / Tebliğ Durumu — hem burada hem İcra Dosyalarım'da
    // (icra.js) gösterilir (kullanıcı isteği, 2026-08-14). Veri kaynağı
    // ikisinde de AYNI (dosya_core._dosya_barkod_ozetlerini_ekle).
    ["kesinlesme_durumu", "Kesinleşme Durumu"], ["tebligat_durumu", "Tebliğ Durumu"]
  ];

  var turKod = {}, dosyaTurKod = {}, durumKod = {};
  var dosyaTurleriTum = [];   // "Tümü" (filtresiz) tam dosya türü listesi — loadFields'ten
  var kayitlarHam = [];    // sunucudan gelen ham kayıtlar (dosyaId dahil) — DB filtreleri uygulanmış
  var selectedIdx = -1;    // kayitlarHam İÇİNDEKİ (ham, yerel-filtrelenmemiş) orijinal index
  var colFilters = {};     // sütun anahtarı -> başlık altındaki canlı filtre <input>
  var colHeadEls = {};     // sütun anahtarı -> başlık <th> (sıralama okunu güncellemek için)
  var sortKey = null, sortReverse = false;

  // Türkçe-duyarsız küçük harf (dosya_core.tr_lower eşi) — canlı filtre ve
  // sıralama karşılaştırmasında düz .toLowerCase() 'İ/I/ı/Ş/Ğ/Ü/Ö/Ç'
  // harflerinde YANLIŞ eşleştirir (bkz. icra.js'teki AYNI yardımcı).
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

  // ── Başlık şeridi: etiket (tıkla-sırala) + altında canlı filtre kutusu ──
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
      inp.addEventListener("input", renderRows);   // canlı süzme — DB'ye gitmez
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

  // Sunucudan gelmiş `kayitlarHam`'a sütun-başlığı filtrelerini ve
  // sıralamayı DB'ye gitmeden, yerelde uygular. Her satırla birlikte
  // ORİJİNAL (ham) index'i taşır — "Dosya Görüntüle" bu index'i kullanır
  // (bkz. dosyaGoruntule, icra.js'teki AYNI desen).
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
    if (sortKey) {
      out = siraliDiz(out, sortKey, sortReverse);
    }
    return out;
  }

  // Boş/biçimsiz değerler HER İKİ yönde de (artan/azalan) SONA atılır —
  // kullanıcı bulgusu, 2026-07-13: eskiden localeCompare/parseFloat farkı
  // yüzünden yalnız `sortReverse ? -cmp : cmp` uygulanıyordu; azalan sırada
  // boş satırlar (özellikle tarih sütununda) başa zıplıyordu. Artık geçerli
  // değerler ayrılıp yalnız ONLAR ters çevrilir, geçersizler HER ZAMAN sonda
  // sabit kalır — masaüstündeki AYNI düzeltme (bkz. `_yerel_uygula`), tüm
  // sütunlarda (sayısal/metin/tarih) aynı şekilde uygulanır.
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
      // JS parseFloat "12.07.2026"ı SESSİZCE 12.07'ye keser (Python float()
      // aksine iki noktalı dizeyi reddetmez) — tam dize sayı biçimine
      // uymuyorsa sayısal karşılaştırmaya HİÇ girilmez.
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

  // "GG.AA.YYYY" -> "YYYYAAGG" dizesi (karşılaştırılabilir); biçime
  // uymuyorsa null (geçersiz — siraliDiz bunu her zaman sona atar).
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
      // Masaüstündeki <Double-1> ile AYNI: bir dosyaya çift tıklayınca ayrıntı
      // açılsın (kullanıcı bulgusu, 2026-07-13: "tıpkı uyaptaki gibi").
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

  // Yargı Türü/Birim (üst çubuk) değişince çağrılır: sütun-başlığı filtreleri
  // ve sıralama SIFIRLANIR — aksi halde eski kapsamda yazılmış görünmeyen bir
  // filtre yeni kapsamdaki kayıtları sessizce eleyip boş tablo gösterebilir
  // (masaüstündeki `_yerel_filtreleri_sifirla` ile AYNI ders, alacaklı/borçlu
  // için zaten bilinen aynı hata sınıfı — kullanıcı bulgusu, 2026-07-12/13).
  // "Yenile" ile AYNI kapsamı tazelerken bu ÇAĞRILMAZ — kullanıcının
  // filtresi kalır.
  function yerelFiltreleriSifirla() {
    KOLONLAR.forEach(function (c) { if (colFilters[c[0]]) colFilters[c[0]].value = ""; });
    sortKey = null; sortReverse = false;
    sortEtiketleriGuncelle();
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

  // Alacaklı/Borçlu rol-bazlı arama yalnız İcra'da anlamlıdır — kullanıcı
  // bulgusu: satır önceden her yargı türünde görünüyordu, kafa karıştırıyordu.
  function icraExtraGuncelle() {
    var icraMi = (turSel.value === "İcra");
    icraExtra.style.display = icraMi ? "" : "none";
    if (!icraMi) { alacakliAdi.value = ""; borcluAdi.value = ""; }
  }

  function fillDatalist(dl, degerler) {
    dl.innerHTML = "";
    (degerler || []).forEach(function (v) {
      var o = document.createElement("option"); o.value = v; dl.appendChild(o);
    });
  }

  function taraflarSecenekleriYukle() {
    fetch("api/dosyalarim/taraf-secenekleri").then(function (r) { return r.json(); }).then(function (d) {
      fillDatalist(alacakliList, d.alacaklilar);
      fillDatalist(borcluList, d.borclular);
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
    if (alacakliAdi.value.trim()) body.alacakli_adi = alacakliAdi.value.trim();
    if (borcluAdi.value.trim()) body.borclu_adi = borcluAdi.value.trim();
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
    turSel.value = "Tümü"; birimYukle();
    mahkemeSel.value = "Tümü";
    dosyaTurSel.value = "Tümü"; durumSel.value = "Tümü";
    tarihBas.value = ""; tarihBit.value = ""; tarafAdi.value = "";
    alacakliAdi.value = ""; borcluAdi.value = "";
    icraExtraGuncelle();
    yerelFiltreleriSifirla();
    filtrele();
  }

  var yenileSinceLog = 0, yenileCalisiyor = false, yenilePaused = false;
  function yenileControls() {
    yenileBtn.disabled = yenileTumuBtn.disabled = yenileCalisiyor;
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
      // Künye bilgileri (kullanıcı bulgusu, 2026-07-13: "tıpkı uyaptaki gibi"
      // dosyanın kimlik bilgileri de görünsün) — ağ çağrısı gerekmez, satır
      // zaten kayitlarHam'da var; her yargı türünde ortaktır.
      var satirlar = [
        "Yargı Türü: " + (rec.yargi_turu_adi || "—"),
        "Yargı Birimi: " + (rec.birimAdi || "—"),
        "Dosya No: " + (rec.dosyaNo || "—"),
        "Dosya Türü: " + (rec.dosyaTur || "—"),
        "Durum: " + (rec.dosyaDurum || "—"),
        "Açılış Tarihi: " + (rec.acilisTarihi || "—"),
        ""
      ];
      if (d.aile === "icra") {
        satirlar = satirlar.concat([
          "Takibin Türü: " + (ham.takibinTuru_metin || ham.takibinTuru || "—"),
          "Takibin Şekli: " + (ham.takibinSekli_metin || ham.takibinSekli || "—"),
          "Takibin Yolu: " + (ham.takibinYolu_metin || ham.takibinYolu || "—"),
          "Alacak Kalemi Toplam: " + (ham.alacakKalemToplamTutar || "—"),
          "Vekalet Ücreti: " + (ham.vekaletUcreti || "—"),
          "Tahsil Harcı: " + (ham.tahsilHarci || "—")
        ]);
      } else if (d.aile === "hukuk") {
        satirlar = satirlar.concat([
          "Dava Açılış Türü: " + (ham.davaAcilisTuru || "—"),
          "Dava Türleri: " + (ham.davaTurleriStr || "—"),
          "İlgili Dava Listesi: " + (ham.ilgiliDavaListesiStr || "—"),
          "Duruşma Tarihi: " + (ham.durusmaTarihi || "—")
        ]);
      } else {
        satirlar.push("Bu yargı türü için henüz ayrıntı görüntüleme desteklenmiyor.");
      }
      var taraflar = d.taraflar || [];
      if (taraflar.length) {
        satirlar.push("");
        satirlar.push("Taraf Bilgileri:");
        taraflar.forEach(function (t) {
          var satir = "  " + (t.rol || "") + ": " + (t.adi || "");
          if (t.vekil) satir += " — Vekil: " + t.vekil.replace(/^\[|\]$/g, "");
          // Kesinleşme/Tebliğ Durumu (kullanıcı bulgusu, 2026-08-04: Barkod
          // Sorgu ile hesaplanan bu veri Dosya Görüntüle'de hiç görünmüyordu)
          // — yalnız borçlu satırlarında dolu olur, bkz.
          // dosya_core._taraflar_kesinlesme_bilgisi_ekle.
          if (t.kesinlesmeDurumu) satir += " — Kesinleşme: " + t.kesinlesmeDurumu;
          if (t.tebligatDurumu) satir += " — Tebliğ: " + t.tebligatDurumu;
          satirlar.push(satir);
        });
      }
      // Barkod Sorgu (Kapalı Tebligat — PTT) modülünün DB'ye yazdığı gerçek
      // sonuçlar — masaüstü dosyalarim_genel.py._barkod_sekmesi'nin web eşi
      // (kullanıcı bulgusu, 2026-08-04: "barkod veritabanında olan veri tüm
      // dosyaları sorgulama ekranına gelmiyor" — bu, DosyaTaraf.tebligatDurumu
      // enum'undan AYRI bir veri kaynağıdır, o enum hiçbir zaman otomatik
      // doldurulmuyor). En yeniden eskiye.
      var barkodlar = d.barkodlar || [];
      if (barkodlar.length) {
        satirlar.push("");
        satirlar.push("Barkod / Tebligat Bilgileri:");
        barkodlar.forEach(function (b) {
          var satir = "  " + (b.evrakAciklama || "—") + " — Barkod: " + (b.barkod || "—") +
            " — PTT Durumu: " + (b.pttDurumu || "—");
          if (b.sonIslemTarihi) satir += " (" + b.sonIslemTarihi + ")";
          satir += " — Tebliğ Mazbatası: " + (b.tebligMazbatasiVar || "—");
          if (b.kapaliTebligMazbatasiVar === "Var") satir += ", Kapalı Mazbata: Var";
          if (b.sorguZamani) satir += " — Sorgu: " + b.sorguZamani;
          satirlar.push(satir);
        });
      }
      alert(satirlar.join("\n") + (d.kaydedildi ? "\n\n(Yerel veritabanına kaydedildi.)" : ""));
      // Kullanıcı bulgusu (2026-07-12): kaydedilen yeni taraf/ayrıntı verisi
      // tabloya hiç yansımıyordu — elle "Filtrele"ye basmadan görünmüyordu.
      if (d.kaydedildi) filtrele();
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }

  turSel.addEventListener("change", function () { birimYukle(); icraExtraGuncelle(); yerelFiltreleriSifirla(); filtrele(); });
  birimSel.addEventListener("change", function () { mahkemeYukle(); yerelFiltreleriSifirla(); filtrele(); });
  [mahkemeSel, dosyaTurSel, durumSel].forEach(function (el) {
    el.addEventListener("change", filtrele);
  });
  [tarihBas, tarihBit, tarafAdi, alacakliAdi, borcluAdi].forEach(function (el) {
    el.addEventListener("keydown", function (e) { if (e.key === "Enter") filtrele(); });
  });
  filtreleBtn.addEventListener("click", filtrele);
  temizleBtn.addEventListener("click", temizle);
  yenileBtn.addEventListener("click", yenile);
  yenileTumuBtn.addEventListener("click", yenileTumu);
  detayBtn.addEventListener("click", dosyaGoruntule);

  buildHead();
  mahkemeYukle();
  icraExtraGuncelle();
  taraflarSecenekleriYukle();
  loadFields().then(filtrele);
})();
