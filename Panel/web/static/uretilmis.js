// Üretilen Modül Çalıştırıcı — masaüstü UretilmisRunner'ın web eşi.
// Logger'ın ürettiği herhangi bir ağ-sorgu modülünü TEK bileşenle sürer: sunucudan
// spec (PARAMETRELER / EXCEL_GIRDI) alınır, girdi formu kurulur, Çalıştır core.calistir'i
// 8800 ofis proxy'sine gönderir, yanıt (liste→tablo / diğer→JSON) gösterilir.
(function () {
  "use strict";
  var kurulan = {};   // key -> true (panel bir kez kurulur)

  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
  }
  function readB64(file) {
    return new Promise(function (res, rej) {
      var rd = new FileReader();
      rd.onload = function () { res(rd.result.split(",")[1] || ""); };
      rd.onerror = rej; rd.readAsDataURL(file);
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
    });
  }

  document.addEventListener("uyap:select", function (e) {
    var key = e.detail && e.detail.key;
    if (!key || !window.UYAP_URETILMIS || !(key in window.UYAP_URETILMIS)) return;
    kurPanel(key);
  });

  function kurPanel(key) {
    var sec = document.querySelector('.panel[data-panel="' + cssEsc(key) + '"]');
    if (!sec || kurulan[key]) return;
    kurulan[key] = true;
    sec.innerHTML = '<div class="panel-head"><h1 class="h1">' + esc(window.UYAP_URETILMIS[key]) + '</h1></div>' +
      '<p class="sub">Logger’ın ürettiği ağ-sorgu modülü. İstek 127.0.0.1:8800 ofis proxy’sine ' +
      'gider (UYAP Bağlantısı açık olmalı). Girdileri doldurun, Çalıştır’a basın.</p>' +
      '<div class="ur-form"></div>' +
      '<div class="sgk-toolbar"><button class="conn-btn inline ur-run">Çalıştır</button>' +
      '<span class="conn-status ur-durum"></span></div>' +
      '<div class="ur-sonuc conn-card"><p class="conn-hint">Henüz çalıştırılmadı.</p></div>' +
      '<div class="logbox ur-log"></div>';

    var formEl = sec.querySelector(".ur-form");
    var runBtn = sec.querySelector(".ur-run");
    var durumEl = sec.querySelector(".ur-durum");
    var sonucEl = sec.querySelector(".ur-sonuc");
    var logEl = sec.querySelector(".ur-log");
    var entries = {}, excelGirdi = null, seciliDosya = null, calisiyor = false;

    function log(t) { logEl.appendChild(document.createTextNode(t + "\n")); logEl.scrollTop = logEl.scrollHeight; }

    fetch("api/uretilmis/spec?key=" + encodeURIComponent(key)).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { formEl.innerHTML = '<p class="conn-hint" style="color:var(--clay)">' + esc(d.msg) + '</p>'; return; }
        (d.parametreler || []).forEach(function (p) {
          var param = p[0], etiket = p[1], ornek = p[2];
          var row = document.createElement("div"); row.className = "ur-row";
          var lab = document.createElement("label"); lab.textContent = etiket + ":";
          var inp = document.createElement("input"); inp.type = "text"; inp.className = "icra-inp wide";
          if (ornek != null) inp.value = ornek;
          entries[param] = inp;
          row.appendChild(lab); row.appendChild(inp); formEl.appendChild(row);
        });
        if (!(d.parametreler || []).length && !d.excel_girdi) {
          formEl.innerHTML = '<p class="conn-hint">(Bu sorgu girdi almıyor — tüm alanlar sabit.)</p>';
        }
        excelGirdi = d.excel_girdi;
        if (excelGirdi) {
          var row = document.createElement("div"); row.className = "ur-row";
          var lab = document.createElement("label"); lab.textContent = (excelGirdi.etiket || "Dosya") + ":";
          var fileInp = document.createElement("input"); fileInp.type = "file";
          fileInp.addEventListener("change", function () { seciliDosya = fileInp.files[0] || null; });
          row.appendChild(lab); row.appendChild(fileInp); formEl.appendChild(row);
        }
      }).catch(function () {});

    runBtn.addEventListener("click", function () {
      if (calisiyor) return;
      calisiyor = true; runBtn.disabled = true; durumEl.textContent = "● Çalışıyor…";
      var girdi = {}; Object.keys(entries).forEach(function (k) { girdi[k] = entries[k].value; });
      var gonder = function (body) {
        post("api/uretilmis/run", body).then(bitti).catch(function (e) {
          calisiyor = false; runBtn.disabled = false; durumEl.textContent = "Hata"; log("❌ " + e);
        });
      };
      if (excelGirdi && seciliDosya) {
        readB64(seciliDosya).then(function (b64) {
          gonder({ key: key, file: { filename: seciliDosya.name, data_b64: b64 } });
        });
      } else { gonder({ key: key, girdi: girdi }); }
    });

    function bitti(d) {
      calisiyor = false; runBtn.disabled = false;
      (d.logs || []).forEach(log);
      if (!d.ok) { durumEl.textContent = "Hata"; sonucEl.innerHTML = '<p class="conn-hint">Hata: ' + esc(d.msg) + '</p>'; return; }
      if (d.type === "table") {
        durumEl.textContent = d.n + " satır";
        var h = '<div class="sgk-tablewrap"><table class="sgk-table"><thead><tr>';
        (d.columns || []).forEach(function (c) { h += "<th>" + esc(c) + "</th>"; });
        h += "</tr></thead><tbody>";
        (d.rows || []).forEach(function (row) {
          h += "<tr>"; row.forEach(function (v) { h += "<td>" + esc(v) + "</td>"; }); h += "</tr>";
        });
        h += "</tbody></table></div>";
        sonucEl.innerHTML = h;
      } else {
        durumEl.textContent = "Yanıt alındı";
        var pre = document.createElement("pre");
        pre.style.cssText = "white-space:pre-wrap;margin:0;padding:12px;font-size:12.5px";
        pre.textContent = d.text || "";
        sonucEl.innerHTML = ""; sonucEl.appendChild(pre);
      }
    }
  }

  function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
  }
})();
