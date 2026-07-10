// Web giriş formu → backend /api/login (orijinal uyap_app doğrulamasını çağırır)
(function () {
  "use strict";
  var form = document.getElementById("login-form");
  var btn = document.getElementById("login-btn");
  var status = document.getElementById("login-status");

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "login-status" + (kind ? " " + kind : "");
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var office = document.getElementById("o").value.trim();
    var username = document.getElementById("u").value.trim();
    var password = document.getElementById("p").value.trim();
    var remember = document.getElementById("r").checked;
    if (!username || !password) {
      setStatus("Lütfen kullanıcı adı ve parolayı girin.", "err");
      return;
    }
    // Ofis kodu ayrı alandır; yalnızca kullanıcı adı zaten tam "ad@ofis"
    // biçimindeyse boş bırakılabilir (eski alışkanlıkla girenler için).
    if (!office && username.indexOf("@") === -1) {
      setStatus("Ofis kodunuzu girin (büronuzu tanımlayan kod, ör. kalkan).", "err");
      return;
    }
    btn.disabled = true;
    btn.textContent = "Bağlanılıyor…";
    setStatus("Sunucuya bağlanılıyor, bilgiler doğrulanıyor…", "info");

    fetch("api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ office_code: office, username: username,
                             password: password, remember: remember })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          setStatus("Giriş başarılı, yönlendiriliyorsunuz…", "ok");
          window.location.href = "./";
        } else {
          btn.disabled = false;
          btn.textContent = "Giriş Yap";
          setStatus("Giriş başarısız: " + (data.msg || "bilinmeyen hata"), "err");
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = "Giriş Yap";
        setStatus("Sunucuya ulaşılamadı.", "err");
      });
  });
})();
