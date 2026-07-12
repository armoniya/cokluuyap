// Ayarlar — açılışta otomatik UYAP bağlantısı tercihi.
// Tercih sunucudaki ORTAK config'e (masaüstüyle aynı uyap_app config) yazılır;
// /api/settings GET/POST. Seçim değişince anında kaydedilir.
(function () {
  "use strict";

  var wrap = document.getElementById("set-auto");
  var statusEl = document.getElementById("set-status");

  var vekilInput = document.getElementById("set-kendi-vekil");
  var vekilBtn = document.getElementById("set-kendi-vekil-kaydet");
  var vekilStatusEl = document.getElementById("set-kendi-vekil-status");

  function setStatus(text, ok) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("on", !!ok);
  }

  function setVekilStatus(text, ok) {
    if (!vekilStatusEl) return;
    vekilStatusEl.textContent = text || "";
    vekilStatusEl.classList.toggle("on", !!ok);
  }

  var radios = wrap ? wrap.querySelectorAll('input[name="auto_connect"]') : null;

  function selectValue(value) {
    if (!radios) return;
    radios.forEach(function (r) { r.checked = (r.value === value); });
  }

  if (wrap) {
    // Değişince kaydet
    radios.forEach(function (r) {
      r.addEventListener("change", function () {
        if (!r.checked) return;
        setStatus("Kaydediliyor…", false);
        fetch("api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auto_connect: r.value })
        }).then(function (resp) { return resp.json(); }).then(function (d) {
          if (d && d.ok) setStatus("✔ Kaydedildi.", true);
          else setStatus((d && d.msg) || "Kaydedilemedi.", false);
        }).catch(function () { setStatus("Sunucuya ulaşılamadı.", false); });
      });
    });
  }

  // Kendi vekil ad(lar)ı — kalabalık İcra dosyalarında "bizim taraf"ı
  // seçmek için (bkz. dosya_core._kendi_vekil_adlari, kullanıcı bulgusu
  // 2026-07-12). Serbest metin olduğundan değişince değil, düğmeyle kaydedilir.
  if (vekilBtn) {
    vekilBtn.addEventListener("click", function () {
      setVekilStatus("Kaydediliyor…", false);
      fetch("api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kendi_vekil_adlari: (vekilInput && vekilInput.value) || "" })
      }).then(function (resp) { return resp.json(); }).then(function (d) {
        if (d && d.ok) setVekilStatus("✔ Kaydedildi.", true);
        else setVekilStatus((d && d.msg) || "Kaydedilemedi.", false);
      }).catch(function () { setVekilStatus("Sunucuya ulaşılamadı.", false); });
    });
  }

  // Mevcut tercihleri yükle
  fetch("api/settings").then(function (r) { return r.json(); }).then(function (d) {
    if (!d) return;
    if (wrap && d.auto_connect) selectValue(d.auto_connect);
    if (vekilInput && d.kendi_vekil_adlari) vekilInput.value = d.kendi_vekil_adlari;
  }).catch(function () {});
})();
