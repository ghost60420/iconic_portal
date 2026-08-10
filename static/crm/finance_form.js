(function () {
  "use strict";

  const page = document.querySelector(".ops-form-page");
  const form = document.querySelector(".ops-finance-form");
  if (!page || !form) return;

  let setupLinks = {};
  const setupNode = document.getElementById("finance-selector-setup");
  if (setupNode) {
    try {
      setupLinks = JSON.parse(setupNode.textContent || "{}");
    } catch (_error) {
      setupLinks = {};
    }
  }
  const canManageSetup = page.dataset.canManageSetup === "1";
  const registry = new Map();

  function icon(name) {
    const node = document.createElement("i");
    node.setAttribute("data-lucide", name);
    node.setAttribute("aria-hidden", "true");
    return node;
  }

  function selectedOption(select) {
    return Array.from(select.options).find((option) => option.value && option.selected) || null;
  }

  function optionPrimary(option) {
    return option ? (option.dataset.primary || option.textContent || "").trim() : "";
  }

  function optionSecondary(option) {
    return option ? (option.dataset.secondary || "").trim() : "";
  }

  function optionMatchesDependency(select, option) {
    const kind = select.dataset.financeSearch;
    const parentName = select.dataset.parentField;
    if (parentName) {
      const parent = form.elements[parentName];
      if (!parent || !parent.value) return false;
      const relatedId = kind === "invoice" ? option.dataset.customerId : option.dataset.supplierId;
      if (relatedId && relatedId !== parent.value) return false;
    }
    if (kind === "account") {
      const transferType = form.elements.transfer_type;
      if (transferType) {
        const directionSides = {
          CA_TO_BD: {from: "CA", to: "BD"},
          BD_TO_CA: {from: "BD", to: "CA"}
        };
        const direction = directionSides[transferType.value];
        let expectedSide = direction ? direction[select.name === "to_account" ? "to" : "from"] : "";
        if (!expectedSide && transferType.value === "INTERNAL") {
          const sourceOption = selectedOption(form.elements.from_account);
          expectedSide = page.dataset.country || (sourceOption ? sourceOption.dataset.side : "");
        }
        if (expectedSide && option.dataset.side && option.dataset.side !== expectedSide) return false;
      }
      const currency = select.name === "to_account" && form.elements.receiving_currency ?
        form.elements.receiving_currency : form.elements.currency;
      if (currency && currency.value && option.dataset.currency && option.dataset.currency !== currency.value) {
        return false;
      }
    }
    return !option.disabled;
  }

  function emptyMessage(select) {
    const kind = select.dataset.financeSearch;
    const parentName = select.dataset.parentField;
    if (parentName) {
      const parent = form.elements[parentName];
      if (!parent || !parent.value) {
        return kind === "invoice" ? "Select a customer to view eligible invoices." : "Select a supplier to view approved bills.";
      }
      return kind === "invoice" ? "No eligible invoices found for this customer." : "No approved outstanding bills found for this supplier.";
    }
    return select.dataset.emptyMessage || "No matching records.";
  }

  function enhanceSelect(select) {
    if (!select || select.dataset.financeEnhanced === "1") return null;
    select.dataset.financeEnhanced = "1";
    select.dataset.financeRequired = select.required ? "1" : "0";
    select.required = false;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    const host = document.createElement("div");
    host.className = "ops-selector";
    host.dataset.kind = select.dataset.financeSearch || "record";

    const control = document.createElement("div");
    control.className = "ops-selector-control";
    control.appendChild(icon("search"));

    const input = document.createElement("input");
    input.type = "search";
    input.className = "ops-selector-input";
    input.id = `${select.id}_search`;
    input.placeholder = select.dataset.searchPlaceholder || "Search...";
    input.autocomplete = "off";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", `${select.id}_results`);
    control.appendChild(input);

    const loading = document.createElement("span");
    loading.className = "ops-selector-loading";
    loading.textContent = "Searching...";
    loading.hidden = true;
    control.appendChild(loading);

    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "ops-selector-clear";
    clear.title = "Clear selection";
    clear.setAttribute("aria-label", "Clear selection");
    clear.appendChild(icon("x"));
    control.appendChild(clear);

    const results = document.createElement("div");
    results.className = "ops-selector-results";
    results.id = `${select.id}_results`;
    results.setAttribute("role", "listbox");
    results.hidden = true;

    select.parentNode.insertBefore(host, select);
    host.appendChild(control);
    host.appendChild(results);
    host.appendChild(select);

    const label = form.querySelector(`label[for="${select.id}"]`);
    if (label) label.htmlFor = input.id;

    const options = Array.from(select.options).filter((option) => option.value);
    let visibleButtons = [];
    let activeIndex = -1;
    let renderToken = 0;
    let suppressNextFocus = false;

    function closeResults() {
      results.hidden = true;
      input.setAttribute("aria-expanded", "false");
      activeIndex = -1;
    }

    function setActive(index) {
      if (!visibleButtons.length) return;
      activeIndex = Math.max(0, Math.min(index, visibleButtons.length - 1));
      visibleButtons.forEach((button, buttonIndex) => {
        button.classList.toggle("is-active", buttonIndex === activeIndex);
        button.setAttribute("aria-selected", buttonIndex === activeIndex ? "true" : "false");
      });
      visibleButtons[activeIndex].scrollIntoView({block: "nearest"});
    }

    function syncInput() {
      const option = selectedOption(select);
      input.value = optionPrimary(option);
      clear.hidden = !option;
      input.classList.toggle("has-selection", Boolean(option));
    }

    function choose(option) {
      const inputAlreadyFocused = document.activeElement === input;
      select.value = option.value;
      input.setCustomValidity("");
      syncInput();
      closeResults();
      select.dispatchEvent(new Event("change", {bubbles: true}));
      if (!inputAlreadyFocused) {
        suppressNextFocus = true;
        input.focus({preventScroll: true});
      }
    }

    function addEmptyState(message) {
      const empty = document.createElement("div");
      empty.className = "ops-selector-empty";
      empty.appendChild(icon("circle-alert"));
      const text = document.createElement("span");
      text.textContent = message;
      empty.appendChild(text);
      const setupUrl = setupLinks[select.dataset.financeSearch];
      if (canManageSetup && setupUrl) {
        const link = document.createElement("a");
        link.href = setupUrl;
        link.textContent = select.dataset.financeSearch === "account" ? "Add Bank Account" :
          select.dataset.financeSearch === "supplier" ? "Add Supplier" : "Configure";
        empty.appendChild(link);
      }
      results.appendChild(empty);
    }

    function renderResults() {
      const token = ++renderToken;
      loading.hidden = false;
      results.hidden = false;
      input.setAttribute("aria-expanded", "true");
      window.requestAnimationFrame(function () {
        if (token !== renderToken) return;
        const query = input.value.trim().toLowerCase();
        results.replaceChildren();
        visibleButtons = [];
        const matches = options.filter((option) => {
          if (!optionMatchesDependency(select, option)) return false;
          const text = `${option.dataset.search || ""} ${option.textContent || ""}`.toLowerCase();
          return !query || text.includes(query);
        });
        matches.slice(0, 60).forEach((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "ops-selector-option";
          button.setAttribute("role", "option");
          button.dataset.value = option.value;
          if (select.value === option.value) button.classList.add("is-selected");

          const primary = document.createElement("strong");
          primary.textContent = optionPrimary(option);
          button.appendChild(primary);
          const secondaryText = optionSecondary(option);
          if (secondaryText) {
            const secondary = document.createElement("small");
            secondary.textContent = secondaryText;
            button.appendChild(secondary);
          }
          if (option.dataset.group) {
            const group = document.createElement("span");
            group.className = "ops-selector-group";
            group.textContent = option.dataset.group;
            button.appendChild(group);
          }
          button.addEventListener("click", function () { choose(option); });
          results.appendChild(button);
          visibleButtons.push(button);
        });
        if (!matches.length) addEmptyState(emptyMessage(select));
        if (matches.length > 60) {
          const more = document.createElement("div");
          more.className = "ops-selector-more";
          more.textContent = `${matches.length - 60} more results. Keep typing to narrow the list.`;
          results.appendChild(more);
        }
        loading.hidden = true;
        activeIndex = -1;
        if (window.lucide) window.lucide.createIcons();
      });
    }

    input.addEventListener("focus", function () {
      if (suppressNextFocus) {
        suppressNextFocus = false;
        return;
      }
      if (selectedOption(select)) input.select();
      renderResults();
    });
    input.addEventListener("input", function () {
      input.setCustomValidity("");
      renderResults();
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (results.hidden) renderResults();
        setActive(activeIndex + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive(activeIndex <= 0 ? visibleButtons.length - 1 : activeIndex - 1);
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        visibleButtons[activeIndex].click();
      } else if (event.key === "Escape") {
        closeResults();
      }
    });
    clear.addEventListener("click", function () {
      select.value = "";
      input.value = "";
      input.setCustomValidity("");
      syncInput();
      select.dispatchEvent(new Event("change", {bubbles: true}));
      input.focus();
      renderResults();
    });
    select.addEventListener("change", syncInput);
    document.addEventListener("click", function (event) {
      if (!host.contains(event.target)) closeResults();
    });

    syncInput();
    const controller = {select, input, renderResults, syncInput};
    registry.set(select.name, controller);
    return controller;
  }

  document.querySelectorAll("select.finance-searchable-select").forEach(enhanceSelect);

  registry.forEach(function (controller) {
    const select = controller.select;
    const parentName = select.dataset.parentField;
    if (!parentName) return;
    const parent = form.elements[parentName];
    if (!parent) return;
    parent.addEventListener("change", function () {
      const option = selectedOption(select);
      if (option && !optionMatchesDependency(select, option)) {
        select.value = "";
        select.dispatchEvent(new Event("change", {bubbles: true}));
      }
      controller.syncInput();
      if (document.activeElement === controller.input) controller.renderResults();
    });
  });

  const currencySelect = form.elements.currency;
  const receivingCurrencySelect = form.elements.receiving_currency;
  function refreshAccountSelectors() {
    registry.forEach(function (controller) {
      if (controller.select.dataset.financeSearch !== "account") return;
      const option = selectedOption(controller.select);
      if (option && !optionMatchesDependency(controller.select, option)) {
        controller.select.value = "";
        controller.select.dispatchEvent(new Event("change", {bubbles: true}));
      }
      controller.syncInput();
      if (document.activeElement === controller.input) controller.renderResults();
    });
  }
  if (currencySelect) {
    currencySelect.addEventListener("change", refreshAccountSelectors);
  }
  if (receivingCurrencySelect) receivingCurrencySelect.addEventListener("change", refreshAccountSelectors);

  registry.forEach(function (controller) {
    const select = controller.select;
    select.addEventListener("change", function () {
      const option = selectedOption(select);
      if (!option || !currencySelect) return;
      const shouldSetCurrency = select.dataset.financeSearch === "invoice" ||
        select.dataset.financeSearch === "supplier_bill" || select.name === "supplier";
      if (shouldSetCurrency && option.dataset.currency) {
        currencySelect.value = option.dataset.currency;
        currencySelect.dispatchEvent(new Event("change", {bubbles: true}));
      }
      if (select.name === "from_account" && option.dataset.currency) {
        currencySelect.value = option.dataset.currency;
        currencySelect.dispatchEvent(new Event("change", {bubbles: true}));
      }
      if (select.name === "to_account" && receivingCurrencySelect && option.dataset.currency) {
        receivingCurrencySelect.value = option.dataset.currency;
        receivingCurrencySelect.dispatchEvent(new Event("change", {bubbles: true}));
      }
    });
  });

  function formatMoney(currency, value) {
    const numeric = Number(value);
    if (!currency || !Number.isFinite(numeric)) return "-";
    return `${currency} ${numeric.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  function supplierBillTotal(option) {
    if (!option) return NaN;
    const match = optionSecondary(option).match(/\|\s*[A-Z]{3}\s+([\d,]+(?:\.\d+)?)\s+total\s*\|/i);
    return match ? Number(match[1].replaceAll(",", "")) : NaN;
  }

  function updatePaymentSummary() {
    const summary = form.parentElement.querySelector("[data-payment-summary]");
    if (!summary) return;
    const summaryKind = summary.dataset.paymentSummary;
    const amountInput = form.elements.amount;
    const amount = Number(amountInput && amountInput.value ? amountInput.value : 0);
    let currency = currencySelect ? currencySelect.value : "";
    let outstanding = NaN;
    let total = NaN;
    let paid = NaN;

    if (summaryKind === "supplier") {
      const supplier = selectedOption(form.elements.supplier);
      const bill = selectedOption(form.elements.supplier_bill);
      currency = bill ? bill.dataset.currency : currency;
      outstanding = Number(bill ? bill.dataset.outstanding : NaN);
      total = supplierBillTotal(bill);
      paid = Number.isFinite(total) && Number.isFinite(outstanding) ? total - outstanding : NaN;
      summary.querySelector('[data-summary="supplier"]').textContent = optionPrimary(supplier) || "Not selected";
      summary.querySelector('[data-summary="bill"]').textContent = optionPrimary(bill) || "Not selected";
    } else {
      const customer = selectedOption(form.elements.customer);
      const invoice = selectedOption(form.elements.invoice);
      currency = invoice ? invoice.dataset.currency : currency;
      outstanding = Number(invoice ? invoice.dataset.outstanding : NaN);
      total = Number(invoice ? invoice.dataset.total : NaN);
      paid = Number(invoice ? invoice.dataset.paid : NaN);
      summary.querySelector('[data-summary="customer"]').textContent = optionPrimary(customer) || "Not selected";
      summary.querySelector('[data-summary="invoice"]').textContent = optionPrimary(invoice) || "Not selected";
    }

    const remaining = Number.isFinite(outstanding) ? outstanding - amount : NaN;
    summary.querySelector('[data-summary="total"]').textContent = formatMoney(currency, total);
    summary.querySelector('[data-summary="paid"]').textContent = formatMoney(currency, paid);
    summary.querySelector('[data-summary="outstanding"]').textContent = formatMoney(currency, outstanding);
    summary.querySelector('[data-summary="payment"]').textContent = amount > 0 ? formatMoney(currency, amount) : "-";
    const remainingNode = summary.querySelector('[data-summary="remaining"]');
    remainingNode.textContent = formatMoney(currency, remaining);
    remainingNode.classList.toggle("is-negative", Number.isFinite(remaining) && remaining < 0);
  }

  [form.elements.customer, form.elements.invoice, form.elements.supplier, form.elements.supplier_bill, form.elements.amount].filter(Boolean).forEach(function (field) {
    field.addEventListener(field.tagName === "INPUT" ? "input" : "change", updatePaymentSummary);
  });
  updatePaymentSummary();

  const transferSummary = form.parentElement.querySelector("[data-transfer-summary]");
  const transferType = form.elements.transfer_type;
  function transferField(name) {
    return form.querySelector(`.field-${name}`);
  }

  function setTransferFieldVisible(name, visible) {
    const field = transferField(name);
    if (field) field.classList.toggle("ops-transfer-hidden", !visible);
  }

  function refreshTransferSections() {
    form.querySelectorAll(".ops-form-section").forEach(function (section) {
      const fields = Array.from(section.querySelectorAll(".ops-field"));
      if (!fields.length) return;
      section.classList.toggle("ops-transfer-hidden", fields.every((field) => field.classList.contains("ops-transfer-hidden")));
    });
  }

  function transferChoiceLabel(field) {
    if (!field || !field.value) return "";
    const option = field.options[field.selectedIndex];
    return option ? option.textContent.trim() : "";
  }

  function updateTransferSummary() {
    if (!transferSummary || !transferType) return;
    const setValue = function (key, value) {
      const node = transferSummary.querySelector(`[data-transfer-summary-value="${key}"]`);
      if (node) node.textContent = value;
    };
    const source = selectedOption(form.elements.from_account);
    const destination = selectedOption(form.elements.to_account);
    const amountSent = form.elements.amount ? form.elements.amount.value : "";
    const amountReceived = form.elements.destination_amount ? form.elements.destination_amount.value : "";
    const sendingCurrency = currencySelect ? currencySelect.value : "";
    const receivingCurrency = receivingCurrencySelect ? receivingCurrencySelect.value : "";
    const fee = form.elements.transfer_fee ? form.elements.transfer_fee.value : "";
    const feeCurrency = form.elements.fee_currency ? form.elements.fee_currency.value : sendingCurrency;
    const service = form.elements.transfer_service;
    const otherService = form.elements.other_transfer_service;
    let serviceLabel = transferChoiceLabel(service) || "Internal";
    if (service && service.value === "OTHER" && otherService && otherService.value.trim()) {
      serviceLabel = otherService.value.trim();
    }
    let rate = "-";
    const sentNumber = Number(amountSent);
    const receivedNumber = Number(amountReceived);
    if (sentNumber > 0 && receivedNumber > 0) {
      if (sendingCurrency === "BDT" && receivingCurrency === "CAD") {
        rate = `1 CAD = ${(sentNumber / receivedNumber).toLocaleString(undefined, {maximumFractionDigits: 6})} BDT`;
      } else {
        rate = `1 ${sendingCurrency} = ${(receivedNumber / sentNumber).toLocaleString(undefined, {maximumFractionDigits: 6})} ${receivingCurrency}`;
      }
    }
    setValue("direction", transferChoiceLabel(transferType) || "Internal Account Transfer");
    setValue("from", optionPrimary(source) || "Not selected");
    setValue("sent", amountSent ? formatMoney(sendingCurrency, amountSent) : "-");
    setValue("service", serviceLabel);
    setValue("fee", Number(fee) > 0 ? formatMoney(feeCurrency, fee) : `${feeCurrency || sendingCurrency} 0.00`);
    setValue("to", optionPrimary(destination) || "Not selected");
    setValue("received", amountReceived ? formatMoney(receivingCurrency, amountReceived) : "-");
    setValue("rate", rate);
    setValue("purpose", transferChoiceLabel(form.elements.business_purpose) || "Not selected");
  }

  function updateTransferUI() {
    if (!transferType) return;
    const crossCountry = transferType.value === "CA_TO_BD" || transferType.value === "BD_TO_CA";
    if (transferType.value === "CA_TO_BD") {
      currencySelect.value = "CAD";
      receivingCurrencySelect.value = "BDT";
      if (form.elements.fee_currency) form.elements.fee_currency.value = "CAD";
    } else if (transferType.value === "BD_TO_CA") {
      currencySelect.value = "BDT";
      receivingCurrencySelect.value = "CAD";
      if (form.elements.fee_currency) form.elements.fee_currency.value = "BDT";
    }
    setTransferFieldVisible("transfer_service", crossCountry);
    setTransferFieldVisible(
      "other_transfer_service",
      crossCountry && form.elements.transfer_service && form.elements.transfer_service.value === "OTHER"
    );
    setTransferFieldVisible("provider_exchange_rate", crossCountry);
    setTransferFieldVisible("transfer_fee", crossCountry);
    setTransferFieldVisible("fee_currency", crossCountry);
    refreshTransferSections();
    refreshAccountSelectors();
    updateTransferSummary();
  }

  if (transferType) {
    transferType.addEventListener("change", updateTransferUI);
    if (form.elements.transfer_service) form.elements.transfer_service.addEventListener("change", updateTransferUI);
    [
      form.elements.from_account, form.elements.to_account, form.elements.amount,
      form.elements.destination_amount, currencySelect, receivingCurrencySelect,
      form.elements.transfer_fee, form.elements.fee_currency, form.elements.other_transfer_service,
      form.elements.business_purpose
    ].filter(Boolean).forEach(function (field) {
      field.addEventListener(field.tagName === "INPUT" ? "input" : "change", updateTransferSummary);
    });
    updateTransferUI();
  }

  form.addEventListener("submit", function (event) {
    let invalidController = null;
    registry.forEach(function (controller) {
      if (!invalidController && controller.select.dataset.financeRequired === "1" && !controller.select.value) {
        invalidController = controller;
      }
    });
    if (invalidController) {
      event.preventDefault();
      invalidController.input.setCustomValidity("Select a valid option from the list.");
      invalidController.input.reportValidity();
      invalidController.input.focus();
      invalidController.renderResults();
    }
  });

  if (window.lucide) window.lucide.createIcons();
})();
