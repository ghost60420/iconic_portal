(function () {
  "use strict";

  function appendHighlightedText(parent, text, query) {
    const source = String(text || "");
    if (!query) {
      parent.appendChild(document.createTextNode(source));
      return;
    }
    const index = source.toLowerCase().indexOf(query.toLowerCase());
    if (index < 0) {
      parent.appendChild(document.createTextNode(source));
      return;
    }
    parent.appendChild(document.createTextNode(source.slice(0, index)));
    const mark = document.createElement("mark");
    mark.textContent = source.slice(index, index + query.length);
    parent.appendChild(mark);
    parent.appendChild(document.createTextNode(source.slice(index + query.length)));
  }

  function initializeSearch(form) {
    const input = form.querySelector(".js-global-search-input");
    const panel = form.querySelector(".js-global-search-suggestions");
    const endpoint = form.dataset.suggestionsUrl;
    if (!input || !panel || !endpoint) return;

    let timer = null;
    let controller = null;
    let activeIndex = -1;
    let links = [];

    function closePanel() {
      panel.hidden = true;
      input.setAttribute("aria-expanded", "false");
      activeIndex = -1;
      links = [];
    }

    function setActive(index) {
      if (!links.length) return;
      activeIndex = (index + links.length) % links.length;
      links.forEach(function (link, linkIndex) {
        link.classList.toggle("is-active", linkIndex === activeIndex);
        link.setAttribute("aria-selected", linkIndex === activeIndex ? "true" : "false");
      });
      links[activeIndex].scrollIntoView({ block: "nearest" });
    }

    function render(data, query) {
      panel.replaceChildren();
      (data.groups || []).forEach(function (group) {
        const section = document.createElement("div");
        section.className = "crm-search-suggestion-group";
        const heading = document.createElement("span");
        heading.className = "crm-search-suggestion-heading";
        heading.textContent = group.label;
        section.appendChild(heading);
        group.rows.forEach(function (row) {
          const link = document.createElement("a");
          link.className = "crm-search-suggestion";
          link.href = row.url;
          link.setAttribute("role", "option");
          link.setAttribute("aria-selected", "false");
          const title = document.createElement("strong");
          appendHighlightedText(title, [row.number, row.name].filter(Boolean).join(" · "), query);
          const meta = document.createElement("span");
          meta.textContent = [row.status, row.amount].filter(Boolean).join(" · ");
          link.append(title, meta);
          section.appendChild(link);
        });
        panel.appendChild(section);
      });
      if (!panel.children.length) {
        const empty = document.createElement("div");
        empty.className = "crm-search-suggestion-empty";
        empty.textContent = "No permitted CRM records found.";
        panel.appendChild(empty);
      }
      links = Array.from(panel.querySelectorAll("a[role='option']"));
      activeIndex = -1;
      panel.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    async function loadSuggestions(query) {
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        const response = await fetch(endpoint + "?q=" + encodeURIComponent(query), {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Search unavailable");
        render(await response.json(), query);
      } catch (error) {
        if (error.name !== "AbortError") closePanel();
      }
    }

    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      const query = input.value.trim();
      if (query.length < 2) {
        if (controller) controller.abort();
        closePanel();
        return;
      }
      timer = window.setTimeout(function () { loadSuggestions(query); }, 160);
    });

    input.addEventListener("focus", function () {
      if (!input.value.trim()) loadSuggestions("");
    });

    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closePanel();
      } else if (event.key === "ArrowDown" && links.length) {
        event.preventDefault();
        setActive(activeIndex + 1);
      } else if (event.key === "ArrowUp" && links.length) {
        event.preventDefault();
        setActive(activeIndex - 1);
      } else if (event.key === "Enter" && activeIndex >= 0 && links[activeIndex]) {
        event.preventDefault();
        window.location.assign(links[activeIndex].href);
      }
    });

    document.addEventListener("click", function (event) {
      if (!form.contains(event.target)) closePanel();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".js-global-search").forEach(initializeSearch);
  });

  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      const input = document.querySelector(".js-global-search-input");
      if (!input) return;
      event.preventDefault();
      input.focus();
      input.select();
    }
  });
})();
