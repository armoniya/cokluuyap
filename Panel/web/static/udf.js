// UDF İşlemleri — orijinal converter/signer/verify mantığını (sunucuda) sürer.
// Sol kart: dönüştür (+imzala). Sağ kart: hazır UDF'i olduğu gibi imzala (sürükle-bırak).
// Akıllı kart ayarları (DLL + PIN) bir butonla açılan modal penceredir.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var fileInput = $("udf-file"), fileInfo = $("udf-fileinfo");
  var dllInput = $("udf-dll"), dllList = $("udf-dll-list"), pinInput = $("udf-pin");
  var signBtn = $("udf-sign"), convBtn = $("udf-convert"), signUdfBtn = $("udf-signudf");
  var statusEl = $("udf-status"), logEl = $("udf-log");
  // sağ kart (hazır UDF imzala)
  var dropZone = $("udf-drop"), dropText = $("udf-drop-text"), signFile = $("udf-sign-file");
  // modal
  var modal = $("udf-card-modal"), cardOpen = $("udf-card-open"),
      cardClose = $("udf-card-close"), cardOk = $("udf-card-ok");
  if (!signBtn) return;

  var picked = null;      // {name, b64}  — dönüştürülecek belge
  var pickedUdf = null;   // {name, b64}  — imzalanacak hazır UDF

  function log(line, cls) {
    var span = document.createElement("span");
    span.textContent = line + "\n";
    if (cls) span.className = "log-" + cls;
    logEl.appendChild(span);
    logEl.scrollTop = logEl.scrollHeight;
  }

  // ── Akıllı kart ayarları (modal) ──
  function openCard() { modal.hidden = false; setTimeout(function () { dllInput.focus(); }, 30); }
  function closeCard() { modal.hidden = true; }
  if (cardOpen) cardOpen.addEventListener("click", openCard);
  if (cardClose) cardClose.addEventListener("click", closeCard);
  if (cardOk) cardOk.addEventListener("click", closeCard);
  if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeCard(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && modal && !modal.hidden) closeCard();
  });

  function fillDlls(dlls) {
    dllList.innerHTML = "";
    dlls.forEach(function (p, i) {
      var o = document.createElement("option"); o.value = p; dllList.appendChild(o);
      if (i === 0 && !dllInput.value) dllInput.value = p;
    });
  }

  // DLL'i otomatik bul: önce hızlı yollar, bulamazsa bilgisayarda derin ara
  fetch("api/udf/dlls").then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) return;
      if (!d.ready) { log("[UYARI] UDF modülü hazır değil: " + (d.err || ""), "warn"); return; }
      if (d.dlls && d.dlls.length) {
        fillDlls(d.dlls);
        log("[BAŞARILI] " + d.dlls.length + " adet PKCS#11 DLL otomatik bulundu.", "ok");
        return;
      }
      log("[BİLGİ] PKCS#11 DLL bilinen yollarda yok; bilgisayarda aranıyor…", "info");
      statusEl.textContent = "● DLL aranıyor…"; statusEl.classList.add("on");
      fetch("api/udf/dlls?deep=1").then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d2) {
          statusEl.textContent = ""; statusEl.classList.remove("on");
          if (d2 && d2.dlls && d2.dlls.length) {
            fillDlls(d2.dlls);
            log("[BAŞARILI] " + d2.dlls.length + " PKCS#11 DLL bulundu: " + d2.dlls[0], "ok");
          } else {
            log("[UYARI] DLL otomatik bulunamadı. 'Akıllı Kart Ayarları'ndan sürücü " +
                "dosyasını (ör. akisp11.dll) elle girin.", "warn");
          }
        }).catch(function () { statusEl.textContent = ""; });
    }).catch(function () {});

  function readAsB64(f, cb) {
    var reader = new FileReader();
    reader.onload = function () { cb(reader.result.split(",")[1] || ""); };
    reader.readAsDataURL(f);
  }

  // ── Sol kart: dönüştürülecek belge ──
  fileInput.addEventListener("change", function () {
    var f = fileInput.files[0];
    if (!f) { picked = null; fileInfo.textContent = "Dosya seçilmedi."; return; }
    readAsB64(f, function (b64) {
      picked = { name: f.name, b64: b64 };
      fileInfo.textContent = f.name + "  (" + Math.round(f.size / 1024) + " KB)";
    });
  });

  // ── Sağ kart: imzalanacak hazır UDF (sürükle-bırak / tıkla-seç) ──
  function acceptUdf(f) {
    if (!f) return;
    if (!/\.udf$/i.test(f.name)) { log("[HATA] Yalnızca .udf dosyası bırakabilirsiniz.", "error"); return; }
    readAsB64(f, function (b64) {
      pickedUdf = { name: f.name, b64: b64 };
      dropText.innerHTML = "";
      dropText.textContent = f.name + "  (" + Math.round(f.size / 1024) + " KB)";
      dropZone.classList.add("has-file");
    });
  }
  if (dropZone) {
    dropZone.addEventListener("click", function () { signFile.click(); });
    signFile.addEventListener("change", function () { acceptUdf(signFile.files[0]); });
    ["dragenter", "dragover"].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation(); dropZone.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation(); dropZone.classList.remove("drag");
      });
    });
    dropZone.addEventListener("drop", function (e) {
      var dt = e.dataTransfer;
      if (dt && dt.files && dt.files.length) acceptUdf(dt.files[0]);
    });
  }

  function busy(on) {
    signBtn.disabled = convBtn.disabled = on;
    if (signUdfBtn) signUdfBtn.disabled = on;
    statusEl.textContent = on ? "● İşleniyor…" : "";
    statusEl.classList.toggle("on", on);
  }

  function needCard() {
    if (!dllInput.value.trim() || !pinInput.value.trim()) {
      log("[HATA] İmzalama için DLL yolu ve PIN gerekli. 'Akıllı Kart Ayarları'nı açın.", "error");
      openCard();
      return false;
    }
    return true;
  }

  function download(name, b64) {
    var bin = atob(b64), len = bin.length, bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    var blob = new Blob([bytes], { type: "application/octet-stream" });
    var url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
  }

  function handleResult(d) {
    (d.logs || []).forEach(function (ln) {
      var cls = ln.indexOf("HATA") >= 0 ? "error" :
        (ln.indexOf("OK") >= 0 || ln.indexOf("TEBRİKLER") >= 0 || ln.indexOf("BAŞARILI") >= 0) ? "ok" : null;
      log(ln, cls);
    });
    if (d.ok && d.data_b64) {
      download(d.filename || "belge.udf", d.data_b64);
      log("[BAŞARILI] UDF indirildi: " + (d.filename || "belge.udf"), "ok");
    }
  }

  // ── Dönüştür / Dönüştür ve imzala ──
  function run(mode) {
    if (!picked) { log("[HATA] Lütfen bir giriş belgesi seçin.", "error"); return; }
    if (mode === "sign" && !needCard()) return;
    busy(true);
    log("[BİLGİ] " + (mode === "sign" ? "Dönüştürülüyor ve imzalanıyor" : "Dönüştürülüyor") +
        ": " + picked.name + " …", "info");
    fetch("api/udf/convert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: picked.name, data_b64: picked.b64, mode: mode,
        dll: dllInput.value.trim(), pin: pinInput.value.trim()
      })
    }).then(function (r) { return r.json(); }).then(handleResult)
      .catch(function (e) { log("[HATA] İstek başarısız: " + e, "error"); })
      .finally(function () { busy(false); });
  }

  // ── Hazır UDF'i olduğu gibi imzala ──
  function runSignExisting() {
    if (!pickedUdf) { log("[HATA] Lütfen imzalanacak bir UDF dosyası seçin (ya da sürükleyin).", "error"); return; }
    if (!needCard()) return;
    busy(true);
    log("[BİLGİ] UDF imzalanıyor (dönüştürmeden): " + pickedUdf.name + " …", "info");
    fetch("api/udf/sign-existing", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: pickedUdf.name, data_b64: pickedUdf.b64,
        dll: dllInput.value.trim(), pin: pinInput.value.trim()
      })
    }).then(function (r) { return r.json(); }).then(handleResult)
      .catch(function (e) { log("[HATA] İstek başarısız: " + e, "error"); })
      .finally(function () { busy(false); });
  }

  signBtn.addEventListener("click", function () { run("sign"); });
  convBtn.addEventListener("click", function () { run("convert"); });
  if (signUdfBtn) signUdfBtn.addEventListener("click", runSignExisting);
  $("udf-clear").addEventListener("click", function () { logEl.textContent = ""; });
})();
