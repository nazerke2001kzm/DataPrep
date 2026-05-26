(function () {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file");
  const pickBtn = document.getElementById("pick");
  const fileNameEl = document.getElementById("fileName");
  const runBtn = document.getElementById("run");
  const resetBtn = document.getElementById("reset");
  const previewOnly = document.getElementById("previewOnly");
  const bundle = document.getElementById("bundle");
  const docxReport = document.getElementById("docxReport");
  const panel = document.getElementById("panel");
  const panelTitle = document.getElementById("panelTitle");
  const alertEl = document.getElementById("alert");
  const jsonOut = document.getElementById("jsonOut");
  const jsonPre = document.getElementById("jsonPre");
  const downloadRow = document.getElementById("downloadRow");
  const okMsg = document.getElementById("okMsg");
  const downloadLink = document.getElementById("downloadLink");
  const closePanel = document.getElementById("closePanel");
  const spinner = document.getElementById("spinner");
  const btnLabel = document.querySelector("#run .btn-label");
  const presetHint = document.getElementById("presetHint");
  const tabButtons = Array.from(document.querySelectorAll(".sidebar-tab[data-preset]"));

  const dataPreview = document.getElementById("dataPreview");
  const previewStatus = document.getElementById("previewStatus");
  const previewMeta = document.getElementById("previewMeta");
  const previewNullsWrap = document.getElementById("previewNullsWrap");
  const previewNullsBody = document.getElementById("previewNullsBody");
  const previewSampleLabel = document.getElementById("previewSampleLabel");
  const previewSampleScroller = document.getElementById("previewSampleScroller");
  const previewSampleHead = document.getElementById("previewSampleHead");
  const previewSampleBody = document.getElementById("previewSampleBody");

  /** @type {AbortController | null} */
  let previewLoadAbort = null;

  /** Счётчик запросов превью — игнорируем устаревший ответ при быстрой смене файла. */
  let previewRequestId = 0;

  /** @type {File | null} */
  let currentFile = null;

  /** @type {"antifraud"|"legal_entity_scoring"|"individual_scoring"|"credit_transition_matrix"} */
  let selectedPreset = "antifraud";

  const PRESET_HINTS = {
    antifraud:
      "Транзакции и события: антиутечки по времени, дубликаты операций, аккуратная работа с суммами и редкими категориями.",
    legal_entity_scoring:
      "Отчётность и контрагенты: ИНН/ОГРН, финпоказатели, отрасль; проверка знаков, единиц измерения и as-of даты.",
    individual_scoring:
      "Заявки физлиц: возраст/доход/бюро-скоры; комплаенс по признакам; коды «нет данных» вместо слепого median.",
    credit_transition_matrix:
      "Миграции по корзинам: wide или long; неотрицательность, суммы по строкам ≈ 1 для долей; без лишнего биннинга корзин.",
  };

  function applyPresetUI(preset) {
    selectedPreset = preset;
    tabButtons.forEach((btn) => {
      const on = btn.dataset.preset === preset;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (presetHint && PRESET_HINTS[preset]) presetHint.textContent = PRESET_HINTS[preset];
  }

  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => applyPresetUI(btn.dataset.preset));
  });

  /** @type {string | null} */
  let objectUrlToRevoke = null;

  function setLoading(on) {
    runBtn.disabled = on || !currentFile;
    spinner.hidden = !on;
    btnLabel.textContent = on ? "Обработка…" : "Обработать";
  }

  function clearAlerts() {
    alertEl.hidden = true;
    alertEl.textContent = "";
    jsonOut.hidden = true;
    jsonPre.textContent = "";
    downloadRow.hidden = true;
    if (objectUrlToRevoke) {
      URL.revokeObjectURL(objectUrlToRevoke);
      objectUrlToRevoke = null;
    }
    downloadLink.removeAttribute("href");
  }

  function showPanel() {
    panel.hidden = false;
  }

  function hidePanel() {
    panel.hidden = true;
    clearAlerts();
  }

  function validateFile(file) {
    const ok = /\.(csv|xlsx|xlsm)$/i.test(file.name);
    if (!ok) {
      showPanel();
      alertEl.hidden = false;
      alertEl.textContent = "Нужен файл .csv, .xlsx или .xlsm";
      panelTitle.textContent = "Ошибка";
      return false;
    }
    return true;
  }

  function setFile(file) {
    clearAlerts();
    if (!validateFile(file)) {
      currentFile = null;
      runBtn.disabled = true;
      hideDataPreview();
      return;
    }
    hidePanel();
    currentFile = file;
    fileNameEl.hidden = false;
    fileNameEl.textContent = file.name;
    runBtn.disabled = false;
    loadDataPreview(file);
  }

  function parseFilename(cd) {
    if (!cd) return null;
    const m = /filename\*?=(?:UTF-8''|")?([^";\n]+)/i.exec(cd);
    if (!m) return null;
    let name = m[1].replace(/"$/, "").trim();
    try {
      name = decodeURIComponent(name);
    } catch (_) {
      /* ignore */
    }
    return name || null;
  }

  function hideDataPreview() {
    if (previewLoadAbort) previewLoadAbort.abort();
    previewLoadAbort = null;
    previewRequestId += 1;
    if (!dataPreview) return;
    dataPreview.classList.add("is-hidden");
    if (previewStatus) {
      previewStatus.textContent = "";
      previewStatus.classList.remove("preview-error");
    }
    if (previewMeta) previewMeta.replaceChildren();
    if (previewNullsBody) previewNullsBody.replaceChildren();
    if (previewNullsWrap) previewNullsWrap.hidden = true;
    if (previewSampleHead) previewSampleHead.replaceChildren();
    if (previewSampleBody) previewSampleBody.replaceChildren();
    if (previewSampleLabel) previewSampleLabel.hidden = true;
    if (previewSampleScroller) previewSampleScroller.hidden = true;
  }

  function formatPreviewCell(value) {
    if (value === null || value === undefined) return { text: "—", na: true };
    if (typeof value === "object") {
      try {
        return { text: JSON.stringify(value), na: false };
      } catch (_) {
        return { text: String(value), na: false };
      }
    }
    return { text: String(value), na: false };
  }

  async function loadDataPreview(file) {
    if (!dataPreview || !previewStatus || !previewMeta) return;
    const requestId = ++previewRequestId;
    dataPreview.classList.remove("is-hidden");
    previewStatus.textContent = "Загрузка превью…";
    previewStatus.classList.remove("preview-error");
    previewMeta.replaceChildren();
    if (previewNullsBody) previewNullsBody.replaceChildren();
    if (previewNullsWrap) previewNullsWrap.hidden = true;
    if (previewSampleHead) previewSampleHead.replaceChildren();
    if (previewSampleBody) previewSampleBody.replaceChildren();
    if (previewSampleLabel) previewSampleLabel.hidden = true;
    if (previewSampleScroller) previewSampleScroller.hidden = true;

    if (previewLoadAbort) previewLoadAbort.abort();
    previewLoadAbort = new AbortController();

    const fd = new FormData();
    fd.append("file", file, file.name);

    const previewUrl = new URL("preview-dataset", window.location.href);
    previewUrl.searchParams.set("sample_rows", "10");

    try {
      const res = await fetch(previewUrl.toString(), {
        method: "POST",
        body: fd,
        signal: previewLoadAbort.signal,
      });
      if (!res.ok) {
        let msg = `${res.status} ${res.statusText}`;
        try {
          const err = await res.json();
          if (err.detail) msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
        } catch (_) {
          const t = await res.text();
          if (t) msg = t.slice(0, 400);
        }
        if (requestId !== previewRequestId) return;
        previewStatus.textContent = msg;
        previewStatus.classList.add("preview-error");
        return;
      }
      /** @type {any} */
      const data = await res.json();
      if (requestId !== previewRequestId) return;
      previewStatus.textContent = `Показано до ${data.sample_row_count ?? 0} из ${data.row_count} строк.`;

      const pFile = document.createElement("p");
      const sFile = document.createElement("strong");
      sFile.textContent = "Файл: ";
      pFile.appendChild(sFile);
      pFile.appendChild(document.createTextNode(data.filename || ""));
      previewMeta.appendChild(pFile);

      const pDim = document.createElement("p");
      const sRows = document.createElement("strong");
      sRows.textContent = "Строк: ";
      pDim.appendChild(sRows);
      pDim.appendChild(document.createTextNode(String(data.row_count ?? "—")));
      pDim.appendChild(document.createTextNode("  "));
      const sCols = document.createElement("strong");
      sCols.textContent = "Колонок: ";
      pDim.appendChild(sCols);
      pDim.appendChild(document.createTextNode(String(data.column_count ?? "—")));
      previewMeta.appendChild(pDim);

      const pCols = document.createElement("p");
      const sColsLabel = document.createElement("strong");
      sColsLabel.textContent = "Имена колонок: ";
      pCols.appendChild(sColsLabel);
      const colsSpan = document.createElement("span");
      colsSpan.className = "preview-cols-list";
      colsSpan.textContent = Array.isArray(data.columns) ? data.columns.join(", ") : "—";
      pCols.appendChild(colsSpan);
      previewMeta.appendChild(pCols);

      if (data.dtypes && typeof data.dtypes === "object") {
        const pDt = document.createElement("p");
        const sDt = document.createElement("strong");
        sDt.textContent = "Типы (pandas): ";
        pDt.appendChild(sDt);
        const dtSpan = document.createElement("span");
        dtSpan.className = "preview-cols-list";
        dtSpan.textContent = (data.columns || [])
          .map((c) => `${c}: ${data.dtypes[c] ?? "?"}`)
          .join("; ");
        pDt.appendChild(dtSpan);
        previewMeta.appendChild(pDt);
      }

      if (previewNullsWrap && previewNullsBody && data.null_counts) {
        previewNullsWrap.hidden = false;
        const entries = Object.entries(data.null_counts).sort((a, b) => b[1] - a[1]);
        const rc = Number(data.row_count) || 0;
        for (const [col, cnt] of entries) {
          const tr = document.createElement("tr");
          const tdN = document.createElement("td");
          tdN.textContent = col;
          tdN.title = col;
          const tdC = document.createElement("td");
          tdC.textContent = String(cnt);
          const tdP = document.createElement("td");
          tdP.textContent = rc ? `${((Number(cnt) / rc) * 100).toFixed(2)}%` : "0%";
          tr.appendChild(tdN);
          tr.appendChild(tdC);
          tr.appendChild(tdP);
          previewNullsBody.appendChild(tr);
        }
      }

      const cols = Array.isArray(data.columns) ? data.columns : [];
      window.__previewColumns = cols;
      const rows = Array.isArray(data.sample_rows) ? data.sample_rows : [];
      if (cols.length && previewSampleHead && previewSampleBody && previewSampleLabel && previewSampleScroller) {
        previewSampleLabel.hidden = false;
        previewSampleScroller.hidden = false;
        const hr = document.createElement("tr");
        const thIdx = document.createElement("th");
        thIdx.className = "row-num";
        thIdx.textContent = "№";
        thIdx.title = "Номер строки в файле (1-based)";
        hr.appendChild(thIdx);
        for (const c of cols) {
          const th = document.createElement("th");
          th.textContent = c;
          th.title = c;
          hr.appendChild(th);
        }
        previewSampleHead.appendChild(hr);
        let rowNum = 0;
        for (const row of rows) {
          rowNum += 1;
          const tr = document.createElement("tr");
          const tdIdx = document.createElement("td");
          tdIdx.className = "row-num";
          tdIdx.textContent = String(rowNum);
          tr.appendChild(tdIdx);
          for (const c of cols) {
            const td = document.createElement("td");
            const cell = formatPreviewCell(row[c]);
            td.textContent = cell.text;
            if (cell.na) td.classList.add("na-cell");
            td.title = cell.text;
            tr.appendChild(td);
          }
          previewSampleBody.appendChild(tr);
        }
      }
      dataPreview.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      if (e && typeof e === "object" && "name" in e && e.name === "AbortError") return;
      if (requestId !== previewRequestId) return;
      previewStatus.textContent = e instanceof Error ? e.message : String(e);
      previewStatus.classList.add("preview-error");
    }
  }

  function syncDocxAvailability() {
    if (!docxReport) return;
    const on = bundle.checked && !previewOnly.checked;
    docxReport.disabled = !on;
    if (!on) docxReport.checked = false;
  }

  previewOnly.addEventListener("change", () => {
    if (previewOnly.checked) bundle.checked = false;
    bundle.disabled = previewOnly.checked;
    syncDocxAvailability();
    runBtn.disabled = !currentFile;
  });

  bundle.addEventListener("change", () => {
    if (bundle.checked) previewOnly.checked = false;
    syncDocxAvailability();
  });

  docxReport?.addEventListener("change", () => {
    if (docxReport.checked) {
      bundle.checked = true;
      previewOnly.checked = false;
      bundle.disabled = false;
    }
    syncDocxAvailability();
  });

  syncDocxAvailability();

  pickBtn.addEventListener("click", () => {
    fileInput.value = "";
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0];
    if (f) setFile(f);
  });

  ["dragenter", "dragover"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragging");
    })
  );

  ["dragleave", "dragend"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragging");
    })
  );

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragging");
    const f = e.dataTransfer?.files?.[0];
    if (f) {
      fileInput.files = e.dataTransfer.files;
      setFile(f);
    }
  });

  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  resetBtn.addEventListener("click", () => {
    currentFile = null;
    fileInput.value = "";
    fileNameEl.hidden = true;
    runBtn.disabled = true;
    hideDataPreview();
    hidePanel();
    window.__clearPrepContext?.();
  });

  closePanel.addEventListener("click", hidePanel);

  runBtn.addEventListener("click", async () => {
    if (!currentFile) return;
    clearAlerts();
    showPanel();
    panelTitle.textContent = "Результат";

    const bundleOn = previewOnly.checked ? false : bundle.checked;
    const params = new URLSearchParams({
      preset: selectedPreset,
      preview_only: previewOnly.checked ? "true" : "false",
      bundle: bundleOn ? "true" : "false",
      docx_report: bundleOn && docxReport?.checked ? "true" : "false",
    });

    const fd = new FormData();
    fd.append("file", currentFile, currentFile.name);
    const guidancePayload = window.buildUserGuidanceJSON?.();
    if (guidancePayload) {
      fd.append("user_guidance", JSON.stringify(guidancePayload));
    }

    setLoading(true);
    try {
      const res = await fetch(`/prepare-dataset?${params}`, {
        method: "POST",
        body: fd,
      });

      if (!res.ok) {
        let msg = `${res.status} ${res.statusText}`;
        try {
          const err = await res.json();
          if (err.detail) msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
        } catch (_) {
          const t = await res.text();
          if (t) msg = t.slice(0, 500);
        }
        alertEl.hidden = false;
        alertEl.textContent = msg;
        panelTitle.textContent = "Ошибка";
        return;
      }

      window.__applyPrepContextFromResponse?.(res);

      if (previewOnly.checked) {
        const data = await res.json();
        jsonOut.hidden = false;
        jsonPre.textContent = JSON.stringify(data, null, 2);
        panelTitle.textContent = "План подготовки (JSON)";
        return;
      }

      const blob = await res.blob();
      const fname = parseFilename(res.headers.get("Content-Disposition")) || "dataset_prepared.csv";
      objectUrlToRevoke = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrlToRevoke;
      a.download = fname;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();

      downloadLink.href = objectUrlToRevoke;
      downloadLink.download = fname;
      downloadRow.hidden = false;
      okMsg.textContent = bundleOn
        ? `Архив сохранён: ${fname}${docxReport?.checked ? " (внутри report.docx при успешной генерации)" : ""}`
        : `Файл сохранён: ${fname}`;
      panelTitle.textContent = "Готово";
    } catch (e) {
      alertEl.hidden = false;
      alertEl.textContent = e instanceof Error ? e.message : String(e);
      panelTitle.textContent = "Ошибка сети";
    } finally {
      setLoading(false);
    }
  });
})();

