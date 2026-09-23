import { GraphView } from "./graph.js";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const paths = {
  network:
    "M4 5h4v4H4z M16 3h4v4h-4z M15 16h5v5h-5z M3 17h4v4H3z M8 7l8-2 M6 9l-1 8 M8 8l9 9 M7 19l8-1",
  list: "M9 5h12 M9 12h12 M9 19h12 M3 5h1 M3 12h1 M3 19h1",
  activity: "M2 12h4l3-8 5 16 3-8h5",
  layers: "m12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5",
  bookmark: "M6 3h12v18l-6-4-6 4z",
  shield: "m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6z m-4 9 3 3 5-6",
  info: "M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  download: "M12 3v12 m-5-5 5 5 5-5 M4 15v6h16v-6",
  upload: "M12 16V3 m-5 5 5-5 5 5 M4 15v6h16v-6",
  search: "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14 m5-2 6 6",
  filter: "M3 5h18 M6 12h12 M9 19h6",
  plus: "M12 5v14 M5 12h14",
  minus: "M5 12h14",
  expand: "M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6",
  close: "m6 6 12 12 M18 6 6 18",
  check: "m5 12 4 4L20 5",
  file: "M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6",
  users:
    "M15 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M5 21v-3a7 7 0 0 1 14 0v3 M18 4a3 3 0 0 1 0 6 M21 20v-4a5 5 0 0 0-2-4",
  wallet: "M3 6h17v15H3z M3 6V3h14v3 M15 11h6v6h-6z M17 14h.01",
  target: "M12 22a10 10 0 1 1 10-10 M12 17a5 5 0 1 1 5-5 M12 12l9-9 M16 3h5v5",
  arrow: "M4 12h16 m-6-6 6 6-6 6",
  clock: "M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
const icons = (root = document) =>
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
const ROLES = {
  consolidator: { label: "Консолидация", color: "#65e335" },
  transit: { label: "Транзит", color: "#79b5da" },
  distributor: { label: "Распределение", color: "#ae9ede" },
  terminal: { label: "Получатель", color: "#e2b871" },
  coordinator: { label: "Связующий узел", color: "#e59caa" },
  peripheral: { label: "Периферия / граница", color: "#8b9e91" },
};
const role = (name) => ROLES[name] || ROLES.peripheral;
const number = (value, digits = 0) =>
  Number(value || 0).toLocaleString("ru-RU", { maximumFractionDigits: digits });
const percent = (value, digits = 0) =>
  value == null ? "—" : `${number(Number(value) * 100, digits)}%`;
function amount(value, compact = false) {
  if (value == null) return "—";
  let cents;
  try {
    cents = BigInt(value);
  } catch {
    return "—";
  }
  const units = [
    [100000000000n, "млрд"],
    [100000000n, "млн"],
    [100000n, "тыс."],
  ];
  if (compact)
    for (const [unit, label] of units)
      if (cents >= unit) {
        const tenths = (cents * 10n + unit / 2n) / unit;
        return `${(tenths / 10n).toLocaleString("ru-RU")}${tenths % 10n ? "," + (tenths % 10n) : ""} ${label} ₸`;
      }
  return `${(cents / 100n).toLocaleString("ru-RU")},${String(cents % 100n).padStart(2, "0")} ₸`;
}
const date = (value, long = false) =>
  value
    ? new Date(`${String(value).slice(0, 10)}T12:00:00`).toLocaleDateString(
        "ru-RU",
        long
          ? { day: "numeric", month: "long", year: "numeric" }
          : { day: "numeric", month: "short" },
      )
    : "—";
const period = (summary) => {
  if (!summary.date_from) return "Новая выгрузка";
  if (summary.date_from.slice(0, 7) === summary.date_to.slice(0, 7))
    return new Date(`${summary.date_from}T12:00:00`)
      .toLocaleDateString("ru-RU", { month: "long", year: "numeric" })
      .replace(" г.", "");
  return `${date(summary.date_from)} — ${date(summary.date_to, true)}`;
};
const chip = (name) =>
  `<span class="role-chip" style="--role-color:${role(name).color}"><i></i>${role(name).label}</span>`;
const compareGid = (a, b) =>
  BigInt(a) < BigInt(b) ? -1 : BigInt(a) > BigInt(b) ? 1 : 0;
const state = {
  data: null,
  nodes: new Map(),
  ranked: [],
  selected: null,
  view: "network",
  role: "all",
  depth: 4,
  cluster: "all",
  query: "",
  focus: true,
  focusHops: 1,
  layout: "depth",
  review: new Set(),
  page: 0,
  details: new Map(),
  expanded: new Set(),
  modalKind: null,
  highlightIds: [],
  uploading: false,
};
let graph,
  toastTimer,
  detailRequest = 0,
  searchTimer,
  scenarioRequest = 0,
  refreshingDataset;
const initialResilienceContent = $("resilience-content").innerHTML;
const PAGE_SIZE = 30;
async function datasetChanged(expected) {
  if (expected === state.data?.metadata?.dataset_id) {
    if (!refreshingDataset) {
      refreshingDataset = loadData().finally(() => {
        refreshingDataset = null;
      });
      await refreshingDataset;
    }
  }
  throw new Error(
    "Набор данных изменился в другой вкладке. Проверьте текущую выгрузку и повторите действие.",
  );
}
async function api(path, payload) {
  const versioned =
    path.startsWith("/api/nodes/") ||
    ["/api/export", "/api/resilience"].includes(path);
  const expected = versioned ? state.data?.metadata?.dataset_id : null;
  if (expected) {
    if (payload === undefined)
      path += `?dataset_id=${encodeURIComponent(expected)}`;
    else payload = { ...payload, dataset_id: expected };
  }
  const response = await fetch(
    path,
    payload === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": state.data?.csrf_token || "",
          },
          body: JSON.stringify(payload),
        },
  );
  const data = await response
    .json()
    .catch(() => ({ error: "Сервер вернул некорректный ответ." }));
  if (response.status === 409 && expected) return datasetChanged(expected);
  if (!response.ok)
    throw new Error(
      data.error || `Не удалось выполнить запрос (${response.status}).`,
    );
  if (expected && expected !== state.data?.metadata?.dataset_id)
    throw new Error(
      "Данные обновились. Повторите действие для текущего набора.",
    );
  return data;
}
async function downloadResult(filename) {
  const expected = state.data.metadata.dataset_id;
  const response = await fetch(
    `/api/download/${filename}?dataset_id=${encodeURIComponent(expected)}`,
    { cache: "no-store" },
  );
  if (response.status === 409) return datasetChanged(expected);
  if (!response.ok) throw new Error("Не удалось получить файл.");
  const blob = await response.blob();
  if (expected !== state.data?.metadata?.dataset_id)
    throw new Error("Данные обновились. Повторите экспорт.");
  download(blob, filename, blob.type);
}
function notify(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4200);
}
function openModal(title, content, kind = "info") {
  state.modalKind = kind;
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = content;
  icons($("modal"));
  if (!$("modal").open) $("modal").showModal();
}
function closeModal() {
  $("modal").close();
  state.modalKind = null;
}
function download(content, filename, mime = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}
function reviewKey() {
  return `hackalem-review:${state.data?.metadata?.dataset_id}`;
}
function saveReview() {
  try {
    localStorage.setItem(reviewKey(), JSON.stringify([...state.review]));
  } catch {
    notify(
      "Список доступен в текущей сессии: сохранение в браузере отключено.",
    );
  }
}
function loadReview() {
  try {
    state.review = new Set(
      JSON.parse(localStorage.getItem(reviewKey()) || "[]").filter((id) =>
        state.nodes.has(id),
      ),
    );
  } catch {
    state.review = new Set();
  }
}
function toggleReview(gid) {
  if (!state.nodes.has(gid)) return;
  const fromCard = !!document.activeElement?.closest("#node-detail");
  const adding = !state.review.has(gid);
  adding ? state.review.add(gid) : state.review.delete(gid);
  saveReview();
  renderTables();
  renderDetail();
  $("review-count").textContent = state.review.size;
  const replacement = (fromCard ? $("node-detail") : document).querySelector(
    `[data-review="${gid}"]`,
  );
  replacement?.focus({ preventScroll: true });
  notify(
    adding
      ? "Клиент добавлен в список на проверку."
      : "Клиент удалён из списка проверки.",
  );
}
function nodeLink(gid) {
  return `<button type="button" class="node-link" data-node="${escapeHTML(gid)}">${escapeHTML(gid)}</button>`;
}
function resetFilters(render = true) {
  state.role = "all";
  state.depth = 4;
  state.cluster = "all";
  state.query = "";
  state.page = 0;
  $("role-filter").value = "all";
  $("depth-filter").value = "4";
  $("cluster-filter").value = "all";
  $("node-search").value = "";
  if (render) refreshFilters();
}
function filteredNodes() {
  return state.ranked.filter(
    (n) =>
      (state.role === "all" || n.role === state.role) &&
      Number(n.depth) <= state.depth &&
      (state.cluster === "all" || String(n.cluster_id) === state.cluster) &&
      (!state.query || n.gid.includes(state.query)),
  );
}
function setView(view) {
  if (
    !["network", "priorities", "patterns", "resilience", "review"].includes(
      view,
    )
  )
    return;
  state.view = view;
  document
    .querySelectorAll(".view")
    .forEach((el) => (el.hidden = el.id !== `view-${view}`));
  document.querySelectorAll(".navigation [data-view]").forEach((el) => {
    const active = el.dataset.view === view;
    el.classList.toggle("active", active);
    active
      ? el.setAttribute("aria-current", "page")
      : el.removeAttribute("aria-current");
  });
  const labels = {
    network: "Обзор сети",
    priorities: "Приоритеты проверки",
    patterns: "Временные и структурные паттерны",
    resilience: "Устойчивость сети",
    review: "Список на проверку",
  };
  const titles = {
    network: "Восстановление финансовой структуры",
    priorities: "От гипотез — к приоритетам проверки",
    patterns: "Повторения, связи и необычная активность",
    resilience: "Структура сети в сценарии удаления",
    review: "Следующий шаг расследования",
  };
  $("breadcrumb-view").textContent = labels[view];
  $("page-title").textContent = titles[view];
  $("filters").hidden = !["network", "priorities"].includes(view);
  $("page-description").textContent =
    view === "network"
      ? "Направление потоков, гипотезы ролей и обоснованный порядок проверки."
      : `${period(state.data.summary)} · ${number(state.data.summary.n_nodes)} обезличенных клиентов · выводы требуют проверки.`;
  history.replaceState(null, "", `#${view}`);
  if (view === "network") {
    requestAnimationFrame(() => renderGraph());
  }
  if (view === "patterns") renderPatterns();
  renderTables();
}
function renderMetrics() {
  const s = state.data.summary;
  $("metrics").innerHTML = [
    `<article class="metric"><div class="metric-label">Клиенты в сети ${icon("users")}</div><div class="metric-value">${number(s.n_nodes)}</div><p class="metric-note">${number(s.n_seeds)} исходных клиентов · до 4 колен</p></article>`,
    `<article class="metric"><div class="metric-label">Объём переводов ${icon("wallet")}</div><div class="metric-value" title="${amount(s.total_tiyn)}">${amount(s.total_tiyn, true).replace(" ₸", "<small>₸</small>")}</div><p class="metric-note">${number(s.n_transactions)} операций за весь период</p></article>`,
    `<article class="metric"><div class="metric-label">Связные сообщества ${icon("layers")}</div><div class="metric-value">${number(s.n_clusters)}</div><p class="metric-note">${number(s.n_edges)} направленных связей</p></article>`,
    `<button type="button" class="metric metric-link accent" data-action="consolidators"><span class="metric-label">Признаки консолидации ${icon("target")}</span><div class="metric-value">${number(s.role_counts?.consolidator)}</div><p class="metric-note">Гипотезы роли · открыть клиентов ↗</p></button>`,
  ].join("");
  $("sidebar-period").textContent = period(s);
  $("sidebar-seeds").textContent =
    `${number(s.n_seeds)} seed → ${number(s.n_nodes)} клиентов`;
  $("depth-mini").innerHTML = Object.entries(s.depth_counts || {})
    .map(
      ([depth, count]) =>
        `<i style="flex:${Number(count)}" title="Колено ${depth}: ${count}"></i>`,
    )
    .join("");
  $("top-count").textContent = state.data.top.length;
  $("review-count").textContent = state.review.size;
  $("cluster-filter").innerHTML =
    '<option value="all">Все сообщества</option>' +
    state.data.clusters
      .map(
        (c) =>
          `<option value="${Number(c.cluster_id)}">Кластер ${Number(c.cluster_id)} · ${number(c.n_nodes)}</option>`,
      )
      .join("");
}
function renderTimeline() {
  const days = state.data.timeline,
    max = Math.max(...days.map((d) => Number(d.sum_tiyn)), 1);
  $("timeline").innerHTML = days
    .map(
      (d, i) =>
        `<button type="button" class="${Number(d.sum_tiyn) >= max * 0.72 ? "peak" : ""}" style="--bar-height:${Math.max(5, (Number(d.sum_tiyn) / max) * 100)}%" data-day="${i}" aria-label="${escapeHTML(date(d.date, true))}: ${escapeHTML(amount(d.sum_tiyn))}, ${number(d.n_tx)} операций" title="${date(d.date)} · ${amount(d.sum_tiyn)}"></button>`,
    )
    .join("");
  const indices = [
    0,
    Math.floor((days.length - 1) / 3),
    Math.floor(((days.length - 1) * 2) / 3),
    days.length - 1,
  ];
  $("timeline-labels").innerHTML = indices
    .filter((i, pos) => indices.indexOf(i) === pos && i >= 0)
    .map((i) => `<span>${date(days[i]?.date)}</span>`)
    .join("");
  $("timeline-caption").textContent =
    `${period(state.data.summary)} · роли и граф рассчитаны за весь период`;
}
function renderGraph() {
  if (!state.data || !graph) return;
  graph.setData(state.data.nodes, state.data.edges, {
    selected: state.selected,
    focus: state.focus,
    focusHops: state.focusHops,
    layout: state.layout,
    depth: state.depth,
    role: state.focus ? "all" : state.role,
    cluster: state.cluster === "all" ? null : Number(state.cluster),
    query: "",
    highlightIds: state.highlightIds,
  });
  $("focus-graph").classList.toggle("active", state.focus);
  $("focus-graph").setAttribute("aria-pressed", String(state.focus));
  $("whole-graph").classList.toggle("active", !state.focus);
  $("whole-graph").setAttribute("aria-pressed", String(!state.focus));
  $("graph-columns").hidden = true;
  $("expand-neighbors").hidden = !state.focus;
  $("expand-neighbors").setAttribute(
    "aria-pressed",
    String(state.focusHops === 2),
  );
  $("expand-neighbors").textContent =
    state.focusHops === 2 ? "−1 шаг" : "+1 шаг";
  $("graph-context").textContent = state.focus
    ? `${state.focusHops === 1 ? "Прямые связи" : "Два шага связей"} · контрагенты всех ролей`
    : "Вся наблюдаемая сеть · приблизьте интересующую группу";
}
function graphStatus(status) {
  $("graph-empty").hidden = status.visibleNodes > 0;
  const extra = status.truncated
    ? ` · фокус ограничен ${number(status.visibleNodes)} узлами`
    : "";
  $("graph-caption").textContent =
    `${number(status.visibleNodes)} из ${number(state.data?.summary.n_nodes)} клиентов · ${number(status.visibleEdges)} связей${extra}. Стрелки — направление; толщина — сумма.`;
}
function graphHover(node, position) {
  const tip = $("graph-tooltip");
  if (!node) {
    tip.hidden = true;
    return;
  }
  tip.innerHTML = `<strong class="gid">${escapeHTML(node.gid)}</strong>${chip(node.role)}<span class="small">Колено ${Number(node.depth)} · кластер ${Number(node.cluster_id)} · приоритет ${number(Number(node.priority_score) * 100, 1)}/100</span><span class="small">Вход ${amount(node.in_tiyn, true)} · выход ${amount(node.out_tiyn, true)}</span>`;
  tip.hidden = false;
  tip.style.left = `${Math.max(8, Math.min(innerWidth - 320, position.clientX + 14))}px`;
  tip.style.top = `${Math.max(8, Math.min(innerHeight - 155, position.clientY + 14))}px`;
}
async function selectNode(gid, { reveal = true, navigate = true } = {}) {
  if (!state.nodes.has(gid)) return;
  state.selected = gid;
  state.highlightIds = [];
  if (reveal) resetFilters(false);
  state.focus = true;
  if (navigate) setView("network");
  renderGraph();
  renderTables();
  renderDetail();
  const request = ++detailRequest;
  if (!state.details.has(gid)) {
    try {
      const detail = await api(`/api/nodes/${encodeURIComponent(gid)}`);
      if (request !== detailRequest) return;
      state.details.set(gid, detail);
      renderDetail();
    } catch (error) {
      if (request === detailRequest) {
        notify(error.message);
      }
    }
  }
  if (navigate) {
    $("node-detail").setAttribute("tabindex", "-1");
    $("node-detail").focus({ preventScroll: true });
  }
  if (navigate)
    $("node-detail").scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "start",
    });
}
function explanation(n) {
  const incoming = Number(n.in_tiyn || 0),
    outgoing = Number(n.out_tiyn || 0);
  const ratio = incoming > 0 ? percent(outgoing / incoming, 1) : null;
  switch (n.role) {
    case "consolidator":
      return `Получает от ${number(n.in_deg)} разных плательщиков; отправляет далее ${ratio} наблюдаемого входа. Это признаки концентрации средств.`;
    case "transit":
      return `Входящий и исходящий потоки близки по объёму. Поступлениями предыдущих 1–${state.data?.metadata?.config?.temporal_window_days || 7} дней сопоставлено ${percent(n.temporal_matched_out_share, 1)} исходящих сумм.`;
    case "distributor":
      return `Отправляет средства ${number(n.out_deg)} разным получателям. Объём исходящего потока — ${amount(n.out_tiyn, true)}; источник всех поступлений неизвестен.`;
    case "terminal":
      return `Получает переводы от ${number(n.in_deg)} плательщиков; исходящих переводов в наблюдаемой выборке нет. За её пределами операции неизвестны.`;
    case "coordinator":
      return `Связывает ${number(n.n_peers)} контрагентов и несколько сообществ. Структурное положение не доказывает управление участниками.`;
    default:
      return Number(n.depth) === 4 && Number(n.out_deg) === 0
        ? "Граница четвёртого колена: исходящие переводы могут быть обрезаны выгрузкой. Конечный получатель не установлен."
        : Number(n.n_peers) === 0
          ? "Наблюдаемых внешних связей нет; данных для определения сетевой роли недостаточно."
          : `Внешних контрагентов — ${number(n.n_peers)}. Наблюдаемые признаки не дают достаточных оснований для другой роли.`;
  }
}
function rankExplanation(n) {
  const parts = [
    ["оборот", "priority_turnover_tiyn"],
    ["посредничество", "priority_betweenness"],
    ["входящая значимость", "priority_pagerank"],
    ["контрагенты", "priority_n_peers"],
    ["операции", "priority_operation_count"],
  ]
    .map(([label, key]) => ({ label, value: Number(n[key] || 0) }))
    .sort((a, b) => b.value - a.value);
  return parts[0].value > 0
    ? `Вклад в приоритет: ${parts
        .slice(0, 2)
        .map((p) => `${p.label} ${number(p.value * 100, 1)}`)
        .join("; ")} балла из 100.`
    : "Без внешней активности все компоненты приоритета равны нулю.";
}
function renderDetail() {
  const base = state.nodes.get(state.selected);
  if (!base) {
    $("node-detail").innerHTML =
      '<div class="empty-state"><h3>Выберите клиента</h3><p>Найдите gid или откройте строку списка приоритетов.</p></div>';
    return;
  }
  const detail = state.details.get(base.gid),
    n = detail?.node || base,
    ins = detail?.insights || base.insights || {},
    reviewed = state.review.has(n.gid);
  const facts = [
    ["Входящие контрагенты", `${number(n.in_deg)} плательщиков`],
    ["Исходящие контрагенты", `${number(n.out_deg)} получателей`],
    ["Получено", amount(n.in_tiyn, true)],
    ["Отправлено", amount(n.out_tiyn, true)],
  ];
  const fifo = ins.fifo?.["2d"];
  const explanations = [
    `Входящих операций: ${number(n.in_tx)}; исходящих: ${number(n.out_tx)}.`,
    n.in_tiyn && BigInt(n.in_tiyn) > 0n
      ? `Наблюдаемый выход составляет ${percent(Number(n.out_tiyn) / Number(n.in_tiyn), 1)} входа.`
      : "Наблюдаемых поступлений нет: отношение потоков не определено.",
    fifo
      ? `За 1–2 дня сопоставлено ${percent(fifo.matched_out_share, 1)} исходящей суммы; тот же день — неопределённый порядок.`
      : null,
  ].filter(Boolean);
  const contributions = [
    ["Оборот", "priority_turnover_tiyn", 0.3],
    ["Посредничество", "priority_betweenness", 0.25],
    ["Входящая значимость", "priority_pagerank", 0.2],
    ["Контрагенты", "priority_n_peers", 0.15],
    ["Операции", "priority_operation_count", 0.1],
  ];
  const gaps =
    ins.gaps ||
    (Number(n.depth) === 4
      ? [
          "Граница четвёртого колена: отсутствие исходящих переводов не подтверждает конечного получателя.",
        ]
      : n.is_seed
        ? ["Входящие потоки исходного клиента могут быть неполными."]
        : []);
  $("node-detail").innerHTML =
    `<div class="detail-overline"><span>ВЫБРАННЫЙ КЛИЕНТ</span>${icon("target")}</div>
    <div class="detail-identity"><h2 class="gid detail-gid">${escapeHTML(n.gid)}</h2><p class="detail-subtitle">${n.is_seed ? "Seed · исходный клиент" : `Колено ${Number(n.depth)}`} · кластер ${Number(n.cluster_id)}</p><div class="detail-role">${chip(n.role)}${Number(n.depth) === 4 ? '<span class="boundary-badge">Граница выгрузки</span>' : ""}</div></div>
    <div class="score-card"><div class="score-label"><span>Приоритет проверки</span>${icon("info")}</div><div class="score-number">${number(Number(n.priority_score) * 100, 1)} <small>/ 100</small></div><div class="score-line"><i style="--score:${Math.max(0, Math.min(100, Number(n.priority_score) * 100))}%"></i></div><p class="score-note">Порядок изучения, а не вероятность нарушения</p></div>
    <dl class="detail-facts">${facts.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("")}</dl>
    <div class="detail-section"><h3>Почему стоит проверить</h3><p class="reason-lead">${escapeHTML(explanation(n))}</p><ul class="fact-list">${explanations.map((t) => `<li>${icon("check")}<span>${escapeHTML(t)}</span></li>`).join("")}</ul><p class="detail-score-support">Поддержка гипотезы: ${percent(n.role_score, 1)}. Эвристическая оценка${Number(n.role_score_cap) < 1 ? `; ограничена для seed до ${percent(n.role_score_cap)}` : ""}.</p>
    <details><summary class="small">Из чего складывается приоритет</summary><div class="contributions">${contributions.map(([label, key, weight]) => `<div class="contribution"><span>${label}</span><i><b style="--score:${Math.min(100, Math.max(0, (Number(n[key] || 0) / weight) * 100))}%"></b></i><span>${number(Number(n[key] || 0) * 100, 1)}</span></div>`).join("")}</div></details></div>
    <div class="detail-actions"><button type="button" class="primary" data-review="${n.gid}" aria-pressed="${reviewed}">${icon(reviewed ? "check" : "plus")}${reviewed ? "В списке на проверку" : "Добавить в проверку"}</button><div class="inline-actions"><button type="button" data-action="operations">${icon("list")}Операции</button><button type="button" data-action="node-report">${icon("file")}Справка</button></div></div>
    <div class="detail-bottom">${gaps.length ? `<div class="data-gap">${escapeHTML(gaps.find((text) => (Number(n.depth) === 4 ? /глубин|границ/i.test(text) : n.is_seed ? /seed/i.test(text) : false)) || gaps[0])}<button type="button" class="text-button" data-action="completeness">Что запросить дальше →</button></div>` : '<button type="button" class="text-button" data-action="completeness">Полнота данных и следующий запрос →</button>'}${!detail ? '<p class="small snapshot-note">Загружаем операции и временные признаки…</p>' : ""}</div>`;
}
function table(rows, { compact = false, offset = 0 } = {}) {
  if (!rows.length)
    return '<div class="empty-state"><h3>Клиенты не найдены</h3><p>Измените роль, колено или поиск по gid.</p><button type="button" data-action="reset">Сбросить фильтры</button></div>';
  return `<div class="table-wrap" tabindex="0" role="region" aria-label="Клиенты и основания приоритета; таблицу можно прокручивать по горизонтали"><table class="node-table"><thead><tr>${compact ? "" : '<th scope="col">№</th>'}<th scope="col">Клиент · gid</th><th scope="col">Роль · гипотеза</th><th scope="col">Приоритет</th><th scope="col">Основание</th><th scope="col"><span class="sr-only">Добавить в проверку</span></th></tr></thead><tbody>${rows.map((n, i) => `<tr class="${n.gid === state.selected ? "selected" : ""}">${compact ? "" : `<td class="rank-cell">${i + offset + 1}</td>`}<td>${nodeLink(n.gid)}${n.is_seed ? '<span class="small" style="display:block;font-size:9px">Исходный клиент</span>' : ""}</td><td>${chip(n.role)}</td><td class="priority-cell"><span class="priority-track"><i style="--score:${Number(n.priority_score) * 100}%"></i></span>${number(Number(n.priority_score) * 100, 1)}</td><td class="reason-cell">${escapeHTML(explanation(n))}<p class="rank-reason">${escapeHTML(rankExplanation(n))}</p></td><td><button type="button" class="review-toggle" data-review="${n.gid}" aria-pressed="${state.review.has(n.gid)}" aria-label="${state.review.has(n.gid) ? "Убрать" : "Добавить"} ${n.gid} ${state.review.has(n.gid) ? "из списка" : "в список"} проверки">${icon(state.review.has(n.gid) ? "check" : "plus")}</button></td></tr>`).join("")}</tbody></table></div>`;
}
function renderTables() {
  if (!state.data) return;
  const matches = filteredNodes();
  $("filter-summary").textContent =
    `${number(matches.length)} из ${number(state.ranked.length)} клиентов`;
  state.previewConsolidators =
    state.role === "all" &&
    state.depth === 4 &&
    state.cluster === "all" &&
    !state.query;
  const preview = state.previewConsolidators
    ? matches.filter((n) => n.role === "consolidator")
    : matches;
  $("queue-title").textContent = state.previewConsolidators
    ? "Консолидация: приоритет проверки"
    : "Кого проверить первым";
  $("queue-count").textContent = preview.length;
  $("priority-preview").innerHTML =
    table(preview.slice(0, 5), { compact: true }) +
    `<div class="table-footer"><span>Показаны ${Math.min(5, preview.length)} из ${number(preview.length)} · приоритет по убыванию</span><button type="button" class="text-button" data-action="preview-priorities">Открыть список →</button></div>`;
  const pages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages - 1);
  $("priority-table").innerHTML =
    table(matches.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE), {
      offset: state.page * PAGE_SIZE,
    }) +
    `<div class="table-footer"><span>${number(matches.length)} клиентов по выбранным условиям</span><div class="pagination"><button type="button" data-page="-1" ${state.page === 0 ? "disabled" : ""} aria-label="Предыдущая страница">←</button><span>${state.page + 1} / ${pages}</span><button type="button" data-page="1" ${state.page >= pages - 1 ? "disabled" : ""} aria-label="Следующая страница">→</button></div></div>`;
  const reviewed = state.ranked.filter((n) => state.review.has(n.gid));
  $("review-content").innerHTML = reviewed.length
    ? `<div class="panel">${table(reviewed)}<div class="table-footer"><span>${number(reviewed.length)} клиентов · основания и запросы войдут в справку</span></div></div>`
    : `<div class="empty-state">${icon("bookmark")}<h3>Список проверки пока пуст</h3><p>Откройте карточку клиента или нажмите «+» в таблице. Добавленные клиенты появятся здесь вместе с основаниями.</p><button type="button" class="primary" data-view="priorities">Перейти к приоритетам</button></div>`;
  $("review-csv").disabled = !reviewed.length;
  $("review-report").disabled = !reviewed.length;
  $("review-count").textContent = reviewed.length;
}
function refreshFilters() {
  state.highlightIds = [];
  renderTables();
  renderGraph();
}
function selectConsolidators() {
  resetFilters(false);
  state.role = "consolidator";
  $("role-filter").value = "consolidator";
  setView("priorities");
  renderTables();
}
function renderPatterns() {
  const data = state.data.insights || {},
    s = data.summary || {},
    patterns = data.patterns || {},
    scope = data.scope || {},
    cfg = data.methodology?.config || {};
  const list = (key, rows, render) =>
    `${
      rows
        .slice(0, state.expanded.has(key) ? rows.length : 5)
        .map(render)
        .join("") ||
      '<p class="small">Таких наблюдений в этой выгрузке нет.</p>'
    }${rows.length > 5 ? `<button type="button" class="text-button" data-expand="${key}">${state.expanded.has(key) ? "Свернуть" : `Показать все ${number(rows.length)}`} →</button>` : ""}`;
  const sync = state.ranked.filter(
    (n) =>
      (n.insights?.activity?.synchronized_days?.length ||
        n.insights?.activity?.synchronized_days_count ||
        0) > 0,
  );
  const bursts = state.ranked.filter(
    (n) =>
      (n.insights?.activity?.burst_days?.length ||
        n.insights?.activity?.burst_days_count ||
        0) > 0,
  );
  const anomalies = state.ranked.filter((n) => n.insights?.anomaly?.flagged);
  const fast = state.ranked.filter(
    (n) =>
      Number(n.insights?.fifo?.["2d"]?.matched_out_share) >= 0.5 &&
      Number(n.out_deg) > 0,
  );
  const route = (ids, cycle = false) =>
    `<div class="route">${ids.map(nodeLink).join('<span aria-hidden="true">→</span>')}${cycle ? '<span aria-label="Возврат к первому узлу">↩</span>' : ""}</div>`;
  $("patterns-content").innerHTML =
    `<div class="pattern-summary"><div class="panel"><h3>Повторяющиеся маршруты</h3><strong>${number(patterns.chains?.length)}</strong><p>Наблюдаемые цепочки с лагом 1–2 дня</p></div><div class="panel"><h3>Замкнутые связи</h3><strong>${number(patterns.cycles?.length)}</strong><p>Направленные циклы из 2–3 клиентов</p></div><div class="panel"><h3>Необычный профиль колена</h3><strong>${number(s.anomaly_nodes)}</strong><p>Сравнение с клиентами той же глубины</p></div></div>
    <div class="pattern-sections">
      <section class="panel pattern-panel"><h3>Быстрый транзит · 1–2 дня</h3><p class="small">Не менее 50% наблюдаемой исходящей суммы сопоставлено с доступными поступлениями прошлых двух дней. Это дополнительный сигнал; основная роль использует окно 1–7 дней.</p>${list("fast", fast, (n) => `<div class="pattern-row"><div>${nodeLink(n.gid)}<p>${chip(n.role)} · выход ${amount(n.out_tiyn, true)}</p></div><strong>${percent(n.insights.fifo["2d"].matched_out_share, 1)}</strong></div>`)}</section>
      <section class="panel pattern-panel"><h3>Синхронные поступления</h3><p class="small">Не менее ${number(cfg.synchronized_min_payers || 3)} разных внешних плательщиков за один календарный день. Порядок внутри дня неизвестен.</p>${list("sync", sync, (n) => `<div class="pattern-row"><div>${nodeLink(n.gid)}<p>${(n.insights.activity.synchronized_days || []).map((d) => date(d)).join(" · ") || `${number(n.insights.activity.synchronized_days_count)} дней с признаком`}</p></div>${chip(n.role)}</div>`)}</section>
      <section class="panel pattern-panel"><h3>Всплески активности</h3><p class="small">От ${number(cfg.burst_min_transactions || 3)} внешних операций и оборот дня ≥ ${number(cfg.burst_multiple || 3)} медиан активных дней клиента. Нужно минимум ${number(cfg.burst_min_active_days || 3)} активных дня.</p>${list("bursts", bursts, (n) => `<div class="pattern-row"><div>${nodeLink(n.gid)}<p>${(n.insights.activity.burst_days || []).map((d) => date(d)).join(" · ") || `${number(n.insights.activity.burst_days_count)} дней с признаком`}</p></div>${chip(n.role)}</div>`)}</section>
      <section class="panel pattern-panel"><h3>Аномалии относительно своего колена</h3><p class="small">Оборот, контрагенты или число операций не ниже ${number((cfg.anomaly_percentile || 0.98) * 100)}-го процентиля среди положительных значений на той же глубине. Это относительный ранг, не вероятность нарушения.</p>${list("anomalies", anomalies, (n) => `<div class="pattern-row"><div>${nodeLink(n.gid)}<p>Колено ${Number(n.depth)} · сравнение с ${number(n.insights.anomaly.cohort_size)} клиентами</p></div><strong>${percent(n.insights.anomaly.score, 1)}</strong></div>`)}</section>
      <section class="panel pattern-panel wide"><h3>Устойчивые цепочки A → B → C</h3><p class="small">Как минимум два отдельных дневных эпизода: поступление на B и перевод B → C через 1–2 дня. Одни и те же события не используются повторно внутри маршрута. Общий источник денег не установлен.</p>${list("chains", patterns.chains || [], (p) => `<div class="pattern-row"><div>${route(p.nodes)}<p>${number(p.event_count)} повторений · ${p.events?.[0] ? `${date(p.events[0].in_date)} → ${date(p.events[0].out_date)}` : ""}</p></div><button type="button" class="text-button" data-pattern="${p.id}" data-kind="chains">Эпизоды →</button></div>`)}</section>
      <section class="panel pattern-panel wide"><h3>Циклы и возможные возвратные связи</h3><p class="small">Путь возвращается к исходному клиенту в наблюдаемом графе. Даты могут подтвердить последовательность рёбер, но не возврат тех же средств.</p>${list("cycles", patterns.cycles || [], (p) => `<div class="pattern-row"><div>${route(p.nodes, true)}<p>${amount(p.edge_total_tiyn, true)} на рёбрах цикла · ${p.chronology_example ? "есть пример последовательности с лагом 1–2 дня" : "только структурная связь, последовательность не подтверждена"}</p></div><button type="button" class="text-button" data-pattern="${p.id}" data-kind="cycles">Подробнее →</button></div>`)}</section>
      <section class="panel pattern-panel wide"><h3>Повторяющиеся небольшие суммы</h3><p class="small">От ${number(cfg.equal_amount_min_transactions || 3)} одинаковых переводов на одной паре за день, каждый ниже ${amount(cfg.equal_amount_threshold_tiyn || "1000000", true)}. Признак для проверки возможного дробления; обход ограничений не установлен.</p>${list("amounts", patterns.amount_groups || [], (p) => `<div class="pattern-row"><div>${route(p.nodes)}<p>${date(p.date, true)} · ${number(p.n_tx)} × ${amount(p.amount_tiyn)} = ${amount(p.total_tiyn)}</p></div></div>`)}</section>
    </div><p class="scope-note">${Object.values(scope).some((v) => v.search_truncated) ? "Поиск ограничен вычислительным лимитом: показаны найденные примеры, это не полный перечень." : "Поиск маршрутов с лагом 1–2 дня и циклов длины 2–3 завершён в пределах заданных правил."} ${Object.values(scope).some((v) => v.display_truncated) ? "Часть найденных примеров не включена в выдачу из-за лимита отображения." : ""} Паттерны не изменяют роль и основной приоритет клиента автоматически.</p>`;
}
function showPattern(kind, id) {
  const p = state.data.insights.patterns[kind]?.find((item) => item.id === id);
  if (!p) return;
  let content = `<div class="node-pills">${p.nodes.map(nodeLink).join("")}</div>`;
  if (kind === "chains")
    content += `<p class="modal-copy">${number(p.event_count)} отдельных эпизодов с лагом 1–2 дня. Суммы на двух рёбрах могут различаться; принадлежность денег одному источнику не установлена.</p><div class="table-wrap"><table><thead><tr><th>Поступление A → B</th><th>Сумма</th><th>Перевод B → C</th><th>Сумма</th></tr></thead><tbody>${p.events.map((e) => `<tr><td>${date(e.in_date)}</td><td>${amount(e.in_tiyn)}</td><td>${date(e.out_date)}</td><td>${amount(e.out_tiyn)}</td></tr>`).join("")}</tbody></table></div>${p.examples_truncated ? '<p class="snapshot-note">Показана часть эпизодов.</p>' : ""}`;
  else
    content += `<p class="modal-copy">Направленный цикл из ${number(p.length)} клиентов. Сумма по его рёбрам: <strong>${amount(p.edge_total_tiyn)}</strong>. Это оборот связей, а не сумма вернувшихся денег.</p>${p.chronology_example ? `<h3>Пример хронологической последовательности</h3><div class="table-wrap"><table><thead><tr><th>Направление</th><th>Дата</th><th>Сумма за день</th></tr></thead><tbody>${p.chronology_example.dates.map((d, i) => `<tr><td class="gid">${escapeHTML(p.chronology_example.nodes[i])} → ${escapeHTML(p.chronology_example.nodes[i + 1])}</td><td>${date(d)}</td><td>${amount(p.chronology_example.amounts_tiyn[i])}</td></tr>`).join("")}</tbody></table></div>` : '<div class="data-gap">Хронологическая последовательность с лагом 1–2 дня не найдена. Нужны точное время операций и более полный период.</div>'}`;
  openModal(
    kind === "chains"
      ? "Повторяющийся маршрут"
      : "Замкнутая структура переводов",
    content,
    "pattern",
  );
}
async function runResilience() {
  const choice = $("resilience-number").value;
  if (choice === "review" && !state.review.size) {
    notify("Сначала добавьте клиентов в список проверки.");
    return;
  }
  const request = ++scenarioRequest;
  $("run-resilience").disabled = true;
  $("resilience-content").innerHTML =
    '<div class="state-panel" role="status"><span class="spinner"></span><p>Пересчитываем связность оставшегося графа…</p></div>';
  try {
    const r = await api(
      "/api/resilience",
      choice === "review"
        ? { gids: [...state.review] }
        : { top_n: Number(choice) },
    );
    if (request !== scenarioRequest) return;
    const a = r.before,
      b = r.after;
    const card = (label, before, after, note) =>
      `<article class="panel scenario-card"><h3>${label}</h3><div class="comparison"><span class="before">${number(before)}</span><span class="arrow">→</span><span class="after">${number(after)}</span></div><div class="comparison-bars"><span style="--bar-width:${Math.max(3, (before / Math.max(before, after, 1)) * 100)}%"></span><span class="new-bar" style="--bar-width:${Math.max(3, (after / Math.max(before, after, 1)) * 100)}%"></span></div><p>${note}</p></article>`;
    $("resilience-content").innerHTML =
      `<div class="scenario-grid">${card("Компоненты связности", a.weak_components, b.weak_components, "Группы, между которыми нет пути без учёта направления.")}${card("Крупнейшая компонента", a.largest_component_nodes, b.largest_component_nodes, "Количество клиентов в самой крупной связной группе.")}${card("Изолированные клиенты", a.isolated_nodes, b.isolated_nodes, `Исходные ${number(a.isolated_nodes)} изолятов учитываются в сравнении.`)}</div><section class="panel removed-nodes"><h3>Удалены из модели · ${number(r.removed_gids.length)}</h3><div class="node-pills">${r.removed_gids.map(nodeLink).join("")}</div><div class="detail-section"><h3>Что осталось в наблюдаемой сети</h3><p class="modal-copy"><strong>${percent(r.retained_edge_amount_share, 1)}</strong> объёма внешних связей: ${amount(r.retained_edge_amount_tiyn)}. У ${number(r.newly_unreachable_surviving_count)} оставшихся клиентов пропал направленный путь от сохранившихся seed.</p></div></section><p class="scope-note">Это удаление узлов и существующих рёбер в модели. Сумма удалённых связей не означает предотвращённый ущерб. Перенаправление переводов, новые участники и изменение поведения не моделируются; роли не пересчитываются.</p>`;
  } catch (error) {
    if (request !== scenarioRequest) return;
    $("resilience-content").innerHTML =
      `<div class="state-panel error" role="alert"><div><h2>Не удалось рассчитать сценарий</h2><p>${escapeHTML(error.message)}</p></div></div>`;
  } finally {
    if (request === scenarioRequest) $("run-resilience").disabled = false;
  }
}
async function ensureDetail() {
  const gid = state.selected;
  if (!gid) throw new Error("Выберите клиента.");
  if (!state.details.has(gid)) {
    const detail = await api(`/api/nodes/${gid}`);
    if (state.selected !== gid)
      throw new Error("Выбран другой клиент. Откройте его карточку ещё раз.");
    state.details.set(gid, detail);
  }
  return state.details.get(gid);
}
async function showOperations(page = 0) {
  try {
    const detail = await ensureDetail(),
      rows = detail.transactions || [],
      scope = detail.transactions_scope || {},
      size = 15,
      pages = Math.max(1, Math.ceil(rows.length / size));
    const html = `<div class="operations-summary"><span class="gid">${escapeHTML(state.selected)}</span><span>${number(scope.total ?? rows.length)} операций</span><span>Сортировка по дате</span></div><p class="snapshot-note">Время внутри дня неизвестно; порядок строк в пределах дня не является порядком исполнения.${scope.truncated ? ` Показаны ${number(scope.shown)} из ${number(scope.total)} операций.` : ""}</p><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Отправитель</th><th>Получатель</th><th>Сумма</th></tr></thead><tbody>${
      rows
        .slice(page * size, (page + 1) * size)
        .map(
          (t) =>
            `<tr><td>${date(t.date)}</td><td class="gid">${escapeHTML(t.src)}</td><td class="gid">${escapeHTML(t.dst)}</td><td class="mono">${amount(t.sum_tiyn)}${t.is_self_transfer ? ' <span class="small">Самоперевод</span>' : ""}</td></tr>`,
        )
        .join("") || '<tr><td colspan="4">Наблюдаемых операций нет.</td></tr>'
    }</tbody></table></div><div class="modal-actions"><span class="small">Все идентификаторы сохранены точно.</span><div class="pagination"><button type="button" data-ops-page="${page - 1}" ${page === 0 ? "disabled" : ""}>←</button><span>${page + 1} / ${pages}</span><button type="button" data-ops-page="${page + 1}" ${page >= pages - 1 ? "disabled" : ""}>→</button></div></div>`;
    openModal("Операции клиента", html, "operations");
  } catch (error) {
    notify(error.message);
  }
}
async function showCompleteness() {
  try {
    const detail = await ensureDetail(),
      ins = detail.insights || {};
    openModal(
      "Полнота данных и следующий запрос",
      `<p class="gid modal-copy">${escapeHTML(state.selected)} · ${chip(detail.node.role)}</p><div class="modal-section"><h3>Что ограничивает вывод</h3><ul class="fact-list">${(ins.gaps || []).map((t) => `<li>${icon("info")}<span>${escapeHTML(t)}</span></li>`).join("")}</ul></div><div class="modal-section"><h3>Что запросить для проверки гипотезы</h3><ul class="fact-list">${(ins.suggested_requests || []).map((t) => `<li>${icon("arrow")}<span>${escapeHTML(t)}</span></li>`).join("")}</ul></div><p class="scope-note">Справка оперирует только обезличенным gid. Решение об углублённой проверке принимает аналитик.</p>`,
      "completeness",
    );
  } catch (error) {
    notify(error.message);
  }
}
async function exportSelection(format = "markdown", gids = [...state.review]) {
  if (!gids.length) {
    notify("Добавьте хотя бы одного клиента в список проверки.");
    return;
  }
  try {
    const data = await api("/api/export", { gids, format });
    download(data.content, data.filename, data.mime_type);
    notify("Справка сформирована по текущим данным.");
  } catch (error) {
    notify(error.message);
  }
}
function showExports() {
  const files = [
    [
      "nodes_roles.csv",
      "Клиенты и гипотезы ролей",
      "Все признаки, оценки и основания",
    ],
    ["clusters.csv", "Связные сообщества", "Размеры, обороты и гипотезы"],
    ["top_nodes.csv", "Топ-20 приоритетов", "Порядок проверки с обоснованиями"],
    [
      "graph.json",
      "Граф и карточки",
      "Строковые идентификаторы и точные данные",
    ],
    ["manifest.json", "Параметры расчёта", "Конфигурация и контрольные суммы"],
  ];
  openModal(
    "Экспорт результатов",
    `<p class="modal-copy">Комплект рассчитан за ${escapeHTML(period(state.data.summary))}. Фильтры экрана не меняют содержимое полного экспорта. При открытии CSV в табличном редакторе импортируйте gid как текст.</p><div class="export-grid">${files.map(([file, title, desc]) => `<div class="export-option"><div><strong>${title}</strong><p>${desc}</p></div><button type="button" data-download="${file}">${icon("download")}${file.endsWith(".csv") ? "CSV" : "JSON"}</button></div>`).join("")}</div><div class="modal-actions"><span class="small">На проверку: ${number(state.review.size)} клиентов. Их карточки включают факты и рекомендации по запросу данных.</span><button type="button" class="primary" data-action="review-report" ${!state.review.size ? "disabled" : ""}>Справка по списку</button></div>`,
    "export",
  );
}
function showMethod() {
  const config = state.data?.metadata?.config,
    window = config?.temporal_window_days || 7;
  openModal(
    "Как читать результаты",
    `<p class="modal-copy">Роли описывают наблюдаемую структуру переводов. Они являются <strong>гипотезами для проверки</strong>. Поддержка роли и приоритет изучения — разные оценки.</p><div class="modal-section"><h3>Роли</h3><ul class="fact-list"><li>${icon("arrow")}<span><strong>Консолидация:</strong> несколько плательщиков, небольшая доля исходящего потока.</span></li><li>${icon("arrow")}<span><strong>Транзит:</strong> сбалансированные суммы и покрытие исходящих переводов поступлениями за 1–${window} дней. Каждое поступление расходуется один раз.</span></li><li>${icon("arrow")}<span><strong>Распределение:</strong> поток расходится к нескольким получателям.</span></li><li>${icon("arrow")}<span><strong>Получатель:</strong> наблюдаются поступления без исходящих операций до границы обхода.</span></li><li>${icon("arrow")}<span><strong>Связующий узел:</strong> соединяет несколько сообществ; это не утверждение об управлении ими.</span></li><li>${icon("arrow")}<span><strong>Периферия / граница:</strong> недостаточно оснований для иной роли, мало внешних связей либо обрыв наблюдения.</span></li></ul></div><div class="modal-section"><h3>Приоритет</h3><p class="modal-copy">Оборот — 30%, посредничество — 25%, входящая значимость (PageRank) — 20%, контрагенты — 15%, число операций — 10%. Используются относительные ранги среди положительных значений. У клиента без внешних связей приоритет равен нулю. Это порядок изучения, а не вероятность нарушения.</p></div><div class="data-gap">Колено 4 может обрывать исходящие связи. Входящие потоки seed неполны. Переводы одного дня не устанавливают последовательность движения денег. Начальные остатки, другие банки и периоды неизвестны.</div><div class="modal-section"><h3>Обработка данных</h3><p class="modal-copy">Интерфейс и расчёт работают на этом компьютере. Используются gid, суммы, даты и связи. Загрузка не требует ФИО, ИИН или номеров счетов. Новый набор не перезаписывает исходные Parquet. Выбранные для проверки gid сохраняются только в этом браузере.</p></div>`,
    "method",
  );
}
let uploadFiles = new Map();
function showUpload() {
  if (state.uploading) {
    notify("Анализ уже выполняется. Дождитесь результата.");
    return;
  }
  uploadFiles = new Map();
  openModal(
    "Загрузить исходные данные",
    `<p class="modal-copy">Выберите три Parquet одной выгрузки. Набор проверяется до расчёта; при ошибке текущий анализ остаётся доступным. Поддерживаются только обезличенные идентификаторы, суммы, даты и технические поля.</p><div class="upload-zone" id="upload-zone">${icon("upload")}<strong>Перетащите три файла сюда</strong><p>nodes.parquet · edges.parquet · transactions.parquet</p><label>Выбрать файлы<input id="upload-files" type="file" accept=".parquet" multiple aria-label="Выбрать три Parquet"></label></div><div class="file-grid" id="file-grid"></div><div class="modal-section"><h3><label for="seed-input">Исходные клиенты · seed</label></h3><p class="small" style="margin-bottom:9px">Необязательно: вставьте список gid для сверки с is_seed в nodes.parquet. Один gid на строку или через запятую. Ожидаемое число берётся из файла.</p><textarea id="seed-input" rows="3" placeholder="100000003684369100&#10;100000003684369101" spellcheck="false"></textarea></div><div id="upload-error" class="upload-error" role="alert" hidden></div><div id="upload-progress" class="upload-progress" role="status" hidden></div><div class="modal-actions"><span class="small">До 64 МиБ суммарно. Полные gid сохраняются без округления. Дополнительные поля персональных данных не принимаются.</span><button type="button" class="primary" id="start-analysis" disabled>${icon("network")}Проверить и построить граф</button></div>`,
    "upload",
  );
  renderFileSlots();
  $("upload-files").addEventListener("change", (event) =>
    acceptFiles(event.target.files),
  );
  const zone = $("upload-zone");
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragover");
    acceptFiles(event.dataTransfer.files);
  });
  $("start-analysis").addEventListener("click", startAnalysis);
}
function renderFileSlots() {
  const names = ["nodes.parquet", "edges.parquet", "transactions.parquet"];
  $("file-grid").innerHTML = names
    .map(
      (name) =>
        `<div class="file-slot ${uploadFiles.has(name) ? "ready" : ""}"><strong>${name}</strong><span>${uploadFiles.has(name) ? `${number(uploadFiles.get(name).size / 1024, 1)} КБ · выбран` : "Ожидается файл"}</span></div>`,
    )
    .join("");
  $("start-analysis").disabled = !names.every((name) => uploadFiles.has(name));
}
function uploadError(message) {
  if ($("upload-error")) {
    $("upload-error").textContent = message;
    $("upload-error").hidden = false;
  } else notify(message);
}
function acceptFiles(files) {
  const allowed = ["nodes.parquet", "edges.parquet", "transactions.parquet"];
  const wrong = [];
  for (const file of files) {
    if (!allowed.includes(file.name)) {
      wrong.push(file.name);
      continue;
    }
    uploadFiles.set(file.name, file);
  }
  if (wrong.length)
    uploadError(
      `Неизвестные имена файлов: ${wrong.join(", ")}. Нужны nodes.parquet, edges.parquet и transactions.parquet.`,
    );
  else $("upload-error").hidden = true;
  renderFileSlots();
}
function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () =>
      reject(new Error(`Не удалось прочитать ${file.name}.`));
    reader.readAsDataURL(file);
  });
}
async function startAnalysis() {
  if (state.uploading) return;
  const total = [...uploadFiles.values()].reduce((sum, f) => sum + f.size, 0);
  if (total > 64 * 1024 * 1024) {
    uploadError("Суммарный размер файлов превышает 64 МиБ.");
    return;
  }
  const seeds = $("seed-input")
    .value.trim()
    .split(/[\s,;]+/)
    .filter(Boolean);
  if (seeds.some((gid) => !/^(0|[1-9]\d{0,18})$/.test(gid))) {
    uploadError(
      "Список seed должен содержать только точные целые gid, без ФИО, ИИН и других полей.",
    );
    return;
  }
  state.uploading = true;
  $("start-analysis").disabled = true;
  $("upload-error").hidden = true;
  $("upload-progress").hidden = false;
  const progress = (message) => {
    if ($("upload-progress"))
      $("upload-progress").innerHTML =
        `<span class="spinner"></span><div>${escapeHTML(message)}<small>Текущий результат сохраняется до успешного завершения.</small></div>`;
  };
  try {
    progress("Читаем выбранные файлы…");
    const entries = await Promise.all(
      [...uploadFiles].map(async ([name, file]) => [
        name,
        await readBase64(file),
      ]),
    );
    const payload = { files: Object.fromEntries(entries) };
    if (seeds.length) payload.seed_gids = seeds;
    const job = await api("/api/analyze", payload);
    const phases = {
      reading_upload: "Читаем структуру Parquet…",
      validating_inputs: "Проверяем суммы, глубины и исходных клиентов…",
      analyzing_graph: "Рассчитываем граф, роли и приоритеты…",
      validating_outputs: "Проверяем результат и собираем карточки…",
    };
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 600));
      const status = await api(`/api/jobs/${job.job_id}`);
      if (status.status === "failed")
        throw new Error(status.error || "Не удалось обработать выгрузку.");
      if (status.status === "complete") break;
      progress(phases[status.phase] || "Выполняем анализ…");
    }
    const refreshed = await loadData();
    if (!refreshed)
      throw new Error(
        "Расчёт завершён, но не удалось получить результат. Повторите подключение.",
      );
    if (state.modalKind === "upload") closeModal();
    notify("Выгрузка проверена. Граф и приоритеты обновлены.");
  } catch (error) {
    uploadError(error.message);
  } finally {
    state.uploading = false;
    if ($("start-analysis")) $("start-analysis").disabled = false;
    if ($("upload-progress")) $("upload-progress").hidden = true;
  }
}
async function loadData() {
  const initialView = location.hash.slice(1) || "network";
  try {
    const data = await api("/api/bootstrap");
    state.data = data;
    detailRequest++;
    scenarioRequest++;
    $("resilience-content").innerHTML = initialResilienceContent;
    icons($("resilience-content"));
    $("run-resilience").disabled = false;
    if ($("modal").open && state.modalKind !== "upload") closeModal();
    if (data.status === "empty" || !data.nodes?.length) {
      $("application").hidden = true;
      $("connection-state").hidden = false;
      $("connection-state").className = "state-panel";
      $("connection-state").innerHTML =
        `${icon("upload")}<div><h2>Начните с выгрузки по делу</h2><p>${escapeHTML(data.load_error || "Загрузите nodes.parquet, edges.parquet и transactions.parquet, чтобы построить сеть и получить приоритеты.")}</p><button type="button" class="primary" style="margin-top:15px" data-action="upload">Загрузить данные</button></div>`;
      $("export-button").disabled = true;
      return;
    }
    state.nodes = new Map(data.nodes.map((n) => [n.gid, n]));
    state.ranked = [...data.nodes].sort(
      (a, b) =>
        Number(b.priority_score) - Number(a.priority_score) ||
        compareGid(a.gid, b.gid),
    );
    state.details.clear();
    state.expanded.clear();
    loadReview();
    state.selected = (
      state.ranked.find((n) => n.role === "consolidator") || state.ranked[0]
    ).gid;
    state.highlightIds = [];
    resetFilters(false);
    $("connection-state").hidden = true;
    $("application").hidden = false;
    $("export-button").disabled = false;
    renderMetrics();
    renderTimeline();
    renderTables();
    renderDetail();
    setView(initialView);
    await selectNode(state.selected, { navigate: false, reveal: false });
    return true;
  } catch (error) {
    $("connection-state").hidden = false;
    $("connection-state").className = "state-panel error";
    $("connection-state").innerHTML =
      `${icon("info")}<div><h2>Не удалось подключиться к расчёту</h2><p>${escapeHTML(error.message)} Проверьте, что локальный сервер запущен.</p><button type="button" data-action="retry" style="margin-top:14px">Повторить подключение</button></div>`;
    return false;
  }
}
icons();
document.querySelectorAll(".navigation [data-view]").forEach((el) =>
  el.setAttribute(
    "aria-label",
    {
      network: "Обзор сети",
      priorities: "Приоритеты",
      patterns: "Паттерны",
      resilience: "Устойчивость",
      review: "На проверку",
    }[el.dataset.view],
  ),
);
$("graph-legend").innerHTML =
  Object.values(ROLES)
    .map(
      (r) => `<span><i style="--role-color:${r.color}"></i>${r.label}</span>`,
    )
    .join("") + "<span>◌ seed</span>";
