// SGK Sorgu — orijinal SorguMotoru + özetleyicileri (sunucuda) sürer
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var fileInput = $("sgk-file");
  var startBtn = $("sgk-start"), retryBtn = $("sgk-retry"), pauseBtn = $("sgk-pause"),
      stopBtn = $("sgk-stop"), statusEl = $("sgk-status");
  var queriesEl = $("sgk-queries"), filterEl = $("sgk-filter");
  var headEl = $("sgk-head"), bodyEl = $("sgk-body"), detailEl = $("sgk-detail");
  var logEl = $("sgk-log");
  if (!startBtn) return;

  var columns = [], queries = [], loaded = false;
  var sinceLog = 0, sinceRev = 0;
  var rowEls = {};       // r -> <tr>
  var running = false, paused = false;
  var filter = "";

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

  function setControls() {
    startBtn.disabled = retryBtn.disabled = fileInput.disabled = (!loaded || running);
    pauseBtn.disabled = !running;
    stopBtn.disabled = !running;
    pauseBtn.textContent = paused ? "Devam" : "Duraklat";
  }

  // ── Excel yükle ──
  fileInput.addEventListener("change", function () {
    var f = fileInput.files[0];
    if (!f) return;
    statusEl.textContent = "● Yükleniyor…";
    var reader = new FileReader();
    reader.onload = function () {
      var b64 = reader.result.split(",")[1] || "";
      post("api/sgk/upload", { filename: f.name, data_b64: b64 }).then(function (d) {
        statusEl.textContent = "";
        if (!d.ok) { log("[HATA] " + (d.msg || "yüklenemedi")); return; }
        columns = d.columns; queries = d.queries; loaded = true;
        sinceLog = 0; sinceRev = 0; rowEls = {};
        buildHead(); buildQueries(); bodyEl.innerHTML = ""; detailEl.textContent = "";
        log("[BİLGİ] " + d.n + " satır yüklendi.");
        setControls();
      }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
    };
    reader.readAsDataURL(f);
  });

  function buildHead() {
    headEl.innerHTML = "";
    columns.forEach(function (c) {
      var th = document.createElement("th"); th.textContent = c; headEl.appendChild(th);
    });
  }

  function buildQueries() {
    queriesEl.innerHTML = "<span class='q-label'>Sorgulanacak sütunlar:</span>";
    queries.forEach(function (q) {
      var lab = document.createElement("label"); lab.className = "q-chip";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = true; cb.value = q.key;
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(q.label));
      queriesEl.appendChild(lab);
    });
  }

  function selectedKeys() {
    return Array.prototype.slice.call(queriesEl.querySelectorAll("input:checked"))
      .map(function (cb) { return cb.value; });
  }

  // ── satır ekle/güncelle ──
  function upsertRow(row) {
    var tr = rowEls[row.r];
    if (!tr) {
      tr = document.createElement("tr");
      tr.dataset.r = row.r;
      rowEls[row.r] = tr;
      bodyEl.appendChild(tr);
    }
    tr.className = row.tag || "";
    tr.innerHTML = "";
    row.values.forEach(function (v, ci) {
      var td = document.createElement("td");
      td.textContent = v == null ? "" : String(v);
      td.title = "tıkla → detay";
      td.addEventListener("click", function () { showDetail(row.r, ci); });
      tr.appendChild(td);
    });
    applyFilter(tr);
  }

  function showDetail(r, c) {
    fetch("api/sgk/cell?r=" + r + "&c=" + c).then(function (x) { return x.json(); })
      .then(function (d) {
        var title = columns[c] || "";
        detailEl.textContent = "【 " + title + " 】\n\n" + (d.value || "");
      }).catch(function () {});
  }

  // ── filtre ──
  function applyFilter(tr) {
    if (!filter) { tr.style.display = ""; return; }
    tr.style.display = (tr.textContent.toLowerCase().indexOf(filter) >= 0) ? "" : "none";
  }
  filterEl.addEventListener("input", function () {
    filter = filterEl.value.trim().toLowerCase();
    Object.keys(rowEls).forEach(function (r) { applyFilter(rowEls[r]); });
  });

  // ── kontroller ──
  startBtn.addEventListener("click", function () { begin("normal"); });
  retryBtn.addEventListener("click", function () { begin("retry"); });
  function begin(mode) {
    var sel = selectedKeys();
    if (!sel.length) { log("En az bir sorgu sütunu seçin."); return; }
    window.topluIs.baslat(function (extra) {
      var body = { mode: mode, selected: sel };
      for (var k in extra) body[k] = extra[k];
      return post("api/sgk/start", body);
    }, function (t) { statusEl.textContent = "● " + t; }).then(function (d) {
      if (!d.ok && !d.cakisma) log(mode === "retry" ? "Tekrar denenecek hatalı hücre yok." : "Başlatılamadı.");
    });
  }
  pauseBtn.addEventListener("click", function () { post("api/sgk/pause"); });
  stopBtn.addEventListener("click", function () { post("api/sgk/stop"); });

  // ── durum polling ──
  function poll() {
    fetch("api/sgk/status?log=" + sinceLog + "&rev=" + sinceRev)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.loaded) return;
        loaded = true;
        if (s.logs && s.logs.length) { s.logs.forEach(log); sinceLog = s.log_n; }
        if (s.rows && s.rows.length) { s.rows.forEach(upsertRow); sinceRev = s.rev; }
        if (s.status) statusEl.textContent = "● " + s.status;
        if (running !== s.running || paused !== s.paused) {
          running = s.running; paused = s.paused; setControls();
        }
      })
      .catch(function () {})
      .finally(function () { setTimeout(poll, 1000); });
  }

  setControls();
  poll();
})();
