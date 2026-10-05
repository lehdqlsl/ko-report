document.addEventListener("click", function (e) {
  var img = e.target.closest("figure img"); var box = document.querySelector(".lightbox");
  if (box) { box.remove(); return; }
  if (!img) return;
  var d = document.createElement("div"); d.className = "lightbox"; d.setAttribute("role", "dialog");
  var big = document.createElement("img"); big.src = img.src; big.alt = img.alt; d.appendChild(big); document.body.appendChild(d);
});
document.addEventListener("keydown", function (e) { if (e.key === "Escape") { var b = document.querySelector(".lightbox"); if (b) b.remove(); } });

/* 인쇄할 때는 접힌 부록을 펼치고, 끝나면 되돌린다 */
(function () {
  var closed = [];
  window.addEventListener("beforeprint", function () {
    closed = [].slice.call(document.querySelectorAll("details:not([open])"));
    closed.forEach(function (d) { d.open = true; });
  });
  window.addEventListener("afterprint", function () { closed.forEach(function (d) { d.open = false; }); closed = []; });
})();

/* web 레이아웃: 왼쪽 목차에서 지금 읽는 절을 표시하고, 목차로 이동한 부록은 펼친다 */
(function () {
  var side = document.querySelector("nav.side"); if (!side) return;
  var links = [].slice.call(side.querySelectorAll("a"));
  var secs = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
  var ticking = false;
  function mark() {
    ticking = false;
    var cur = 0, y = window.innerHeight * 0.3;
    secs.forEach(function (s, i) { if (s && s.getBoundingClientRect().top <= y) cur = i; });
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) cur = secs.length - 1;
    links.forEach(function (a, i) { a.classList.toggle("on", i === cur); if (i === cur) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
  }
  window.addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(mark); } }, { passive: true });
  window.addEventListener("resize", mark);
  links.forEach(function (a, i) { a.addEventListener("click", function () { var d = secs[i] && secs[i].querySelector("details"); if (d) d.open = true; }); });
  mark();
})();