graph = new GraphView($("network-canvas"), {
  onSelect: (gid) => selectNode(gid, { reveal: false, navigate: false }),
  onHover: graphHover,
  onStatus: graphStatus,
});
$("upload-button").addEventListener("click", showUpload);
$("export-button").addEventListener("click", () =>
  state.data?.nodes?.length
    ? showExports()
    : notify("Сначала загрузите данные."),
);
$("method-button").addEventListener("click", showMethod);
$("close-modal").addEventListener("click", closeModal);
$("modal").addEventListener("close", () => (state.modalKind = null));
$("expand-neighbors").addEventListener("click", () => {
  state.focusHops = state.focusHops === 1 ? 2 : 1;
  renderGraph();
  graph.fit();
});
$("focus-graph").addEventListener("click", () => {
  state.focus = true;
  renderGraph();
  graph.fit();
});
$("whole-graph").addEventListener("click", () => {
  state.focus = false;
  renderGraph();
  graph.fit();
});
$("layout-select").addEventListener("change", (event) => {
  state.layout = event.target.value;
  renderGraph();
  graph.fit();
});
$("zoom-in").addEventListener("click", () => graph.zoomBy(1.3));
$("zoom-out").addEventListener("click", () => graph.zoomBy(1 / 1.3));
$("fit-graph").addEventListener("click", () => graph.fit());
$("role-filter").addEventListener("change", (event) => {
  state.role = event.target.value;
  state.page = 0;
  refreshFilters();
});
$("depth-filter").addEventListener("change", (event) => {
  state.depth = Number(event.target.value);
  state.page = 0;
  refreshFilters();
});
$("cluster-filter").addEventListener("change", (event) => {
  state.cluster = event.target.value;
  state.page = 0;
  state.focus = false;
  refreshFilters();
});
$("reset-filters").addEventListener("click", () => resetFilters());
$("node-search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  state.query = event.target.value.trim();
  state.page = 0;
  searchTimer = setTimeout(() => {
    renderTables();
    if (state.nodes.has(state.query)) {
      const query = state.query;
      resetFilters(false);
      state.query = query;
      $("node-search").value = query;
      selectNode(query, { reveal: false, navigate: false });
    }
  }, 120);
});
$("node-search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const found = filteredNodes()[0];
    if (found) selectNode(found.gid);
    else notify("Такой gid не найден в текущей выгрузке.");
  }
});
$("focus-consolidators").addEventListener("click", selectConsolidators);
$("run-resilience").addEventListener("click", runResilience);
$("review-csv").addEventListener("click", () => exportSelection("csv"));
$("review-report").addEventListener("click", () => exportSelection());
document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target || target.disabled) return;
  if (target.dataset.view && state.data?.nodes?.length) {
    setView(target.dataset.view);
    return;
  }
  if (target.dataset.node) {
    if ($("modal").open) closeModal();
    selectNode(target.dataset.node);
    return;
  }
  if (target.dataset.review) {
    toggleReview(target.dataset.review);
    return;
  }
  if (target.dataset.page) {
    state.page += Number(target.dataset.page);
    renderTables();
    return;
  }
  if (target.dataset.opsPage !== undefined) {
    showOperations(Number(target.dataset.opsPage));
    return;
  }
  if (target.dataset.day !== undefined) {
    const d = state.data.timeline[Number(target.dataset.day)];
    $("timeline-caption").textContent =
      `${date(d.date, true)} · ${amount(d.sum_tiyn)} · ${number(d.n_tx)} операций. Роли за весь период.`;
    $("timeline")
      .querySelectorAll("button")
      .forEach((b) => b.classList.toggle("selected", b === target));
    return;
  }
  if (target.dataset.expand) {
    state.expanded.has(target.dataset.expand)
      ? state.expanded.delete(target.dataset.expand)
      : state.expanded.add(target.dataset.expand);
    renderPatterns();
    return;
  }
  if (target.dataset.pattern) {
    showPattern(target.dataset.kind, target.dataset.pattern);
    return;
  }
  if (target.dataset.download) {
    try {
      await downloadResult(target.dataset.download);
    } catch (error) {
      notify(error.message);
    }
    return;
  }
  const action = target.dataset.action;
  if (action === "preview-priorities") {
    state.previewConsolidators ? selectConsolidators() : setView("priorities");
  }
  if (action === "reset") resetFilters();
  if (action === "consolidators") selectConsolidators();
  if (action === "method") showMethod();
  if (action === "operations") showOperations();
  if (action === "completeness") showCompleteness();
  if (action === "node-report") exportSelection("markdown", [state.selected]);
  if (action === "review-report") exportSelection();
  if (action === "upload") showUpload();
  if (action === "retry") loadData();
});
document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  if (state.data?.nodes?.length) setView("network");
});
window.addEventListener("pagehide", () => graph.destroy(), { once: true });
loadData();
