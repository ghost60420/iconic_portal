(function () {
  const root = document.querySelector("[data-dashboard-root]");
  if (!root) return;

  const densitySelect = root.querySelector("[data-density-select]");
  const compactToggle = root.querySelector("[data-compact-toggle]");
  const compactToggleLabel = root.querySelector("[data-density-toggle-label]");
  const densityKey = "iconic.dashboard.density";
  const legacyCompactKey = "iconic.dashboard.compact";
  const allowedDensities = new Set(["comfortable", "compact"]);

  function readDensity() {
    let density = localStorage.getItem(densityKey);
    if (!allowedDensities.has(density)) {
      density = localStorage.getItem(legacyCompactKey) === "1" ? "compact" : root.dataset.density || "comfortable";
    }
    return allowedDensities.has(density) ? density : "comfortable";
  }

  function applyPrefs() {
    const density = readDensity();
    const compact = density === "compact";
    root.dataset.density = density;
    root.dataset.crmDensity = density;
    root.classList.toggle("is-compact", compact);
    if (densitySelect) densitySelect.value = density;
    if (compactToggle) {
      compactToggle.setAttribute("aria-pressed", compact ? "true" : "false");
      compactToggle.classList.toggle("is-active", compact);
      compactToggle.setAttribute("title", compact ? "Switch to comfortable dashboard density" : "Switch to compact dashboard density");
    }
    if (compactToggleLabel) {
      compactToggleLabel.textContent = compact ? "Comfortable Mode" : "Compact Mode";
    }
  }

  if (densitySelect) {
    densitySelect.addEventListener("change", function () {
      const nextDensity = allowedDensities.has(densitySelect.value) ? densitySelect.value : "comfortable";
      localStorage.setItem(densityKey, nextDensity);
      localStorage.removeItem(legacyCompactKey);
      applyPrefs();
    });
  }

  if (compactToggle) {
    compactToggle.addEventListener("click", function () {
      const nextDensity = readDensity() === "compact" ? "comfortable" : "compact";
      localStorage.setItem(densityKey, nextDensity);
      localStorage.removeItem(legacyCompactKey);
      applyPrefs();
    });
  }

  applyPrefs();

  const dashboardMenus = Array.from(root.querySelectorAll("[data-dashboard-menu], .dashboard-toolbar-actions > .dashboard-menu"));

  function syncDashboardMenuState() {
    const hasOpenMenu = dashboardMenus.some(function (menu) {
      return menu.open;
    });
    root.classList.toggle("has-dashboard-menu-open", hasOpenMenu);
  }

  function closeDashboardMenus(exceptMenu) {
    dashboardMenus.forEach(function (menu) {
      if (menu !== exceptMenu) {
        menu.open = false;
      }
    });
  }

  dashboardMenus.forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (menu.open) {
        closeDashboardMenus(menu);
      }
      syncDashboardMenuState();
    });
  });

  document.addEventListener("click", function (event) {
    if (!dashboardMenus.length) return;
    const target = event.target;
    dashboardMenus.forEach(function (menu) {
      if (menu.open && !menu.contains(target)) {
        menu.open = false;
      }
    });
    syncDashboardMenuState();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    const openMenu = dashboardMenus.find(function (menu) {
      return menu.open;
    });
    if (!openMenu) return;
    openMenu.open = false;
    syncDashboardMenuState();
    const summary = openMenu.querySelector("summary");
    if (summary) summary.focus();
  });

  const searchForm = root.querySelector("[data-search-form]");
  if (searchForm) {
    searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      const input = searchForm.querySelector("[data-search-input]");
      const scope = searchForm.querySelector("[data-search-scope]");
      const option = scope ? scope.options[scope.selectedIndex] : null;
      const baseUrl = (option && option.dataset.url) || searchForm.getAttribute("action") || "/";
      const nextUrl = new URL(baseUrl, window.location.origin);
      const query = input ? (input.value || "").trim() : "";
      if (query) {
        nextUrl.searchParams.set("q", query);
      }
      window.location.href = nextUrl.pathname + nextUrl.search;
    });
  }

  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons();
  }

  const raw = document.getElementById("dash-data");
  if (!raw || !window.Chart) {
    root.querySelectorAll("[data-chart-shell]").forEach(function (shell) {
      shell.classList.remove("is-loading");
    });
    return;
  }

  const data = JSON.parse(raw.textContent || "{}");
  const chartRegistry = new WeakMap();

  function safeLabels(labels, fallback) {
    return labels && labels.length ? labels : fallback;
  }

  function safeValues(values, size) {
    if (values && values.length) return values;
    return Array(size || 1).fill(0);
  }

  function withAlpha(hex, alpha) {
    const value = hex.replace("#", "");
    const r = parseInt(value.substring(0, 2), 16);
    const g = parseInt(value.substring(2, 4), 16);
    const b = parseInt(value.substring(4, 6), 16);
    return "rgba(" + r + ", " + g + ", " + b + ", " + alpha + ")";
  }

  const palette = {
    sky: "#38bdf8",
    teal: "#2dd4bf",
    green: "#22c55e",
    amber: "#f59e0b",
    coral: "#fb7185",
    violet: "#a855f7",
    slate: "#94a3b8",
    white: "#f8fafc",
  };

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        intersect: false,
        mode: "index",
      },
      plugins: {
        legend: {
          position: "top",
          align: "start",
          labels: {
            color: "#dbe5f3",
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: "circle",
            font: {
              size: 11,
              weight: "700",
            },
          },
        },
        tooltip: {
          backgroundColor: "rgba(7, 13, 24, 0.96)",
          titleColor: "#f8fafc",
          bodyColor: "#dbe5f3",
          borderColor: "rgba(148, 163, 184, 0.18)",
          borderWidth: 1,
          padding: 12,
          displayColors: true,
        },
      },
      scales: {
        x: {
          ticks: {
            color: "#93a7c3",
            maxRotation: 0,
            autoSkip: true,
            font: {
              size: 10,
              weight: "600",
            },
          },
          grid: {
            color: "rgba(148, 163, 184, 0.08)",
            drawBorder: false,
          },
          border: {
            display: false,
          },
        },
        y: {
          ticks: {
            color: "#93a7c3",
            font: {
              size: 10,
              weight: "600",
            },
          },
          grid: {
            color: "rgba(148, 163, 184, 0.08)",
            drawBorder: false,
          },
          border: {
            display: false,
          },
        },
      },
    };
  }

  function doughnutOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "68%",
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            color: "#dbe5f3",
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
            pointStyle: "circle",
            font: {
              size: 11,
              weight: "700",
            },
          },
        },
        tooltip: {
          backgroundColor: "rgba(7, 13, 24, 0.96)",
          titleColor: "#f8fafc",
          bodyColor: "#dbe5f3",
          borderColor: "rgba(148, 163, 184, 0.18)",
          borderWidth: 1,
          padding: 12,
        },
      },
    };
  }

  function barPalette(size) {
    const colors = [palette.sky, palette.teal, palette.amber, palette.coral, palette.violet, palette.green];
    return Array.from({ length: size }, function (_, index) {
      return withAlpha(colors[index % colors.length], 0.82);
    });
  }

  function chartConfig(key, canvas) {
    const trendLabels = safeLabels(data.leads_labels, ["No data"]);
    const trendSize = trendLabels.length;
    const gradientCtx = canvas.getContext("2d");
    const areaGradient = gradientCtx.createLinearGradient(0, 0, 0, canvas.height || 280);
    areaGradient.addColorStop(0, withAlpha(palette.sky, 0.34));
    areaGradient.addColorStop(1, withAlpha(palette.sky, 0.02));

    if (key === "opportunity-trend") {
      return {
        type: "line",
        data: {
          labels: trendLabels,
          datasets: [
            {
              label: "Opportunities",
              data: safeValues(data.opp_daily_values, trendSize),
              borderColor: palette.sky,
              backgroundColor: areaGradient,
              fill: true,
              tension: 0.38,
              pointRadius: 0,
              borderWidth: 2.4,
            },
            {
              label: "Leads",
              data: safeValues(data.leads_values, trendSize),
              borderColor: palette.teal,
              backgroundColor: withAlpha(palette.teal, 0.1),
              fill: false,
              tension: 0.32,
              pointRadius: 0,
              borderWidth: 2,
            },
          ],
        },
        options: baseOptions(),
      };
    }

    if (key === "lead-source") {
      const labels = safeLabels(data.lead_source_labels, ["Unknown"]);
      return {
        type: "doughnut",
        data: {
          labels: labels,
          datasets: [
            {
              data: safeValues(data.lead_source_values, labels.length),
              backgroundColor: barPalette(labels.length),
              borderColor: "rgba(7, 13, 24, 0.96)",
              borderWidth: 2,
            },
          ],
        },
        options: doughnutOptions(),
      };
    }

    if (key === "production-status") {
      return {
        type: "bar",
        data: {
          labels: safeLabels(data.prod_labels, ["Planning", "In progress", "On hold", "Done"]),
          datasets: [
            {
              label: "Orders",
              data: safeValues(data.prod_counts, 4),
              backgroundColor: [withAlpha(palette.slate, 0.72), withAlpha(palette.sky, 0.78), withAlpha(palette.coral, 0.78), withAlpha(palette.green, 0.78)],
              borderRadius: 10,
              borderSkipped: false,
            },
          ],
        },
        options: Object.assign(baseOptions(), {
          indexAxis: "y",
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "shipment-status") {
      return {
        type: "bar",
        data: {
          labels: safeLabels(data.ship_labels, ["Current"]),
          datasets: [
            {
              label: "Shipped",
              data: safeValues(data.ship_shipped, 1),
              stack: "ship",
              backgroundColor: withAlpha(palette.green, 0.8),
              borderRadius: 10,
              borderSkipped: false,
            },
            {
              label: "Pending",
              data: safeValues(data.ship_pending, 1),
              stack: "ship",
              backgroundColor: withAlpha(palette.amber, 0.82),
              borderRadius: 10,
              borderSkipped: false,
            },
            {
              label: "Delayed",
              data: safeValues(data.ship_delayed, 1),
              stack: "ship",
              backgroundColor: withAlpha(palette.coral, 0.82),
              borderRadius: 10,
              borderSkipped: false,
            },
          ],
        },
        options: Object.assign(baseOptions(), {
          scales: {
            x: Object.assign({}, baseOptions().scales.x, { stacked: true }),
            y: Object.assign({}, baseOptions().scales.y, { stacked: true }),
          },
        }),
      };
    }

    if (key === "lead-fit") {
      const labels = safeLabels(data.lead_fit_labels, ["0-24", "25-49", "50-74", "75-100"]);
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Leads",
              data: safeValues(data.lead_fit_values, labels.length),
              backgroundColor: [withAlpha(palette.coral, 0.74), withAlpha(palette.amber, 0.78), withAlpha(palette.sky, 0.8), withAlpha(palette.green, 0.82)],
              borderRadius: 10,
              borderSkipped: false,
            },
          ],
        },
        options: Object.assign(baseOptions(), {
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "revenue-expense") {
      return {
        type: "line",
        data: {
          labels: trendLabels,
          datasets: [
            {
              label: "Revenue",
              data: safeValues(data.revenue_daily_values, trendSize),
              borderColor: palette.green,
              backgroundColor: withAlpha(palette.green, 0.1),
              fill: false,
              tension: 0.34,
              pointRadius: 0,
              borderWidth: 2.2,
            },
            {
              label: "Expenses",
              data: safeValues(data.expense_daily_values, trendSize),
              borderColor: palette.coral,
              backgroundColor: withAlpha(palette.coral, 0.1),
              fill: false,
              tension: 0.34,
              pointRadius: 0,
              borderWidth: 2.2,
            },
          ],
        },
        options: baseOptions(),
      };
    }

    if (key === "monthly-profit") {
      const labels = safeLabels(data.monthly_profit_labels, ["Month"]);
      const values = safeValues(data.monthly_profit_values, labels.length);
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            {
              label: "Net profit",
              data: values,
              backgroundColor: values.map(function (value) {
                return value >= 0 ? withAlpha(palette.violet, 0.82) : withAlpha(palette.coral, 0.82);
              }),
              borderRadius: 10,
              borderSkipped: false,
            },
          ],
        },
        options: Object.assign(baseOptions(), {
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "invoice-status") {
      const labels = safeLabels(data.invoice_status_labels, ["Draft", "Sent", "Partial", "Paid"]);
      return {
        type: "doughnut",
        data: {
          labels: labels,
          datasets: [
            {
              data: safeValues(data.invoice_status_values, labels.length),
              backgroundColor: [withAlpha(palette.slate, 0.78), withAlpha(palette.sky, 0.8), withAlpha(palette.amber, 0.82), withAlpha(palette.green, 0.84)],
              borderColor: "rgba(7, 13, 24, 0.96)",
              borderWidth: 2,
            },
          ],
        },
        options: doughnutOptions(),
      };
    }

    if (key === "lead-status") {
      const labels = safeLabels(data.lead_status_labels, ["New"]);
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{
            label: "Leads",
            data: safeValues(data.lead_status_values, labels.length),
            backgroundColor: withAlpha(palette.sky, 0.78),
            borderRadius: 10,
            borderSkipped: false,
          }],
        },
        options: Object.assign(baseOptions(), {
          indexAxis: "y",
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "opp-stages") {
      const labels = safeLabels(data.opp_stage_labels, ["Prospecting"]);
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{
            label: "Opportunities",
            data: safeValues(data.opp_stage_values, labels.length),
            backgroundColor: withAlpha(palette.teal, 0.8),
            borderRadius: 10,
            borderSkipped: false,
          }],
        },
        options: Object.assign(baseOptions(), {
          indexAxis: "y",
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "win-loss") {
      return {
        type: "doughnut",
        data: {
          labels: safeLabels(data.win_loss_labels, ["Won", "Lost"]),
          datasets: [{
            data: safeValues(data.win_loss_values, 2),
            backgroundColor: [withAlpha(palette.green, 0.82), withAlpha(palette.coral, 0.82)],
            borderColor: "rgba(7, 13, 24, 0.96)",
            borderWidth: 2,
          }],
        },
        options: doughnutOptions(),
      };
    }

    if (key === "market") {
      const labels = safeLabels(data.lead_market_labels, ["Unknown"]);
      return {
        type: "doughnut",
        data: {
          labels: labels,
          datasets: [{
            data: safeValues(data.lead_market_values, labels.length),
            backgroundColor: barPalette(labels.length),
            borderColor: "rgba(7, 13, 24, 0.96)",
            borderWidth: 2,
          }],
        },
        options: doughnutOptions(),
      };
    }

    if (key === "priority") {
      const labels = safeLabels(data.lead_priority_labels, ["Medium"]);
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [{
            label: "Leads",
            data: safeValues(data.lead_priority_values, labels.length),
            backgroundColor: barPalette(labels.length),
            borderRadius: 10,
            borderSkipped: false,
          }],
        },
        options: Object.assign(baseOptions(), {
          plugins: Object.assign(baseOptions().plugins, { legend: { display: false } }),
        }),
      };
    }

    if (key === "orders") {
      return {
        type: "line",
        data: {
          labels: trendLabels,
          datasets: [
            {
              label: "Created",
              data: safeValues(data.orders_created_values, trendSize),
              borderColor: palette.sky,
              backgroundColor: withAlpha(palette.sky, 0.12),
              fill: false,
              tension: 0.34,
              pointRadius: 0,
              borderWidth: 2.1,
            },
            {
              label: "Processed",
              data: safeValues(data.orders_processed_values, trendSize),
              borderColor: palette.green,
              backgroundColor: withAlpha(palette.green, 0.12),
              fill: false,
              tension: 0.34,
              pointRadius: 0,
              borderWidth: 2.1,
            },
          ],
        },
        options: baseOptions(),
      };
    }

    if (key === "cash-flow") {
      return {
        type: "line",
        data: {
          labels: trendLabels,
          datasets: [{
            label: "Net cash",
            data: safeValues(data.cash_daily_values, trendSize),
            borderColor: palette.amber,
            backgroundColor: withAlpha(palette.amber, 0.08),
            fill: false,
            tension: 0.34,
            pointRadius: 0,
            borderWidth: 2.2,
          }],
        },
        options: baseOptions(),
      };
    }

    return null;
  }

  function renderChart(canvas) {
    if (!canvas || chartRegistry.has(canvas)) return;
    const key = canvas.dataset.chart;
    const shell = canvas.closest("[data-chart-shell]");
    const config = chartConfig(key, canvas);
    if (!config) {
      if (shell) shell.classList.remove("is-loading");
      return;
    }
    const chart = new window.Chart(canvas, config);
    chartRegistry.set(canvas, chart);
    if (shell) shell.classList.remove("is-loading");
  }

  function maybeRender(canvas) {
    if (!canvas) return;
    const details = canvas.closest("details");
    if (details && !details.open) return;
    renderChart(canvas);
  }

  const canvases = Array.from(root.querySelectorAll("canvas[data-chart]"));
  const detailsBlocks = Array.from(root.querySelectorAll("details.dashboard-disclosure"));

  detailsBlocks.forEach(function (details) {
    details.addEventListener("toggle", function () {
      if (!details.open) return;
      details.querySelectorAll("canvas[data-chart]").forEach(maybeRender);
    });
  });

  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          maybeRender(entry.target);
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "140px 0px" }
    );
    canvases.forEach(function (canvas) {
      observer.observe(canvas);
    });
  } else {
    canvases.forEach(maybeRender);
  }

  const idle = window.requestIdleCallback || function (callback) { return setTimeout(callback, 250); };
  idle(function () {
    canvases.slice(0, 4).forEach(maybeRender);
  });
})();
