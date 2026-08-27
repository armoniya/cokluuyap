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

  // "GG/AA/YYYY" veya "GG/AA/YYYY SS:DD:SS" (barkod_sorgu'nun tarih alanları
  // hep bu biçimde) — lexicographic sıralama yıl/ay/gün sırasını BOZAR, bu
  // yüzden ayrıştırıp gerçek tarihe göre karşılaştırılır.
  var TARIH_RE = /^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2}):(\d{2}))?$/;
  function tarihAyristir(v) {
    var m = TARIH_RE.exec(String(v == null ? "" : v).trim());
    if (!m) return null;
    return new Date(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)).getTime();
  }

  // Tıklanan başlığa göre sırala (tarih kolonları tarihe göre, diğerleri
  // Türkçe alfabetik) ve her kolonun altında serbest metin filtresi göster.
  function renderTable(container, columns, rows) {
    var sortCol = -1, sortDir = 1, filtreler = {};

    var kolonTip = columns.map(function (_, ci) {
      var degerler = rows.map(function (r) { return r[ci]; }).filter(function (v) { return v !== "" && v != null; });
      return (degerler.length && degerler.every(function (v) { return tarihAyristir(v) !== null; })) ? "tarih" : "metin";
    });

    function ciz(odakCi) {
      var odakEl = container.querySelector('.ur-filtre[data-ci="' + odakCi + '"]');
      var imlecPos = odakEl ? odakEl.selectionStart : null;

      var filtreli = rows.filter(function (r) {
        return Object.keys(filtreler).every(function (ci) {
          var q = filtreler[ci];
          if (!q) return true;
          return String(r[ci] == null ? "" : r[ci]).toLocaleLowerCase("tr-TR").indexOf(q) !== -1;
        });
      });

      if (sortCol >= 0) {
        var ci = sortCol, dir = sortDir, tip = kolonTip[ci];
        filtreli.sort(function (a, b) {
          var av = a[ci], bv = b[ci];
          if (tip === "tarih") {
            var ad = tarihAyristir(av), bd = tarihAyristir(bv);
            if (ad === null && bd === null) return 0;
            if (ad === null) return 1;
            if (bd === null) return -1;
            return (ad - bd) * dir;
          }
          return String(av == null ? "" : av).localeCompare(
            String(bv == null ? "" : bv), "tr", { sensitivity: "base", numeric: true }) * dir;
        });
      }

      var h = '<div class="sgk-tablewrap"><table class="sgk-table"><thead><tr>';
      columns.forEach(function (c, ci) {
        var ok = sortCol === ci ? (sortDir === 1 ? " ▲" : " ▼") : "";
        h += '<th class="ur-th" data-ci="' + ci + '" title="Sıralamak için tıklayın">' + esc(c) + ok + "</th>";
      });
      h += '</tr><tr class="ur-filtrow">';
      columns.forEach(function (c, ci) {
        h += '<th><input type="text" class="icra-inp ur-filtre" data-ci="' + ci +
          '" placeholder="Filtrele…" value="' + esc(filtreler[ci] || "") + '"></th>';
      });
      h += "</tr></thead><tbody>";
      filtreli.forEach(function (row) {
        h += "<tr>"; row.forEach(function (v) { h += "<td>" + esc(v) + "</td>"; }); h += "</tr>";
      });
      h += "</tbody></table></div>" +
        '<p class="conn-hint">' + filtreli.length + " / " + rows.length + " satır</p>";
      container.innerHTML = h;

      container.querySelectorAll(".ur-th").forEach(function (th) {
        th.addEventListener("click", function () {
          var ci = +th.getAttribute("data-ci");
          if (sortCol === ci) { sortDir = -sortDir; } else { sortCol = ci; sortDir = 1; }
          ciz(null);
        });
      });
      container.querySelectorAll(".ur-filtre").forEach(function (inp) {
        inp.addEventListener("click", function (e) { e.stopPropagation(); });
        inp.addEventListener("input", function () {
          var ci2 = +inp.getAttribute("data-ci");
          var q = inp.value.toLocaleLowerCase("tr-TR").trim();
          if (q) filtreler[ci2] = q; else delete filtreler[ci2];
          ciz(ci2);
        });
      });

      if (odakEl && imlecPos != null) {
        var yeni = container.querySelector('.ur-filtre[data-ci="' + odakCi + '"]');
        if (yeni) { yeni.focus(); try { yeni.setSelectionRange(imlecPos, imlecPos); } catch (e) {} }
      }
    }

    ciz(null);
  }

  // "barkod_sorgu" katalogda uretilmis::barkod_sorgu olarak kayıtlı (Mağaza
  // sahiplik/menü girişini besler) ama artık bu jenerik çalıştırıcı YERİNE
  // kendi özel ekranına (bkz. barkod.js + index.html data-panel="barkod_sorgu")
  // sahip — burada BİLEREK atlanır, aksi halde iki script aynı panel
  // bölümünü üzerine yazmaya çalışırdı.
  var HARIC = { barkod_sorgu: true };

  document.addEventListener("uyap:select", function (e) {
    var key = e.detail && e.detail.key;
    if (!key || HARIC[key] || !window.UYAP_URETILMIS || !(key in window.UYAP_URETILMIS)) return;
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
      '<button class="conn-btn ghost inline ur-pause" disabled style="display:none">Duraklat</button>' +
      '<button class="conn-btn ghost inline ur-stop" disabled style="display:none">Durdur</button>' +
      '<span class="conn-status ur-durum"></span><span class="ur-indir"></span></div>' +
      '<div class="ur-sonuc conn-card"><p class="conn-hint">Henüz çalıştırılmadı.</p></div>' +
      '<div class="logbox ur-log"></div>';

    var formEl = sec.querySelector(".ur-form");
    var runBtn = sec.querySelector(".ur-run");
    var pauseBtn = sec.querySelector(".ur-pause");
    var stopBtn = sec.querySelector(".ur-stop");
    var durumEl = sec.querySelector(".ur-durum");
    var indirEl = sec.querySelector(".ur-indir");
    var sonucEl = sec.querySelector(".ur-sonuc");
    var logEl = sec.querySelector(".ur-log");
    var entries = {}, excelGirdi = null, seciliDosya = null, calisiyor = false, paused = false;
    var sinceLog = 0, polling = false;

    function log(t) { logEl.appendChild(document.createTextNode(t + "\n")); logEl.scrollTop = logEl.scrollHeight; }

    function setControls() {
      runBtn.disabled = calisiyor;
      if (excelGirdi) {
        pauseBtn.style.display = stopBtn.style.display = "";
        pauseBtn.disabled = !calisiyor;
        stopBtn.disabled = !calisiyor;
        pauseBtn.textContent = paused ? "Devam" : "Duraklat";
      }
    }

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
        setControls();
      }).catch(function () {});

    runBtn.addEventListener("click", function () {
      if (calisiyor) return;
      durumEl.textContent = "● Çalışıyor…"; indirEl.innerHTML = "";
      var girdi = {}; Object.keys(entries).forEach(function (k) { girdi[k] = entries[k].value; });
      var gonder = function (body) {
        window.topluIs.baslat(function (extra) {
          var b = {}; for (var k2 in body) b[k2] = body[k2]; for (var k3 in extra) b[k3] = extra[k3];
          return post("api/uretilmis/run", b);
        }, function (t) { durumEl.textContent = t; }).then(function (d) {
          if (!d.ok) {
            if (!d.cakisma) { durumEl.textContent = "Hata"; log("❌ " + (d.msg || "")); }
            return;
          }
          calisiyor = true; setControls();
          sinceLog = 0; poll();
        }).catch(function (e) {
          durumEl.textContent = "Hata"; log("❌ " + e);
        });
      };
      if (excelGirdi && seciliDosya) {
        readB64(seciliDosya).then(function (b64) {
          gonder({ key: key, file: { filename: seciliDosya.name, data_b64: b64 } });
        });
      } else { gonder({ key: key, girdi: girdi }); }
    });

    function duraklatToggle() {
      if (!calisiyor) return;
      post("api/uretilmis/pause").then(function (d) { paused = !!d.paused; setControls(); });
    }
    function durdur() {
      if (calisiyor) post("api/uretilmis/stop");
    }
    pauseBtn.addEventListener("click", duraklatToggle);
    stopBtn.addEventListener("click", durdur);

    function poll() {
      if (polling) return;
      polling = true;
      fetch("api/uretilmis/status?log=" + sinceLog).then(function (r) { return r.json(); })
        .then(function (s) {
          polling = false;
          if (!s || !s.loaded) { calisiyor = false; setControls(); return; }
          if (s.logs && s.logs.length) { s.logs.forEach(log); sinceLog = s.log_n; }
          paused = !!s.paused; setControls();
          if (s.running) { setTimeout(poll, 1000); return; }
          calisiyor = false; setControls();
          if (s.sonuc) bitti(s.sonuc);
        }).catch(function () { polling = false; calisiyor = false; setControls(); });
    }

    function bitti(d) {
      if (!d.ok) { durumEl.textContent = "Hata"; sonucEl.innerHTML = '<p class="conn-hint">Hata: ' + esc(d.msg) + '</p>'; return; }
      if (d.dosya_b64) {
        var byteChars = atob(d.dosya_b64);
        var byteNums = new Array(byteChars.length);
        for (var i = 0; i < byteChars.length; i++) byteNums[i] = byteChars.charCodeAt(i);
        var blob = new Blob([new Uint8Array(byteNums)],
          { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = d.dosya_ad || "sonuc.xlsx";
        a.className = "conn-btn inline";
        a.textContent = "📥 Excel olarak indir";
        indirEl.innerHTML = ""; indirEl.appendChild(a);
      }
      if (d.type === "table") {
        durumEl.textContent = d.n + " satır";
        renderTable(sonucEl, d.columns || [], d.rows || []);
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
