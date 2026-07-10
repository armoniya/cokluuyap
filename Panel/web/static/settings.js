// Ayarlar — açılışta otomatik UYAP bağlantısı tercihi.
// Tercih sunucudaki ORTAK config'e (masaüstüyle aynı uyap_app config) yazılır;
// /api/settings GET/POST. Seçim değişince anında kaydedilir.
(function () {
  "use strict";

  var wrap = document.getElementById("set-auto");
  var statusEl = document.getElementById("set-status");
  if (!wrap) return;

  var radios = wrap.querySelectorAll('input[name="auto_connect"]');

  function setStatus(text, ok) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("on", !!ok);
  }

  function selectValue(value) {
    radios.forEach(function (r) { r.checked = (r.value === value); });
  }

  // Mevcut tercihi yükle
  fetch("api/settings").then(function (r) { return r.json(); }).then(function (d) {
    if (d && d.auto_connect) selectValue(d.auto_connect);
  }).catch(function () {});

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
})();
