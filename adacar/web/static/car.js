// A car's whole text: a click anywhere on its card opens it in a dialog, except on the card's
// own links, buttons and pictures, or after selecting text in it.
(() => {
  const dialog = document.getElementById("car-detail");
  if (!dialog) return;
  const body = document.getElementById("car-detail-body");
  let opening = false;
  let trigger = null;

  function carOf(event) {
    const link = event.target.closest(".car-open");
    if (link) return link.closest("#other") ? null : link.dataset.car; // beside the search: there
    const card = event.target.closest("#car-list > .car");
    if (!card || event.target.closest("a, button, input, select, textarea, label, summary")) return null;
    if (String(window.getSelection())) return null;
    return card.dataset.id;
  }

  document.addEventListener("click", (event) => {
    if (event.ctrlKey || event.shiftKey) return; // Ctrl/Shift-click on the title opens the page
    const car = carOf(event);
    if (!car) return;
    event.preventDefault();
    opening = true;
    const source = event.target.closest(".car") || undefined;
    trigger = event.target.closest(".car-open") || source;
    htmx.ajax("GET", `/cars/${car}`, { source, target: body, swap: "innerHTML" });
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target !== body || !opening) return;
    opening = false;
    if (!dialog.open) window.adacarShowModal(dialog, trigger);
    body.scrollTop = 0;
  });
  window.adacarOnClosed(dialog, () => body.replaceChildren());
})();
