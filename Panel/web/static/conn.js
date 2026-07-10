// UYAP Bağlantısı modülü — orijinal office_agent/home_client mantığını (sunucuda) sürer.
// Kullanıcı ayrıntılı günlükle ilgilenmez; bunun yerine tek, belirgin bir görsel durum
// gösterilir: Bağlanılıyor → Bağlantı kuruldu → Bağlantı hatası.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var pin = $("conn-pin"), savePin = $("conn-save-pin");
  var shareBtn = $("conn-share"), shareBrowser = $("conn-share-browser"), shareStatus = $("conn-share-status");
  var recvBtn = $("conn-receive"), recvBrowser = $("conn-receive-browser"), recvStatus = $("conn-receive-status");
  var stateEl = $("conn-state"), stateTitle = $("conn-state-title"), stateSub = $("conn-state-sub");
  var chip = $("conn-chip");
  if (!shareBtn) return;

  var logN = 0, sharing = false, receiving = false;
  var GIRIS = "http://127.0.0.1:8800/giris";

  // Görsel durum makinesi için iç bayraklar (günlük ekranda gösterilmez, yalnızca okunur).
  var pendingShare = false, pendingReceive = false;   // başlatıldı, henüz kurulmadı
  var shareReady = false, recvReady = false;           // P2P/oturum gerçekten hazır
  var errMsg = "";                                      // son [HATA] satırı

  function post(url, body) {
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  // Sunucu günlüğünü ekrana basmıyoruz; yalnızca anlamlı durum işaretlerini süzüyoruz.
  function scanLogs(lines) {
    if (!lines || !lines.length) return;
    lines.forEach(function (raw) {
      var ln = String(raw);
      if (ln.indexOf("[HATA]") >= 0) {
        errMsg = ln.replace(/^.*\[HATA\]\s*/, "").trim();
        return;
      }
      // Paylaşım tarafı hazır sinyalleri
      if (ln.indexOf("oda hazır") >= 0 || ln.indexOf("Signaling'e bağlandı") >= 0 ||
          ln.indexOf("DataChannel açıldı") >= 0) shareReady = true;
      // Alıcı tarafı hazır sinyalleri
      if (ln.indexOf("P2P kanal açık") >= 0 || ln.indexOf("Doğrudan LAN bağlantısı") >= 0)
        recvReady = true;
    });
  }

  function render(state, title, sub) {
    stateEl.setAttribute("data-state", state);
    stateTitle.textContent = title;
    stateSub.textContent = sub;
    chip.className = "conn-chip is-" + state;
    chip.textContent = "● " + title.replace(/…$/, "");
  }

  // sharing/receiving + iç bayraklardan tek bir genel durum türet.
  function refreshState() {
    var active = sharing || receiving;
    var connected = (sharing && shareReady) || (receiving && recvReady);
    var connecting = (sharing || pendingShare || receiving || pendingReceive) && !connected;

    if (connected) {
      var sub = sharing ? "Oturumunuz paylaşıma açık; istemciler bağlanabilir."
                        : "Ofis bilgisayarına bağlandınız.";
      render("connected", "Bağlantı kuruldu", sub);
    } else if (connecting) {
      render("connecting", "Bağlanılıyor…", "UYAP oturumu hazırlanıyor, lütfen bekleyin.");
    } else if (errMsg) {
      render("error", "Bağlantı hatası", errMsg || "Bağlantı kurulamadı. Lütfen tekrar deneyin.");
    } else {
      render("idle", "Bağlantı bekleniyor",
             "Paylaşmak ya da bir ofise bağlanmak için yukarıdan bir işlem başlatın.");
    }
  }

  function applyState(s) {
    if (s.sharing !== sharing) {
      sharing = s.sharing;
      shareBtn.textContent = sharing ? "Paylaşımı Durdur" : "Bağlantıyı Paylaş";
      shareBtn.classList.toggle("stop", sharing);
      shareBrowser.disabled = !sharing;
      shareStatus.textContent = sharing ? "● Paylaşım aktif" : "● Paylaşım durduruldu";
      shareStatus.classList.toggle("on", sharing);
      pin.disabled = sharing;
      if (!sharing) { pendingShare = false; shareReady = false; }
    }
    if (s.receiving !== receiving) {
      receiving = s.receiving;
      recvBtn.textContent = receiving ? "Bağlantıyı Kes" : "Bağlantıyı Al";
      recvBtn.classList.toggle("stop", receiving);
      recvBrowser.disabled = !receiving;
      recvStatus.textContent = receiving ? "● Bağlantı aktif" : "● Bağlantı yok";
      recvStatus.classList.toggle("on", receiving);
      if (!receiving) { pendingReceive = false; recvReady = false; }
    }
    refreshState();
  }

  function poll() {
    fetch("api/conn/status?since=" + logN)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (s) { scanLogs(s.logs); logN = s.n; applyState(s); }
      })
      .catch(function () {})
      .finally(function () { setTimeout(poll, 1100); });
  }

  shareBtn.addEventListener("click", function () {
    if (sharing) { post("api/conn/share/stop"); return; }
    if (!pin.value.trim()) {
      shareStatus.textContent = "● PIN kodu gerekli";
      errMsg = "E-imza PIN kodu gerekli."; refreshState(); return;
    }
    errMsg = ""; shareReady = false; pendingShare = true; refreshState();
    post("api/conn/share", { pin: pin.value.trim(), save_pin: !!(savePin && savePin.checked) })
      .then(function (d) {
        if (!d.ok) {
          pendingShare = false;
          errMsg = (d.msg || (d.logs && d.logs[0]) || "Paylaşım başlatılamadı.");
          shareStatus.textContent = "● " + errMsg;
          refreshState();
        }
      });
  });

  recvBtn.addEventListener("click", function () {
    if (receiving) { post("api/conn/receive/stop"); return; }
    errMsg = ""; recvReady = false; pendingReceive = true; refreshState();
    post("api/conn/receive").then(function (d) {
      if (!d.ok) {
        pendingReceive = false;
        errMsg = (d.msg || (d.logs && d.logs[0]) || "Bağlantı başlatılamadı.");
        recvStatus.textContent = "● " + errMsg;
        refreshState();
      }
    });
  });

  shareBrowser.addEventListener("click", function () { window.open(GIRIS, "_blank"); });
  recvBrowser.addEventListener("click", function () { window.open(GIRIS, "_blank"); });

  refreshState();
  poll();
})();
