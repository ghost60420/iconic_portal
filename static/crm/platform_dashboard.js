(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const root = document.querySelector("[data-dashboard-root]");
    if (!root) return;
    const orderNode = document.getElementById("dashboard-widget-order");
    const hiddenNode = document.getElementById("dashboard-hidden-widgets");
    const savedOrder = orderNode ? JSON.parse(orderNode.textContent || "[]") : [];
    const hidden = new Set(hiddenNode ? JSON.parse(hiddenNode.textContent || "[]") : []);
    const widget = function (key) { return root.querySelector('[data-dashboard-widget="' + key + '"]'); };

    savedOrder.slice().reverse().forEach(function (key) {
      const item = widget(key);
      const anchor = root.querySelector(".dashboard-topbar");
      if (item && anchor) anchor.insertAdjacentElement("afterend", item);
    });
    root.querySelectorAll("[data-dashboard-widget]").forEach(function (item) {
      item.hidden = hidden.has(item.dataset.dashboardWidget);
    });

    let dragged = null;
    root.addEventListener("dragstart", function (event) {
      dragged = event.target.closest("[data-dashboard-widget]");
      if (dragged) dragged.classList.add("is-dragging");
    });
    root.addEventListener("dragend", function () {
      if (dragged) dragged.classList.remove("is-dragging");
      dragged = null;
    });
    root.addEventListener("dragover", function (event) {
      const target = event.target.closest("[data-dashboard-widget]");
      if (!dragged || !target || target === dragged) return;
      event.preventDefault();
      const box = target.getBoundingClientRect();
      target.insertAdjacentElement(event.clientY < box.top + box.height / 2 ? "beforebegin" : "afterend", dragged);
    });

    document.querySelectorAll("[data-widget-visibility]").forEach(function (input) {
      input.addEventListener("change", function () {
        const item = widget(input.value);
        if (item) item.hidden = !input.checked;
      });
    });
    const save = document.querySelector("[data-save-dashboard-layout]");
    if (save) save.addEventListener("click", async function () {
      const csrfMatch = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
      const payload = {
        hidden: Array.from(document.querySelectorAll("[data-widget-visibility]:not(:checked)")).map(function (input) { return input.value; }),
        order: Array.from(root.querySelectorAll("[data-dashboard-widget]")).map(function (item) { return item.dataset.dashboardWidget; })
      };
      const response = await fetch(save.dataset.url, {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfMatch ? decodeURIComponent(csrfMatch[1]) : ""},
        body: JSON.stringify(payload)
      });
      save.textContent = response.ok ? "Layout saved" : "Save failed";
    });
  });
})();
