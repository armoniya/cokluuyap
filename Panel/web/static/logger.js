// UYAP Oturum Kaydı (Logger) — masaüstü LoggerPanel'in web eşi.
// İŞ MANTIĞI sunucudaki logger_core.SessionLogger'dadır (tarayıcı 8800 ofis
// proxy'sine bağlanıp trafiği yakalar; yakalanan çağrılardan *_core.py modülü
// üretir). Burada yalnızca sunum: canlı günlük + endpoint inceleme/karar arayüzü.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  if (!$("lg-baslat")) return;

  var KOVA_SIRA = ["sabit", "girdi", "ic"];
  var KOVA_GOSTER = { sabit: "🔒 sabit", girdi: "✏️ girdi", ic: "⚙️ iç" };
  var sinceLog = 0, running = false;
  var endpoints = [], seciliEp = null;
  var kovaSt = {}, paramSt = {}, gosterSt = {}, baslikSt = {}, yanitSira = {}, epData = {};

  function log(line) {
    var el = $("lg-log");
    el.appendChild(document.createTextNode(line + "\n"));
    el.scrollTop = el.scrollHeight;
  }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
  }

  // ── sekmeler ──
  Array.prototype.forEach.call(document.querySelectorAll(".lg-tab"), function (tab) {
    tab.addEventListener("click", function () {
      var t = tab.getAttribute("data-tab");
      Array.prototype.forEach.call(document.querySelectorAll(".lg-tab"), function (x) {
        x.classList.toggle("active", x === tab);
      });
      Array.prototype.forEach.call(document.querySelectorAll(".lg-pane"), function (p) {
        p.hidden = p.getAttribute("data-pane") !== t;
      });
      if (t === "inceleme") yenileEndpointler();
    });
  });

  // ── kontroller ──
  $("lg-baslat").addEventListener("click", function () {
    post("api/logger/start", {}).then(function (d) {
      if (!d.ok) { log("[HATA] " + (d.msg || "başlatılamadı")); return; }
      running = true; setControls();
    });
  });
  $("lg-durdur").addEventListener("click", function () { post("api/logger/stop", {}); });
  $("lg-temizle").addEventListener("click", function () {
    $("lg-log").textContent = "";
  });
  function setControls() {
    $("lg-baslat").disabled = running;
    $("lg-durdur").disabled = !running;
  }

  // ── canlı günlük polling ──
  function poll() {
    fetch("api/logger/status?log=" + sinceLog).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return;
        (s.logs || []).forEach(log);
        if (typeof s.log_n === "number") sinceLog = s.log_n;
        if (s.status) $("lg-durum").textContent = "● " + s.status;
        if (s.log_dir) $("lg-klasor").textContent = "Çıktı klasörü: " + s.log_dir;
        if (running !== !!s.running) { running = !!s.running; setControls(); }
      }).catch(function () {})
      .finally(function () { setTimeout(poll, 600); });
  }

  // ── Endpoint İnceleme ──
  $("lg-yenile").addEventListener("click", yenileEndpointler);
  $("lg-sifirla").addEventListener("click", function () {
    if (!confirm("Yakalanan tüm yapılandırılmış kayıtlar silinsin mi? (Canlı günlük etkilenmez.)")) return;
    post("api/logger/clear-records", {}).then(function () {
      endpoints = []; seciliEp = null;
      kovaSt = {}; paramSt = {}; gosterSt = {}; baslikSt = {}; yanitSira = {}; epData = {};
      renderEpList(); temizleDetay();
    });
  });

  function yenileEndpointler() {
    var onceki = seciliEp;
    fetch("api/logger/endpoints").then(function (r) { return r.json(); }).then(function (d) {
      endpoints = d.endpoints || [];
      endpoints.forEach(function (ep) { epData[ep.endpoint] = ep; initEpState(ep); });
      renderEpList();
      if (onceki && epData[onceki]) selectEp(onceki);
    }).catch(function () {});
  }

  function initEpState(ep) {
    var name = ep.endpoint;
    kovaSt[name] = kovaSt[name] || {};
    paramSt[name] = paramSt[name] || {};
    gosterSt[name] = gosterSt[name] || {};
    baslikSt[name] = baslikSt[name] || {};
    yanitSira[name] = (ep.yanit || []).map(function (y) { return y.alan; });
    (ep.istek || []).forEach(function (f) {
      if (kovaSt[name][f.alan] === undefined) kovaSt[name][f.alan] = f.oneri_kova;
      if (kovaSt[name][f.alan] === "girdi" && paramSt[name][f.alan] === undefined)
        paramSt[name][f.alan] = f.param_oneri;
    });
    (ep.yanit || []).forEach(function (y) {
      if (gosterSt[name][y.alan] === undefined) gosterSt[name][y.alan] = true;
      if (baslikSt[name][y.alan] === undefined) baslikSt[name][y.alan] = y.alan;
    });
  }

  function renderEpList() {
    var ul = $("lg-ep-list"); ul.innerHTML = "";
    endpoints.forEach(function (ep) {
      var li = document.createElement("li");
      li.textContent = ep.endpoint + "   (" + ep.count + ")";
      li.className = (ep.endpoint === seciliEp) ? "active" : "";
      li.addEventListener("click", function () { selectEp(ep.endpoint); });
      ul.appendChild(li);
    });
  }

  function temizleDetay() {
    $("lg-istek-head").innerHTML = ""; $("lg-istek-body").innerHTML = "";
    $("lg-yanit-body").innerHTML = ""; $("lg-uret").disabled = true; $("lg-uret-durum").textContent = "";
  }

  function selectEp(name) {
    seciliEp = name;
    renderEpList();
    var ep = epData[name];
    if (!ep) { temizleDetay(); return; }
    // İSTEK başlık
    var head = $("lg-istek-head");
    head.innerHTML = "<th>Alan</th>";
    (ep.cagri_basliklari || []).forEach(function (c) {
      var th = document.createElement("th"); th.textContent = c; head.appendChild(th);
    });
    head.insertAdjacentHTML("beforeend", "<th>Kova</th><th>Parametre</th>");
    // İSTEK gövde
    var body = $("lg-istek-body"); body.innerHTML = "";
    (ep.istek || []).forEach(function (f) {
      var tr = document.createElement("tr");
      var c = "<td>" + esc(f.alan) + "</td>";
      (f.degerler || []).forEach(function (v) { c += "<td>" + esc(v) + "</td>"; });
      tr.innerHTML = c;
      var kovaTd = document.createElement("td");
      kovaTd.style.cursor = "pointer";
      kovaTd.textContent = KOVA_GOSTER[kovaSt[name][f.alan]] || kovaSt[name][f.alan];
      kovaTd.addEventListener("click", function () {
        var cur = kovaSt[name][f.alan];
        var yeni = KOVA_SIRA[(KOVA_SIRA.indexOf(cur) + 1) % KOVA_SIRA.length];
        kovaSt[name][f.alan] = yeni;
        if (yeni === "girdi" && !paramSt[name][f.alan]) paramSt[name][f.alan] = f.param_oneri;
        selectEp(name);
      });
      tr.appendChild(kovaTd);
      var paramTd = document.createElement("td");
      if (kovaSt[name][f.alan] === "girdi") {
        paramTd.textContent = paramSt[name][f.alan] || f.param_oneri;
        paramTd.style.cursor = "pointer"; paramTd.title = "tıkla → adlandır";
        paramTd.addEventListener("click", function () {
          var v = prompt("Parametre adı:", paramSt[name][f.alan] || f.param_oneri);
          if (v != null) { paramSt[name][f.alan] = v.trim() || f.param_oneri; selectEp(name); }
        });
      } else { paramTd.textContent = "—"; }
      tr.appendChild(paramTd);
      body.appendChild(tr);
    });
    // YANIT gövde
    var yb = $("lg-yanit-body"); yb.innerHTML = "";
    (ep.yanit || []).forEach(function (y) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td>" + esc(y.alan) + "</td><td>" + esc(y.ornek) + "</td>";
      var gTd = document.createElement("td");
      gTd.style.cursor = "pointer";
      gTd.textContent = gosterSt[name][y.alan] ? "☑" : "☐";
      gTd.addEventListener("click", function () {
        gosterSt[name][y.alan] = !gosterSt[name][y.alan]; selectEp(name);
      });
      tr.appendChild(gTd);
      var bTd = document.createElement("td");
      bTd.style.cursor = "pointer"; bTd.title = "tıkla → yeniden adlandır";
      bTd.textContent = baslikSt[name][y.alan] || y.alan;
      bTd.addEventListener("click", function () {
        var v = prompt("Ekran başlığı:", baslikSt[name][y.alan] || y.alan);
        if (v != null) { baslikSt[name][y.alan] = v.trim() || y.alan; selectEp(name); }
      });
      tr.appendChild(bTd);
      yb.appendChild(tr);
    });
    $("lg-uret").disabled = false;
    $("lg-uret-durum").textContent = "";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
    });
  }

  function grupSor(ad) {
    return prompt("“" + ad + "” sol menüde hangi grup altında görünsün?", "Üretilen Modüller");
  }

  // ── üret (tek endpoint) ──
  $("lg-uret").addEventListener("click", function () {
    if (!seciliEp) return;
    var ep = epData[seciliEp];
    var ad = prompt("Üretilecek modülün adı (menüde de bu görünür):",
      seciliEp.replace(".ajx", "") + " sorgu");
    if (!ad) return;
    var grup = grupSor(ad);
    if (!grup) return;
    var kolonlar = (yanitSira[seciliEp] || []).filter(function (a) { return gosterSt[seciliEp][a]; })
      .map(function (a) { return [a, baslikSt[seciliEp][a] || a]; });
    $("lg-uret-durum").textContent = "✓ üretiliyor · canlı doğrulanıyor…";
    post("api/logger/generate", {
      endpoint: seciliEp, ad: ad, grup: grup,
      kovalar: kovaSt[seciliEp], param: paramSt[seciliEp], kolonlar: kolonlar
    }).then(uretBitti);
  });

  // ── tüm session'u modüle çevir ──
  $("lg-session").addEventListener("click", function () {
    var ad = prompt("Yakalanan oturum TEK modüle çevrilecek.\nModülün adı:", "oturum akışı");
    if (!ad) return;
    var grup = grupSor(ad);
    if (!grup) return;
    $("lg-uret-durum").textContent = "✓ üretiliyor · canlı doğrulanıyor…";
    post("api/logger/generate", { session: true, ad: ad, grup: grup }).then(uretBitti);
  });

  function uretBitti(d) {
    if (!d.ok) { $("lg-uret-durum").textContent = ""; alert(d.msg || "Üretim hatası."); log("❌ " + (d.msg || "")); return; }
    var isaret = d.dogrula && d.dogrula.ok ? "✓" : "⚠️";
    var mesaj = d.dogrula ? d.dogrula.mesaj : "";
    $("lg-uret-durum").textContent = isaret + " " + d.core + ".py · " + mesaj;
    log(isaret + " " + d.core + ".py üretildi (" + d.grup + ") · " + mesaj);
    log("   Sol menüye eklendi; görünmek için sayfayı yenilemeniz gerekebilir.");
    // Katalog değişti — menüyü tazelemek için store catalog'u yeniden çek (varsa).
    if (window.UYAP_RELOAD_CATALOG) window.UYAP_RELOAD_CATALOG();
  }

  // ── açılış ──
  setControls();
  poll();
})();
