(function () {
  var out = document.getElementById("out");
  var run = document.getElementById("run");
  var tamper = document.getElementById("tamper");
  var blocks = document.querySelectorAll(".block[data-v]");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var tampered = false;
  var timer = null;
  var IDX = "0x772bef4afb843eec39dc880b3c0def75c7c363d3dc3942d8ac546931bf5d48bd";
  var V1 = "fdb11b68cb3386a4  blob k7xbplKaLg_6X9VHmiHx6IzMmY1HQ8q6IfxfytE3V-M";
  var V2 = "577cd51205e7f5a1  blob IWdN2tdMe_iMto_WsNzvfu-s2ca3DIdgA38abPxpCXg";
  var V3 = "51f27807f88e3d0d  blob 46DO0SCDR0uAz0VWBzYihPr9MgJmUzeNdfEATmtbsX0";

  function lines() {
    var l = [
      '<span class="p">$</span> hermes mnemos-sui history --verify',
      "MemoryIndex " + IDX + "  pubkey a12af0e49b9c18ab",
      'v1    <b class="ok">OK</b>     ' + V1
    ];
    if (tampered) {
      l.push('v2    <b class="bad">BROKEN</b> ' + V2 + "  (blob does not match the committed content hash)");
      l.push('v3    <b class="ok">OK</b>     ' + V3);
      l.push('<b class="bad">BROKEN: 1 of 3 versions failed</b>');
      l.push('<span class="p">$</span> echo $?');
      l.push("1");
    } else {
      l.push('v2    <b class="ok">OK</b>     ' + V2);
      l.push('v3    <b class="ok">OK</b>     ' + V3);
      l.push('<b class="ok">OK: 3 versions verified</b>');
    }
    return l;
  }

  function mark(i, state) {
    var b = blocks[i];
    b.classList.remove("ok", "bad", "busy");
    if (state) b.classList.add(state);
    b.querySelector(".st").textContent = state === "ok" ? "OK" : state === "bad" ? "BROKEN" : "";
  }

  function play() {
    clearTimeout(timer);
    var l = lines();
    for (var i = 0; i < blocks.length; i++) mark(i, "");
    if (reduce) {
      out.innerHTML = l.join("\n");
      mark(0, "ok");
      mark(1, tampered ? "bad" : "ok");
      mark(2, "ok");
      return;
    }
    out.innerHTML = "";
    var n = 0;
    (function step() {
      out.innerHTML = l.slice(0, n + 1).join("\n");
      if (n === 1) mark(0, "busy");
      if (n === 2) { mark(0, "ok"); mark(1, "busy"); }
      if (n === 3) { mark(1, tampered ? "bad" : "ok"); mark(2, "busy"); }
      if (n === 4) mark(2, "ok");
      n++;
      if (n < l.length) timer = setTimeout(step, n === 1 ? 500 : 650);
    })();
  }

  run.addEventListener("click", play);
  tamper.addEventListener("click", function () {
    tampered = !tampered;
    tamper.setAttribute("aria-pressed", String(tampered));
    tamper.textContent = tampered ? "Restore v2" : "Tamper with v2";
    document.querySelector('.block[data-v="2"]').classList.toggle("forged", tampered);
    play();
  });

  if ("IntersectionObserver" in window && !reduce) {
    var seen = false;
    new IntersectionObserver(function (e, o) {
      if (e[0].isIntersecting && !seen) { seen = true; o.disconnect(); play(); }
    }, { threshold: 0.4 }).observe(document.getElementById("chain"));
  } else {
    mark(0, "ok"); mark(1, "ok"); mark(2, "ok");
  }
})();
