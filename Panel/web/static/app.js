// UYAP Çalışma Paneli — web kabuğu
// Sol menü ve Uygulama Mağazası, sunucudaki katalog + sahiplik (masaüstüyle AYNI
// magaza_core / sahip_olunan.json) üzerinden DİNAMİK kurulur. Bir özellik web'den
// ya da masaüstünden etkinleştirilse de ikisi de aynı durumu görür.
(function () {
  "use strict";

  // Çekirdek yapraklar — HER ZAMAN menüde (en altta). Masaüstündeki CEKIRDEK_NAV eşi.
  var CORE = [
    { key: "baglanti", label: "UYAP Bağlantısı", accent: "blue" },
    { key: "magaza", label: "Uygulama Mağazası", accent: "sage" },
    { key: "senkron_kapsami", label: "Senkron Kapsamı", accent: "clay" },
    { key: "ayarlar", label: "Ayarlar", accent: "ink_faint" }
  ];
  // Menü gruplarının (katlanır başlık) rengi — katalogdaki menu_yolu adlarına göre.
  var GROUP_ACCENT = {
    "Dosyalarım": "sage", "İcra": "clay", "Toplu Takip Aç": "sage",
    "Araçlar": "gold", "Raporlar": "blue", "Üretilen Modüller": "gold"
  };

  var navEl = document.getElementById("nav");
  var contentEl = document.querySelector(".content");
  var statusEl = document.getElementById("status");

  var catalog = [];          // [{kimlik, ad, aciklama, fiyat, menu_yolu, yapraklar, uretilmis}]
  var owned = [];            // [kimlik, ...]
  var labels = {};           // key -> label (çekirdek + TÜM katalog yaprakları)
  var current = "pano";

  function accentVar(name) {
    // "sage" -> var(--sage); "ink_faint" -> var(--ink-faint)
    return "var(--" + String(name || "sage").replace(/_/g, "-") + ")";
  }

  // ── Etiket sözlüğü: çekirdek + katalogdaki tüm yapraklar (başlık/durum için) ──
  function rebuildLabels() {
    labels = {};
    labels.pano = "Pano";
    CORE.forEach(function (c) { labels[c.key] = c.label; });
    catalog.forEach(function (app) {
      (app.yapraklar || []).forEach(function (y) { labels[y.key] = y.label; });
    });
  }

  // ── Sahip olunan app'lerden gezinme ağacı kur (masaüstü _aktif_nav eşi) ──
  // Düğüm: yaprak {key,label,accent} ya da grup {label,accent,children:[...]}.
  function buildTree() {
    var tree = [];
    var ownedSet = {};
    owned.forEach(function (k) { ownedSet[k] = true; });

    function place(yol, yaprak) {
      var nodes = tree;
      yol.forEach(function (ad) {
        var hedef = null;
        for (var i = 0; i < nodes.length; i++) {
          if (nodes[i].label === ad && nodes[i].children) { hedef = nodes[i]; break; }
        }
        if (!hedef) {
          hedef = { label: ad, accent: GROUP_ACCENT[ad] || "gold", children: [] };
          nodes.push(hedef);
        }
        nodes = hedef.children;
      });
      if (!nodes.some(function (n) { return n.key === yaprak.key; })) {
        nodes.push({ key: yaprak.key, label: yaprak.label, accent: yaprak.accent });
      }
    }

    // Pano her zaman en üstte (web ana ekranı)
    tree.push({ key: "pano", label: "Pano", accent: "sage" });

    catalog.forEach(function (app) {
      if (!ownedSet[app.kimlik]) return;
      (app.yapraklar || []).forEach(function (y) {
        place(app.menu_yolu || [], y);
      });
    });

    // Çekirdek üçlü — her zaman en altta
    CORE.forEach(function (c) { tree.push({ key: c.key, label: c.label, accent: c.accent }); });
    return tree;
  }

  // ── Ağacı DOM'a çiz (orijinal statik menüyle aynı sınıf yapısı) ──
  function renderNav() {
    var tree = buildTree();
    navEl.innerHTML = "";
    tree.forEach(function (node) { navEl.appendChild(nodeEl(node)); });
    highlightActive();
  }

  function nodeEl(node) {
    if (node.children) {
      var frag = document.createDocumentFragment();
      var btn = document.createElement("button");
      btn.className = "nav-group collapsed";
      btn.setAttribute("data-group", "");
      btn.style.setProperty("--accent", accentVar(node.accent));
      btn.innerHTML = '<i class="dot"></i><span></span><i class="chev"></i>';
      btn.querySelector("span").textContent = node.label;
      var kids = document.createElement("div");
      kids.className = "nav-children collapsed";
      node.children.forEach(function (c) { kids.appendChild(nodeEl(c)); });
      frag.appendChild(btn);
      frag.appendChild(kids);
      return frag;
    }
    var leaf = document.createElement("button");
    leaf.className = "nav-item leaf";
    leaf.setAttribute("data-target", node.key);
    leaf.style.setProperty("--accent", accentVar(node.accent));
    leaf.innerHTML = '<i class="dot"></i><span></span>';
    leaf.querySelector("span").textContent = node.label;
    return leaf;
  }

  // ── Her yaprak için bir içerik paneli olduğundan emin ol ──
  // Panel yoksa kur; varsa ama BOŞSA (gerçek içeriği gelmemiş yer tutucu) sakin bir
  // boş-durumla doldur. Gerçek içeriği olan paneller (baglanti, sgk, udf, magaza) atlanır.
  function ensurePanels() {
    Object.keys(labels).forEach(function (key) {
      if (key === "pano") return;
      var sec = contentEl.querySelector('.panel[data-panel="' + cssEsc(key) + '"]');
      if (sec) {
        if (sec.children.length === 0) fillEmpty(sec, labels[key]);
        return;
      }
      sec = document.createElement("section");
      sec.className = "panel";
      sec.setAttribute("data-panel", key);
      contentEl.appendChild(sec);
      fillEmpty(sec, labels[key]);
    });
  }

  function fillEmpty(sec, title) {
    sec.innerHTML =
      '<div class="panel-head"><h1 class="h1"></h1></div>' +
      '<div class="empty"><div class="glyph"><i></i></div>' +
      '<b>Bu modül buraya eklenecek</b>' +
      '<p>Kabuk hazır. Modülün kendi orijinal kodu, mantığı bozulmadan ' +
      'bu alana yerleştirilecek.</p></div>';
    sec.querySelector(".h1").textContent = title;
  }

  function cssEsc(s) {
    return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
  }

  // ── Uygulama Mağazası kartları ──
  function renderStore() {
    var grid = document.getElementById("store-grid");
    if (!grid) return;
    grid.innerHTML = "";
    var ownedSet = {};
    owned.forEach(function (k) { ownedSet[k] = true; });
    if (!catalog.length) {
      var p = document.createElement("p");
      p.className = "store-empty";
      p.textContent = "Katalog yüklenemedi.";
      grid.appendChild(p);
      return;
    }
    catalog.forEach(function (app) {
      grid.appendChild(storeCard(app, !!ownedSet[app.kimlik]));
    });
  }

  function storeCard(app, sahip) {
    var accent = (app.yapraklar && app.yapraklar[0] && app.yapraklar[0].accent) || "sage";
    var card = document.createElement("div");
    card.className = "store-card";
    card.style.setProperty("--accent", accentVar(accent));

    var stripe = document.createElement("div");
    stripe.className = "stripe";
    card.appendChild(stripe);

    var body = document.createElement("div");
    body.className = "body";

    var top = document.createElement("div");
    top.className = "top";
    var h3 = document.createElement("h3");
    h3.textContent = app.ad || app.kimlik;
    var badge = document.createElement("span");
    badge.className = "badge " + (sahip ? "on" : "off");
    badge.textContent = sahip ? "● Sahip" : "○ Sahip değil";
    top.appendChild(h3);
    top.appendChild(badge);

    var desc = document.createElement("p");
    desc.className = "desc";
    desc.textContent = app.aciklama || "";

    var foot = document.createElement("div");
    foot.className = "foot";
    var price = document.createElement("span");
    price.className = "price";
    price.textContent = app.fiyat || "";
    var btn = document.createElement("button");
    btn.className = "conn-btn inline" + (sahip ? " ghost" : "");
    btn.textContent = sahip ? "Kaldır" : "Etkinleştir";
    btn.addEventListener("click", function () { toggle(app, sahip); });
    foot.appendChild(price);
    foot.appendChild(btn);

    body.appendChild(top);
    body.appendChild(desc);
    body.appendChild(foot);
    card.appendChild(body);
    return card;
  }

  // ── Etkinleştir / Kaldır → sunucuya yaz, menü + kartları CANLI yenile ──
  function toggle(app, sahip) {
    fetch("api/store/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kimlik: app.kimlik, enable: !sahip })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.ok) {
        owned = d.owned || owned;
        renderNav();
        renderStore();
        // Kaldırılan yaprak görüntüleniyorsa mağazaya dön (panel kalsa da menüde yok).
        if (sahip && !ownedKeyVisible(current)) select("magaza");
        statusEl.textContent = (app.ad || app.kimlik) +
          (sahip ? " kaldırıldı" : " etkinleştirildi");
      } else {
        statusEl.textContent = (d && d.msg) || "İşlem başarısız.";
      }
    }).catch(function () { statusEl.textContent = "Sunucuya ulaşılamadı."; });
  }

  // current yaprağı, sahip olunan app'lerin yapraklarından biri mi (ya da çekirdek)?
  function ownedKeyVisible(key) {
    if (key === "pano") return true;
    if (CORE.some(function (c) { return c.key === key; })) return true;
    var ownedSet = {};
    owned.forEach(function (k) { ownedSet[k] = true; });
    return catalog.some(function (app) {
      return ownedSet[app.kimlik] &&
        (app.yapraklar || []).some(function (y) { return y.key === key; });
    });
  }

  // ── Gezinme ──
  function select(key) {
    current = key;
    contentEl.querySelectorAll(".panel").forEach(function (p) {
      p.classList.toggle("active", p.getAttribute("data-panel") === key);
    });
    navEl.querySelectorAll(".nav-item").forEach(function (it) {
      it.classList.toggle("active", it.getAttribute("data-target") === key);
    });
    statusEl.textContent = (labels[key] || "Pano") + " görüntüleniyor";
    contentEl.scrollTop = 0;
    // Üretilmiş modül çalıştırıcısı (uretilmis.js) bu olayla ilgili paneli kurar.
    try { document.dispatchEvent(new CustomEvent("uyap:select", { detail: { key: key } })); }
    catch (e) {}
  }

  function highlightActive() {
    navEl.querySelectorAll(".nav-item").forEach(function (it) {
      it.classList.toggle("active", it.getAttribute("data-target") === current);
    });
  }

  function toggleGroup(g) {
    var kids = g.nextElementSibling;
    if (!kids || !kids.classList.contains("nav-children")) return;
    g.classList.toggle("collapsed");
    kids.classList.toggle("collapsed");
  }

  document.addEventListener("click", function (e) {
    var g = e.target.closest("[data-group]");
    if (g) { toggleGroup(g); return; }
    var t = e.target.closest("[data-target]");
    if (t) select(t.getAttribute("data-target"));
  });

  // ── Katalog yükle → her şeyi kur ──
  function loadCatalog() {
    return fetch("api/store/catalog").then(function (r) { return r.json(); })
      .then(function (d) {
        catalog = (d && d.catalog) || [];
        owned = (d && d.owned) || [];
        // Üretilmiş modül yapraklarını uretilmis.js için global haritaya yaz.
        window.UYAP_URETILMIS = {};
        catalog.forEach(function (app) {
          if (app.uretilmis) (app.yapraklar || []).forEach(function (y) {
            window.UYAP_URETILMIS[y.key] = y.label;
          });
        });
        rebuildLabels();
        ensurePanels();
        renderNav();
        renderStore();
      });
  }

  // Logger gibi modüller yeni bir "üretilmiş" app yazınca menüyü canlı tazeler.
  window.UYAP_RELOAD_CATALOG = function () { loadCatalog().catch(function () {}); };

  // ── Açılış ──
  fetch("api/me").then(function (r) { return r.json(); }).then(function (d) {
    if (!d || !d.user) { window.location.href = "login"; return; }
    document.getElementById("top-user").textContent = d.user;
    loadCatalog().then(function () { select("pano"); })
      .catch(function () { select("pano"); });
  }).catch(function () {});

  var lo = document.getElementById("logout");
  if (lo) lo.addEventListener("click", function () {
    fetch("api/logout", { method: "POST" }).then(function () {
      window.location.href = "login";
    });
  });
})();
