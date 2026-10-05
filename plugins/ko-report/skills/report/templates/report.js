document.addEventListener("click", function (e) {
  var img = e.target.closest("figure img"); var box = document.querySelector(".lightbox");
  if (box) { box.remove(); return; }
  if (!img) return;
  var d = document.createElement("div"); d.className = "lightbox"; d.setAttribute("role", "dialog");
  var big = document.createElement("img"); big.src = img.src; big.alt = img.alt; d.appendChild(big); document.body.appendChild(d);
});
document.addEventListener("keydown", function (e) { if (e.key === "Escape") { var b = document.querySelector(".lightbox"); if (b) b.remove(); } });
