// Toplu İş Çakışma Diyaloğu — paylaşılan (Dosya Sorgulama/Barkod/SGK/Üretilen
// Modüller ortak kullanır). Masaüstündeki toplu_is_dialog.py'nin web eşidir:
// bir toplu iş zaten çalışırken yenisi başlatılmak istendiğinde kullanıcıya
// "Sıraya Koy / Karma Çalıştır / İptal" seçeneklerini sunar. Görsel olarak
// mevcut .modal-overlay/.modal-card deseni (bkz. udf-card modalı) yeniden kullanılır.
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[<>&]/g, function (c) {
      return { "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c];
    });
  }

  function cakismaSor(calisanAdlari) {
    return new Promise(function (resolve) {
      var adMetni = (calisanAdlari && calisanAdlari.length) ? calisanAdlari.join(", ") : "bilinmeyen bir iş";
      var overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML =
        '<div class="modal-card">' +
          '<span class="legend">Toplu İş</span>' +
          "<h3>Yürümekte olan bir toplu iş var</h3>" +
          '<p class="conn-hint">Şu anda çalışan: <b>' + esc(adMetni) + "</b></p>" +
          '<p class="conn-hint">Bu yeni işi nasıl başlatalım?<br>' +
            "• <b>Sıraya Koy</b> — diğer iş bitince otomatik başlar.<br>" +
            "• <b>Karma Çalıştır</b> — iki iş katı sırayla nöbetleşir (bir kayıt " +
            "o işten, bir kayıt bu işten); UYAP'a asla eşzamanlı istek gitmez.</p>" +
          '<div class="modal-actions">' +
            '<button class="conn-btn ghost inline" data-v="iptal">İptal</button>' +
            '<button class="conn-btn ghost inline" data-v="karma">Karma Çalıştır</button>' +
            '<button class="conn-btn inline" data-v="sira">Sıraya Koy</button>' +
          "</div>" +
        "</div>";
      document.body.appendChild(overlay);
      overlay.addEventListener("click", function (e) {
        var v = e.target && e.target.getAttribute && e.target.getAttribute("data-v");
        if (!v) return;
        document.body.removeChild(overlay);
        resolve(v);
      });
    });
  }

  // startFn(extraBody) -> Promise<yanıt JSON>; sunucu çakışma yoksa {ok:true,...},
  // çakışma varsa {ok:false, cakisma:"cakisma"|"sirada", calisan:[...]} döner
  // (bkz. server.py _toplu_is_basvur). onDurum(text) opsiyonel — "Sırada
  // bekliyor…" gibi ilerleme metni göstermek için.
  function baslat(startFn, onDurum) {
    function girisim(mod) {
      return startFn(mod ? { mod: mod } : {}).then(function (d) {
        if (d && d.ok) return d;
        if (d && d.cakisma === "cakisma") {
          return cakismaSor(d.calisan).then(function (secim) {
            if (secim === "iptal") return d;
            if (secim === "karma") return girisim("karma");
            if (onDurum) onDurum("Sırada bekliyor…");
            return bekleVeTekrarDene();
          });
        }
        if (d && d.cakisma === "sirada") {
          if (onDurum) onDurum("Sırada bekliyor…");
          return bekleVeTekrarDene();
        }
        return d;
      });
    }
    function bekleVeTekrarDene() {
      return new Promise(function (resolve) {
        setTimeout(function () { girisim("sira").then(resolve); }, 2000);
      });
    }
    return girisim(null);
  }

  window.topluIs = { cakismaSor: cakismaSor, baslat: baslat };
})();