(function () {
  const chatLog = document.getElementById("chatLog");
  const chatInput = document.getElementById("chatInput");
  const chatSend = document.getElementById("chatSend");
  const chatSendLabel = document.getElementById("chatSendLabel");
  const chatSpinner = document.getElementById("chatSpinner");
  const chatClear = document.getElementById("chatClear");

  /** @type {{ role: 'user' | 'assistant', content: string }[]} */
  let chatHistory = [];

  function syncPrepContextBanner() {
    const el = document.getElementById("chatContextHint");
    if (!el) return;
    const c = window.__prepContext;
    if (c && typeof c === "object" && c.filename) {
      el.hidden = false;
      const preset = c.preset_label_ru || c.preset || "";
      const rows =
        c.rows_after != null && c.rows_after !== undefined
          ? `${c.rows_before} → ${c.rows_after} строк`
          : `${c.rows_before} строк (только план JSON)`;
      el.textContent = `Ответы привязаны к последнему запуску: «${c.filename}», пресет «${preset}». ${rows}.`;
    } else {
      el.hidden = true;
      el.textContent = "";
    }
  }

  window.__applyPrepContextFromResponse = function (res) {
    const h = res.headers.get("X-Prep-Context");
    if (!h) return;
    try {
      window.__prepContext = JSON.parse(atob(h.trim()));
    } catch (_) {
      window.__prepContext = null;
    }
    syncPrepContextBanner();
  };

  window.__clearPrepContext = function () {
    window.__prepContext = null;
    syncPrepContextBanner();
  };

  function setChatLoading(on) {
    chatSend.disabled = on;
    chatInput.disabled = on;
    chatSpinner.hidden = !on;
    chatSendLabel.textContent = on ? "Отправка…" : "Отправить";
  }

  function appendBubble(role, text, extraClass) {
    const wrap = document.createElement("div");
    wrap.className = `chat-msg ${role} chat-dynamic${extraClass ? ` ${extraClass}` : ""}`;
    const roleEl = document.createElement("span");
    roleEl.className = "chat-role";
    roleEl.textContent = role === "user" ? "Вы" : "Агент";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = text;
    wrap.appendChild(roleEl);
    wrap.appendChild(bubble);
    chatLog.appendChild(wrap);
    chatLog.scrollTop = chatLog.scrollHeight;
    return wrap;
  }

  function appendError(text) {
    const el = document.createElement("div");
    el.className = "chat-error chat-dynamic";
    el.textContent = text;
    chatLog.appendChild(el);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  async function sendChat() {
    const msg = chatInput.value.trim();
    if (!msg) return;

    appendBubble("user", msg);
    chatInput.value = "";
    const pending = appendBubble("assistant", "…", "pending");

    setChatLoading(true);
    try {
      const payload = {
        message: msg,
        history: chatHistory,
        processing_context: window.__prepContext || null,
      };
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      pending.remove();

      if (!res.ok) {
        const detail = data.detail;
        let errText = `${res.status} ${res.statusText}`;
        if (typeof detail === "string") errText = detail;
        else if (Array.isArray(detail)) errText = detail.map((x) => x.msg || JSON.stringify(x)).join("; ");
        else if (detail != null) errText = JSON.stringify(detail);
        appendError(errText);
        return;
      }

      const reply = data.reply || "";
      chatHistory.push({ role: "user", content: msg });
      chatHistory.push({ role: "assistant", content: reply });
      if (chatHistory.length > 40) {
        chatHistory = chatHistory.slice(-40);
      }
      appendBubble("assistant", reply || "(пустой ответ)");
    } catch (e) {
      pending.remove();
      appendError(e instanceof Error ? e.message : String(e));
    } finally {
      setChatLoading(false);
      chatInput.focus();
    }
  }

  chatSend.addEventListener("click", () => sendChat());

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendChat();
    }
  });

  chatClear.addEventListener("click", () => {
    chatHistory = [];
    document.querySelectorAll("#chatLog .chat-dynamic").forEach((n) => n.remove());
    chatInput.focus();
  });
})();

