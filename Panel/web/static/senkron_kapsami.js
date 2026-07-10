// Senkron Kapsamı — hangi yargı türü/birimi kombinasyonlarının arka planda
// taranıp indirileceğini seçme ekranı. Aynı SenkronKapsami/YargiBirimi
// tablolarını masaüstüyle PAYLAŞIR (server.py -> dosya_core.py; bkz.
// docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7/§8). Bir türün listesini
// yenilemek yalnız O KARTI günceller — diğer kartlardaki henüz kaydedilmemiş
// işaretlemeler bozulmaz (masaüstü senkron_kapsami.py'nin frame-bazlı
// yeniden-çizim deseniyle aynı fikir).
(function () {
  "use strict";

  var grid = document.getElementById("sk-grid");
  var statusEl = document.getElementById("sk-status");
  var saveBtn = document.getElementById("sk-save");
  if (!grid) return;

  function setStatus(text, ok) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("on", !!ok);
  }

  function buildCard(t) {
    var kart = document.createElement("div");
    kart.className = "conn-card sk-card";
    kart.dataset.turu = t.kod;

    var ust = document.createElement("div");
    ust.className = "sk-card-head";
    var h3 = document.createElement("h3");
    h3.textContent = t.ad;
    ust.appendChild(h3);

    var tumuLabel = document.createElement("label");
    tumuLabel.className = "sk-tumu";
    var tumuCb = document.createElement("input");
    tumuCb.type = "checkbox";
    tumuCb.className = "sk-tumu-cb";
    tumuCb.addEventListener("change", function () {
      toggleBirimDisabled(t.kod, tumuCb.checked);
    });
    tumuLabel.appendChild(tumuCb);
    tumuLabel.appendChild(document.createTextNode(" Tümü (bu yargı türünün tamamı)"));
    ust.appendChild(tumuLabel);
    kart.appendChild(ust);

    var list = document.createElement("div");
    list.className = "sk-birim-list";
    kart.appendChild(list);

    var yenile = document.createElement("button");
    yenile.type = "button";
    yenile.className = "link-btn sk-yenile";
    yenile.textContent = "↻ Listeyi UYAP'tan Getir/Yenile";
    yenile.addEventListener("click", function () { refreshBirimler(t.kod); });
    kart.appendChild(yenile);

    return kart;
  }

  function toggleBirimDisabled(turu, tumuOn) {
    var list = grid.querySelector('.sk-card[data-turu="' + turu + '"] .sk-birim-list');
    if (!list) return;
    list.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      cb.disabled = tumuOn;
    });
  }

  function fillBirimList(turu, birimler, aktifKodlar, tumuAktif) {
    var kart = grid.querySelector('.sk-card[data-turu="' + turu + '"]');
    if (!kart) return;
    var tumuCb = kart.querySelector(".sk-tumu-cb");
    if (tumuCb) tumuCb.checked = tumuAktif;
    var list = kart.querySelector(".sk-birim-list");
    list.innerHTML = "";
    if (!birimler || !birimler.length) {
      var bos = document.createElement("small");
      bos.className = "sk-bos";
      bos.textContent = "Yargı birimi listesi henüz alınmadı (ofis bağlantısı kapalı olabilir).";
      list.appendChild(bos);
    }
    (birimler || []).forEach(function (b) {
      var lbl = document.createElement("label");
      lbl.className = "sk-birim";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = aktifKodlar.indexOf(b.kod) !== -1;
      cb.disabled = tumuAktif;
      cb.dataset.kod = b.kod;
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(" " + b.ad + "  (" + b.kod + ")"));
      list.appendChild(lbl);
    });
  }

  function aktifOzet(secili, turu) {
    var kodlar = secili.filter(function (s) {
      return s.aktif && s.yargi_turu === turu && s.yargi_birimi_kod;
    }).map(function (s) { return s.yargi_birimi_kod; });
    var tumu = secili.some(function (s) {
      return s.aktif && s.yargi_turu === turu && !s.yargi_birimi_kod;
    });
    return { kodlar: kodlar, tumu: tumu };
  }

  function refreshBirimler(turu) {
    setStatus("Yargı Birimi listesi çekiliyor…", false);
    fetch("api/senkron-kapsami/yenile?yargi_turu=" + encodeURIComponent(turu))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          setStatus((d && d.msg) || "Liste alınamadı.", false);
          return;
        }
        // Kaydedilmiş durumu tazele (bu türe ait) — diğer kartlara dokunmadan.
        fetch("api/senkron-kapsami").then(function (r2) { return r2.json(); }).then(function (durum) {
          var ozet = aktifOzet((durum && durum.secili) || [], turu);
          fillBirimList(turu, d.birimler || [], ozet.kodlar, ozet.tumu);
          setStatus("✔ Liste güncellendi.", true);
        }).catch(function () {
          fillBirimList(turu, d.birimler || [], [], false);
          setStatus("✔ Liste güncellendi.", true);
        });
      }).catch(function () { setStatus("Sunucuya ulaşılamadı.", false); });
  }

  function load() {
    fetch("api/senkron-kapsami").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) {
        setStatus((d && d.err) || "Senkron Kapsamı modülü hazır değil.", false);
        return;
      }
      var turler = d.turler || [];
      var secili = d.secili || [];
      var birimler = d.birimler || {};
      grid.innerHTML = "";
      turler.forEach(function (t) { grid.appendChild(buildCard(t)); });
      turler.forEach(function (t) {
        var ozet = aktifOzet(secili, t.kod);
        fillBirimList(t.kod, birimler[String(t.kod)] || [], ozet.kodlar, ozet.tumu);
      });
    }).catch(function () { setStatus("Sunucuya ulaşılamadı.", false); });
  }

  function save() {
    var secimler = [];
    grid.querySelectorAll(".sk-card").forEach(function (kart) {
      var turu = parseInt(kart.dataset.turu, 10);
      var tumuCb = kart.querySelector(".sk-tumu-cb");
      if (tumuCb && tumuCb.checked) {
        secimler.push([turu, ""]);
        return;
      }
      kart.querySelectorAll(".sk-birim-list input[type=checkbox]").forEach(function (cb) {
        if (cb.checked && !cb.disabled) secimler.push([turu, cb.dataset.kod]);
      });
    });
    setStatus("Kaydediliyor…", false);
    fetch("api/senkron-kapsami", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secimler: secimler })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) setStatus("✔ Kaydedildi (" + d.kayit + " kapsam aktif).", true);
      else setStatus((d && d.msg) || "Kaydedilemedi.", false);
    }).catch(function () { setStatus("Sunucuya ulaşılamadı.", false); });
  }

  if (saveBtn) saveBtn.addEventListener("click", save);
  load();
})();
