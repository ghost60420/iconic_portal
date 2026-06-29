(function () {
  "use strict";

  function enhance(textarea) {
    const endpoint = textarea.dataset.mentionUrl;
    if (!endpoint) return;
    const host = textarea.parentElement;
    host.classList.add("mention-wrap");
    const menu = document.createElement("div");
    menu.className = "mention-suggestions";
    menu.hidden = true;
    menu.setAttribute("role", "listbox");
    host.appendChild(menu);
    let rows = [];
    let activeIndex = -1;
    let requestNumber = 0;

    function close() {
      menu.hidden = true;
      menu.replaceChildren();
      rows = [];
      activeIndex = -1;
    }

    function tokenAtCursor() {
      const before = textarea.value.slice(0, textarea.selectionStart);
      const match = before.match(/@([A-Za-z0-9._-]{1,40})$/);
      return match ? { query: match[1], start: before.length - match[0].length } : null;
    }

    function choose(row) {
      const token = tokenAtCursor();
      if (!token) return close();
      const cursor = textarea.selectionStart;
      textarea.value = textarea.value.slice(0, token.start) + "@" + row.handle + " " + textarea.value.slice(cursor);
      const nextCursor = token.start + row.handle.length + 2;
      textarea.setSelectionRange(nextCursor, nextCursor);
      textarea.focus();
      close();
    }

    function draw() {
      menu.replaceChildren();
      rows.forEach(function (row, index) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mention-option" + (index === activeIndex ? " is-active" : "");
        button.setAttribute("role", "option");
        let avatar;
        if (row.photo_url) {
          avatar = document.createElement("img");
          avatar.src = row.photo_url;
          avatar.alt = "";
        } else {
          avatar = document.createElement("span");
          avatar.textContent = row.initials || "?";
        }
        avatar.className = "mention-avatar";
        const details = document.createElement("span");
        const name = document.createElement("strong");
        name.textContent = row.display_name;
        const meta = document.createElement("small");
        meta.textContent = [row.position, row.department].filter(Boolean).join(", ");
        details.append(name, meta);
        const handle = document.createElement("span");
        handle.textContent = "@" + row.handle;
        button.append(avatar, details, handle);
        button.addEventListener("mousedown", function (event) {
          event.preventDefault();
          choose(row);
        });
        menu.appendChild(button);
      });
      menu.hidden = rows.length === 0;
    }

    textarea.addEventListener("input", function () {
      const token = tokenAtCursor();
      if (!token) return close();
      const currentRequest = ++requestNumber;
      fetch(endpoint + "?q=" + encodeURIComponent(token.query), { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (response) { return response.ok ? response.json() : { results: [] }; })
        .then(function (payload) {
          if (currentRequest !== requestNumber) return;
          rows = payload.results || [];
          activeIndex = rows.length ? 0 : -1;
          draw();
        })
        .catch(close);
    });

    textarea.addEventListener("keydown", function (event) {
      if (menu.hidden || !rows.length) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        activeIndex = (activeIndex + direction + rows.length) % rows.length;
        draw();
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        choose(rows[activeIndex]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });

    textarea.addEventListener("blur", function () { window.setTimeout(close, 120); });
  }

  document.querySelectorAll("textarea[data-mention-input]").forEach(enhance);
})();
