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
    const width = Math.max(canvas.clientWidth, 260);
    const height = Math.max(canvas.clientHeight, 145);
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.scale(ratio, ratio);
    context.clearRect(0, 0, width, height);
    context.font = "10px Arial";
    context.fillStyle = "#8f98a6";
    if (!rows.length) {
      context.fillText("Insufficient History", 12, height / 2);
      return;
    }
    const padding = {top: 14, right: 12, bottom: 28, left: 30};
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    context.strokeStyle = "#343941";
    context.lineWidth = 1;
    [0, 25, 50, 75, 100].forEach(function (value) {
      const y = padding.top + plotHeight - (value / 100) * plotHeight;
      context.beginPath();
      context.moveTo(padding.left, y);
      context.lineTo(width - padding.right, y);
      context.stroke();
    });
    const points = rows.map(function (row, index) {
      const x = padding.left + (
        rows.length === 1
          ? plotWidth / 2
          : (index / (rows.length - 1)) * plotWidth
      );
      const value = Math.max(0, Math.min(100, Number(row.value || 0)));
      return {
        x: x,
        y: padding.top + plotHeight - (value / 100) * plotHeight,
        label: row.label
      };
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
      context.fillStyle = "#a8b0bc";
      context.textAlign = "center";
      context.fillText(point.label, point.x, height - 8);
    });
  }

  function hydrate(root) {
    root.querySelectorAll("[data-kpi-intelligence-chart]").forEach(drawChart);
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function requestUrl(element, page) {
    const parameters = new URLSearchParams(window.location.search);
    if (page) parameters.set("page", page);
    else parameters.delete("page");
    const query = parameters.toString();
    return element.dataset.widgetUrl + (query ? "?" + query : "");
  }

  async function loadWidget(element, page) {
    if (element.dataset.loading === "1") return;
    element.dataset.loading = "1";
    try {
      const response = await fetch(requestUrl(element, page), {
        credentials: "same-origin",
        headers: {"X-Requested-With": "XMLHttpRequest"}
      });
      if (!response.ok) throw new Error("Intelligence request failed");
      element.innerHTML = await response.text();
      element.dataset.loaded = "1";
      hydrate(element);
    } catch (_error) {
      element.innerHTML = (
        '<div class="kpi8-widget-error" role="alert">' +
        "This intelligence section could not be loaded.</div>"
      );
    } finally {
      element.dataset.loading = "0";
    }
  }

  function prepareReports() {
    const query = window.location.search;
    document.querySelectorAll("[data-kpi-report-link]").forEach(function (link) {
      if (query) link.href += query;
    });
  }

  function start() {
    const widgets = document.querySelectorAll("[data-kpi-intelligence-widget]");
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
      widgets.forEach(function (widget) { loadWidget(widget); });
    }
    document.addEventListener("click", function (event) {
      const button = event.target.closest("[data-kpi8-page]");
      if (!button || button.disabled) return;
      const widget = button.closest("[data-kpi-intelligence-widget]");
      if (widget) loadWidget(widget, button.dataset.kpi8Page);
    });
    prepareReports();
    hydrate(document);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.addEventListener("resize", function () {
    document.querySelectorAll("[data-kpi-intelligence-chart]").forEach(drawChart);
  });
}());
