(function () {
  "use strict";

  function drawChart(canvas) {
    const source = document.getElementById(canvas.dataset.seriesId);
    if (!source) return;
    let rows = [];
    try {
      rows = JSON.parse(source.textContent || "[]");
    } catch (_error) {
      rows = [];
    }
    const context = canvas.getContext("2d");
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth, 240);
    const height = Math.max(canvas.clientHeight, 130);
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.scale(ratio, ratio);
    context.clearRect(0, 0, width, height);
    context.font = "10px Arial";
    context.fillStyle = "#7f8997";
    if (!rows.length) {
      context.fillText("No approved trend data", 10, height / 2);
      return;
    }
    const padding = {top: 12, right: 12, bottom: 25, left: 28};
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    context.strokeStyle = "#30343b";
    context.lineWidth = 1;
    [0, 25, 50, 75, 100].forEach(function (value) {
      const y = padding.top + plotHeight - (value / 100) * plotHeight;
      context.beginPath();
      context.moveTo(padding.left, y);
      context.lineTo(width - padding.right, y);
      context.stroke();
    });
    const points = rows.map(function (row, index) {
      const x = padding.left + (rows.length === 1 ? plotWidth / 2 : (index / (rows.length - 1)) * plotWidth);
      const value = Math.max(0, Math.min(100, Number(row.value || 0)));
      const y = padding.top + plotHeight - (value / 100) * plotHeight;
      return {x: x, y: y, label: row.label, value: value};
    });
    context.strokeStyle = "#d6b45a";
    context.lineWidth = 2;
    context.beginPath();
    points.forEach(function (point, index) {
      if (index === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.stroke();
    points.forEach(function (point) {
      context.fillStyle = "#d6b45a";
      context.beginPath();
      context.arc(point.x, point.y, 3, 0, Math.PI * 2);
      context.fill();
      context.fillStyle = "#9da5b3";
      context.textAlign = "center";
      context.fillText(point.label, point.x, height - 7);
    });
  }

  function hydrate(root) {
    root.querySelectorAll("[data-kpi-chart]").forEach(drawChart);
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  async function loadWidget(element) {
    if (element.dataset.loaded === "1") return;
    element.dataset.loaded = "1";
    const query = window.location.search || "";
    try {
      const response = await fetch(element.dataset.widgetUrl + query, {
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
      if (!response.ok) throw new Error("Widget request failed");
      element.innerHTML = await response.text();
      hydrate(element);
    } catch (_error) {
      element.dataset.loaded = "0";
      element.innerHTML = '<div class="kpi7-widget-error" role="alert">This widget could not be loaded.</div>';
    }
  }

  function start() {
    const widgets = document.querySelectorAll("[data-kpi-widget]");
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          observer.unobserve(entry.target);
          loadWidget(entry.target);
        });
      }, {rootMargin: "180px"});
      widgets.forEach(function (widget) { observer.observe(widget); });
    } else {
      widgets.forEach(loadWidget);
    }
    hydrate(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
  window.addEventListener("resize", function () {
    document.querySelectorAll("[data-kpi-chart]").forEach(drawChart);
  });
}());
