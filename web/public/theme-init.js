// Applies the persisted theme before first paint (external file: the CSP forbids inline scripts).
(function () {
  var theme = "system";
  try {
    theme = localStorage.getItem("om.theme") || "system";
  } catch (e) {
    /* storage unavailable */
  }
  var dark =
    theme === "dark" ||
    (theme !== "light" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#121214" : "#ffffff");
})();
