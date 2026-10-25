/* One editor for existing ProductionStage records; no separate stage workflow. */
(function () {
  "use strict";
  const panel = document.querySelector("[data-production-stages]");
  if (!panel) return;
  const dialog = document.getElementById("production-stage-dialog");
  const form = document.getElementById("production-stage-dialog-form");
  const notice = panel.querySelector(".production-stage-message");
  const errorBox = dialog && dialog.querySelector(".production-stage-dialog-error");
  const pendingStages = new Set();
  let opening = false;
  let saving = false;
  let opener = null;

  function notify(message, tone) {
    notice.textContent = message;
    notice.dataset.tone = tone;
    notice.hidden = false;
  }

  function errorText(payload, fallback) {
    const messages = [payload.message || fallback];
    Object.entries(payload.errors || {}).forEach(function (entry) {
      const name = entry[0] === "__all__" ? "" : entry[0].replaceAll("_", " ") + ": ";
      const values = Array.isArray(entry[1]) ? entry[1] : [entry[1]];
      values.forEach(function (value) { messages.push(name + (value.message || value)); });
    });
    return messages.filter(Boolean).join(" ");
  }

  async function request(url, options) {
    const response = await fetch(url, Object.assign({
      credentials: "same-origin",
      headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    }, options));
    const contentType = response.headers.get("Content-Type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error(response.redirected ? "Your session may have expired. Sign in again before saving." : "The stage could not be loaded or saved (HTTP " + response.status + "). Please try again.");
    }
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      const error = new Error(errorText(payload, "The stage could not be saved."));
      error.payload = payload;
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function replaceStatusClass(element, status, quick) {
    ["planned", "in_progress", "current", "done", "delay", "hold"].forEach(function (name) { element.classList.remove(name); });
    element.classList.add(quick && status === "in_progress" ? "current" : status);
  }

  function updateStage(stage, payload) {
    const card = panel.querySelector('[data-stage-card="' + stage.id + '"]');
    if (card) {
      replaceStatusClass(card, stage.status, false);
      card.querySelector("[data-stage-status]").textContent = stage.status_label;
      card.querySelector("[data-stage-planned]").textContent = (stage.planned_start || "—") + " to " + (stage.planned_end || "—");
      card.querySelector("[data-stage-started]").textContent = stage.actual_started_display || stage.actual_started_at || stage.actual_start || "—";
      card.querySelector("[data-stage-completed]").textContent = stage.actual_completed_display || stage.actual_completed_at || stage.actual_end || "—";
      const notes = card.querySelector("[data-stage-notes]");
      notes.textContent = stage.notes || "";
      notes.hidden = !stage.notes;
    }
    const quick = panel.querySelector('[data-stage-quick="' + stage.id + '"]');
    if (quick) {
      const button = quick.querySelector("button");
      replaceStatusClass(button, stage.status, true);
      quick.querySelector("[data-stage-status]").textContent = stage.status_label;
      quick.elements.expected_status.value = stage.status;
      quick.elements.version.value = stage.version;
    }
    if (payload && typeof payload.percent_done === "number") {
      const percent = Math.max(0, Math.min(100, payload.percent_done));
      panel.querySelector("[data-stage-percent]").textContent = percent + "%";
      panel.querySelector("[data-stage-progress-bar]").style.width = percent + "%";
      document.querySelectorAll("[data-stage-progress-label]").forEach(function (label) { label.textContent = percent + "% complete"; });
    }
    if (payload && typeof payload.order_delayed === "boolean") {
      panel.querySelector("[data-stage-progress-bar]").classList.toggle("risk", payload.order_delayed);
      panel.querySelector("[data-stage-progress-description]").textContent = payload.order_delayed ? "Review delay risk" : "Factory progress";
    }
    if (payload && payload.operational_status) {
      document.querySelectorAll("[data-stage-operational-status]").forEach(function (label) {
        const old = label.dataset.stageOperationalStatus;
        if (old) label.classList.remove(old);
        label.dataset.stageOperationalStatus = payload.operational_status;
        label.classList.add(payload.operational_status);
        label.textContent = payload.operational_status_label;
      });
    }
  }

  function fillEditor(stage) {
    form.dataset.stageId = String(stage.id);
    document.getElementById("production-stage-name").value = stage.name;
    ["status", "planned_start", "planned_end", "notes", "version"].forEach(function (name) { form.elements[name].value = stage[name] || ""; });
  }

  function showDialogError(error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    errorBox.focus();
  }

  async function openEditor(button) {
    if (!dialog || opening || saving || pendingStages.has(button.dataset.stageId)) return;
    opening = true;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try {
      const payload = await request(button.dataset.stageEditUrl);
      opener = button;
      form.action = button.dataset.stageEditUrl;
      fillEditor(payload.stage);
      updateStage(payload.stage);
      errorBox.hidden = true;
      dialog.showModal();
      form.elements.status.focus();
    } catch (error) {
      notify(error.message || "The stage could not be loaded. Please try again.", "error");
    } finally {
      opening = false;
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  panel.addEventListener("click", function (event) {
    const button = event.target.closest("[data-stage-edit-url]");
    if (button) openEditor(button);
  });

  panel.querySelectorAll("[data-stage-quick]").forEach(function (quick) {
    quick.addEventListener("submit", async function (event) {
      event.preventDefault();
      const stageId = quick.dataset.stageQuick;
      if (pendingStages.has(stageId)) return;
      pendingStages.add(stageId);
      const button = quick.querySelector("button");
      const data = new FormData(quick);
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
      try {
        const payload = await request(quick.action, {method: "POST", body: data});
        updateStage(payload.stage, payload);
        notify(payload.message || "Stage updated.", "success");
      } catch (error) {
        if (error.payload && error.payload.stage) updateStage(error.payload.stage);
        notify(error.message || "The stage could not be saved. Review the stage before trying again.", "error");
      } finally {
        pendingStages.delete(stageId);
        button.disabled = false;
        button.removeAttribute("aria-busy");
      }
    });
  });

  if (!dialog) return;
  dialog.querySelector("[data-stage-cancel]").addEventListener("click", function () { if (!saving) dialog.close(); });
  dialog.addEventListener("cancel", function (event) { if (saving) event.preventDefault(); });
  dialog.addEventListener("close", function () { if (opener && opener.isConnected) opener.focus(); });
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const stageId = form.dataset.stageId;
    if (saving || pendingStages.has(stageId) || !form.reportValidity()) return;
    saving = true;
    pendingStages.add(stageId);
    errorBox.hidden = true;
    const data = new FormData(form);
    const controls = Array.from(form.querySelectorAll("button, input, select, textarea"));
    controls.forEach(function (control) { control.disabled = true; });
    form.setAttribute("aria-busy", "true");
    try {
      const payload = await request(form.action, {method: "POST", body: data});
      updateStage(payload.stage, payload);
      fillEditor(payload.stage);
      dialog.close();
      notify(payload.message || "Stage saved.", "success");
    } catch (error) {
      if (error.payload && error.payload.stage) {
        updateStage(error.payload.stage);
        fillEditor(error.payload.stage);
      }
      showDialogError(error);
    } finally {
      controls.forEach(function (control) { control.disabled = false; });
      form.removeAttribute("aria-busy");
      saving = false;
      pendingStages.delete(stageId);
    }
  });
}());