(function () {
  const STORAGE_KEY = "datasetPrepUserGuidance";
  const columnDescriptionsText = document.getElementById("columnDescriptionsText");
  const validationRulesList = document.getElementById("validationRulesList");
  const freeformInstructions = document.getElementById("freeformInstructions");
  const addRuleRow = document.getElementById("addRuleRow");
  const guidanceSyncCols = document.getElementById("guidanceSyncCols");

  const RULE_OPTIONS = [
    ["must_be_numeric", "Число"],
    ["must_be_integer", "Целое"],
    ["must_be_datetime", "Дата/время"],
    ["date_year_min", "Год не раньше…"],
    ["date_year_max", "Год не позже…"],
    ["numeric_min", "Число ≥"],
    ["numeric_max", "Число ≤"],
    ["string_length_min", "Мин. длина строки"],
    ["string_length_max", "Макс. длина строки"],
    ["regex_extract_digits", "Только цифры (regex)"],
    ["regex_extract", "Извлечь по regex"],
    ["regex_replace", "Замена regex"],
    ["regex_must_match", "Строка по шаблону"],
    ["not_null", "Не пусто"],
    ["allowed_values", "Список значений"],
    ["unique", "Уникальные значения"],
  ];

  const ON_FAIL = [
    ["null", "Обнулить / NA"],
    ["drop_row", "Удалить строку"],
    ["keep", "Оставить как есть"],
  ];

  function columnDatalistId() {
    return "guidance-cols-datalist";
  }

  function ensureColDatalist() {
    let dl = document.getElementById(columnDatalistId());
    if (!dl) {
      dl = document.createElement("datalist");
      dl.id = columnDatalistId();
      document.body.appendChild(dl);
    }
    dl.innerHTML = "";
    const cols = window.__previewColumns || [];
    for (const c of cols) {
      const opt = document.createElement("option");
      opt.value = c;
      dl.appendChild(opt);
    }
    return dl.id;
  }

  function colInput(placeholder) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.placeholder = placeholder || "колонка";
    inp.setAttribute("list", ensureColDatalist());
    return inp;
  }

  function paramFieldsForRule(rule) {
    const wrap = document.createElement("div");
    wrap.className = "rule-params";
    const add = (label, name, type, def) => {
      const lab = document.createElement("label");
      lab.textContent = label;
      const inp = document.createElement("input");
      inp.type = type;
      inp.dataset.param = name;
      if (def != null) inp.value = String(def);
      lab.appendChild(inp);
      wrap.appendChild(lab);
    };
    if (rule === "date_year_min") add("мин. год", "min_year", "number", "1920");
    if (rule === "date_year_max") add("макс. год", "max_year", "number", "2100");
    if (rule === "numeric_min") add("мин.", "min", "number", "0");
    if (rule === "numeric_max") add("макс.", "max", "number", "");
    if (rule === "string_length_min") add("мин. длина", "min_length", "number", "1");
    if (rule === "string_length_max") add("макс. длина", "max_length", "number", "12");
    if (rule === "regex_extract_digits" || rule === "regex_extract" || rule === "regex_replace") {
      add("новая колонка", "output_column", "text", "");
    }
    if (rule === "regex_extract" || rule === "regex_replace" || rule === "regex_must_match") {
      add("шаблон", "pattern", "text", rule === "regex_extract_digits" ? "" : "\\d+");
    }
    if (rule === "regex_replace") add("замена", "replacement", "text", "");
    if (rule === "allowed_values") add("значения через ;", "values", "text", "");
    return wrap;
  }

  function createRuleRow(data) {
    const row = document.createElement("div");
    row.className = "guidance-row rule-row";
    const cInp = colInput("колонка");
    cInp.value = data?.column || "";
    const ruleSel = document.createElement("select");
    for (const [val, label] of RULE_OPTIONS) {
      const o = document.createElement("option");
      o.value = val;
      o.textContent = label;
      ruleSel.appendChild(o);
    }
    ruleSel.value = data?.rule || "must_be_numeric";
    const failSel = document.createElement("select");
    for (const [val, label] of ON_FAIL) {
      const o = document.createElement("option");
      o.value = val;
      o.textContent = label;
      failSel.appendChild(o);
    }
    failSel.value = data?.on_fail || "null";
    const noteInp = document.createElement("input");
    noteInp.type = "text";
    noteInp.placeholder = "комментарий";
    noteInp.value = data?.note || "";
    let paramsEl = paramFieldsForRule(ruleSel.value);
    ruleSel.addEventListener("change", () => {
      const np = paramFieldsForRule(ruleSel.value);
      paramsEl.replaceWith(np);
      paramsEl = np;
    });
    if (data?.params) {
      for (const inp of paramsEl.querySelectorAll("[data-param]")) {
        const k = inp.dataset.param;
        const val = data.params[k];
        if (val != null) inp.value = Array.isArray(val) ? val.join("; ") : String(val);
      }
    }
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "btn-row-remove";
    rm.textContent = "×";
    rm.addEventListener("click", () => row.remove());
    row.appendChild(cInp);
    row.appendChild(ruleSel);
    row.appendChild(failSel);
    row.appendChild(rm);
    row.appendChild(noteInp);
    row.appendChild(paramsEl);
    return row;
  }

  function collectGuidance() {
    const column_descriptions_text = (columnDescriptionsText?.value || "").trim();
    const validation_rules = [];
    validationRulesList?.querySelectorAll(".rule-row").forEach((row) => {
      const col = row.querySelector("input[list]")?.value?.trim();
      const selects = row.querySelectorAll("select");
      const rule = selects[0]?.value;
      const on_fail = selects[1]?.value || "null";
      const note = row.querySelector('input[placeholder="комментарий"]')?.value?.trim() || "";
      if (!col || !rule) return;
      const params = {};
      row.querySelectorAll("[data-param]").forEach((inp) => {
        const k = inp.dataset.param;
        let v = inp.value.trim();
        if (inp.type === "number" && v !== "") v = Number(v);
        if (k === "values" && typeof v === "string") {
          params.values = v.split(";").map((s) => s.trim()).filter(Boolean);
        } else if (v !== "") params[k] = v;
      });
      validation_rules.push({ column: col, rule, params, on_fail, note });
    });
    const freeform = (freeformInstructions?.value || "")
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    return {
      column_descriptions_text,
      validation_rules,
      freeform_instructions: freeform,
    };
  }

  window.buildUserGuidanceJSON = function () {
    const g = collectGuidance();
    if (
      !g.column_descriptions_text &&
      !g.validation_rules.length &&
      !g.freeform_instructions.length
    ) {
      return null;
    }
    return g;
  };

  function saveStorage() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(collectGuidance()));
    } catch (_) {
      /* ignore */
    }
  }

  function loadStorage() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const g = JSON.parse(raw);
      if (g.column_descriptions_text && columnDescriptionsText) {
        columnDescriptionsText.value = g.column_descriptions_text;
      } else if (g.column_descriptions?.length && columnDescriptionsText) {
        columnDescriptionsText.value = g.column_descriptions
          .map((d) => `${d.column}: ${d.description}`)
          .join("\n");
      }
      validationRulesList.innerHTML = "";
      for (const r of g.validation_rules || []) validationRulesList.appendChild(createRuleRow(r));
      if (g.freeform_instructions?.length) freeformInstructions.value = g.freeform_instructions.join("\n");
    } catch (_) {
      /* ignore */
    }
  }

  addRuleRow?.addEventListener("click", () => {
    validationRulesList?.appendChild(createRuleRow(null));
    saveStorage();
  });
  guidanceSyncCols?.addEventListener("click", () => {
    const cols = window.__previewColumns || [];
    if (!cols.length || !columnDescriptionsText) return;
    const cur = columnDescriptionsText.value.trim();
    const mentioned = new Set(
      cur.split("\n").map((line) => line.split(/[:\u2014\-–]/)[0].trim()).filter(Boolean)
    );
    const add = cols.filter((c) => !mentioned.has(c)).map((c) => `${c}: `);
    if (!add.length) return;
    columnDescriptionsText.value = cur ? `${cur}\n${add.join("\n")}` : add.join("\n");
    saveStorage();
  });

  document.getElementById("guidancePanel")?.addEventListener("input", () => saveStorage());

  loadStorage();
})();
