(function () {
  var out = document.getElementById("out");
  var run = document.getElementById("run");
  var tamper = document.getElementById("tamper");
  var blocks = document.querySelectorAll(".block[data-v]");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var tampered = false;
  var timer = null;
  var IDX = "0x40028382ba48edaff90240696fb0441854046d9bee10195d296d2046a72668e3";
  var V1 = "03053bacf1305209  blob ymHOBfSei9ECmnM_CoA-4Q2oLtTX3tYgVgvN4Hngxiw";
  var V2 = "799a8745d1d77c62  blob 31Wm7ToeEUOP0GWGFn3_UjinT02gtLuJ4Rm2mnF0HVg";

  function lines() {
    var l = [
      '<span class="p">$</span> hermes mnemos history --verify',
      "MemoryIndex " + IDX + "  pubkey c5f2488e608132c3",
      'v1    <b class="ok">OK</b>     ' + V1
    ];
    if (tampered) {
      l.push('v2    <b class="bad">BROKEN</b> ' + V2 + "  (blob does not match the committed content hash)");
      l.push('<b class="bad">BROKEN: 1 of 2 versions failed</b>');
      l.push('<span class="p">$</span> echo $?');
      l.push("1");
    } else {
      l.push('v2    <b class="ok">OK</b>     ' + V2);
      l.push('<b class="ok">OK: 2 versions verified</b>');
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
      return;
    }
    out.innerHTML = "";
    var n = 0;
    (function step() {
      out.innerHTML = l.slice(0, n + 1).join("\n");
      if (n === 1) mark(0, "busy");
      if (n === 2) { mark(0, "ok"); mark(1, "busy"); }
      if (n === 3) mark(1, tampered ? "bad" : "ok");
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
    mark(0, "ok"); mark(1, "ok");
  }
})();
