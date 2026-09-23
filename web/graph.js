/**
 * Deterministic, dependency-free Canvas view of the observed directed graph.
 * Coordinates in onHover are CSS pixels; clientX/clientY are viewport pixels.
 * "visible" counts describe the filtered graph, not a screen-space viewport.
 */
const COLORS = Object.freeze({
  consolidator: "#65e335",
  transit: "#79b5da",
  distributor: "#ae9ede",
  terminal: "#e2b871",
  coordinator: "#e59caa",
  peripheral: "#8b9e91",
});
const DEFAULTS = Object.freeze({
  selected: null,
  layout: "depth",
  focus: false,
  focusHops: 1,
  depth: 4,
  role: "all",
  cluster: null,
  query: "",
  highlightIds: [],
});
const FOCUS_LIMIT = 180;
const TAU = Math.PI * 2;
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
const idOrder = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const priority = (node) => clamp(Number(node.priority_score) || 0, 0, 1);
const color = (node) => COLORS[node.role] || COLORS.peripheral;
const shortMoney = (value) => {
  const amount = Number(value) || 0;
  const [base, suffix] =
    amount >= 1e9
      ? [1e9, "млрд"]
      : amount >= 1e6
        ? [1e6, "млн"]
        : amount >= 1e3
          ? [1e3, "тыс."]
          : [1, ""];
  return `${(amount / base).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${suffix} ₸`;
};

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

export class GraphView {
  constructor(
    canvas,
    { onSelect = () => {}, onHover = () => {}, onStatus = () => {} } = {},
  ) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    if (!this.ctx) throw new Error("Canvas 2D недоступен");
    this.onSelect = onSelect;
    this.onHover = onHover;
    this.onStatus = onStatus;
    this.options = { ...DEFAULTS };
    this.nodes = [];
    this.edges = [];
    this.nodeMap = new Map();
    this.visibleNodes = [];
    this.visibleEdges = [];
    this.positions = new Map();
    this.islands = [];
    this.selected = null;
    this.hovered = null;
    this.transform = { x: 0, y: 0, scale: 1 };
    this.width = 950;
    this.height = 420;
    this.dpr = 1;
    this.frame = null;
    this.pointer = null;
    this.disposed = false;
    this.hasData = false;
    this.listeners = [];
    this.bounds = { x: 0, y: 0, width: 1200, height: 560 };
    this.canvas.style.touchAction = "none";
    this.canvas.style.cursor = "grab";
    this.canvas.setAttribute("role", "img");
    this.canvas.setAttribute(
      "aria-label",
      "Граф наблюдаемых переводов. Выбор клиента — нажатием; стрелки меняют выбранный узел; плюс и минус изменяют масштаб.",
    );
    if (!this.canvas.hasAttribute("tabindex")) this.canvas.tabIndex = 0;
    this._listen("pointerdown", (event) => this._pointerDown(event));
    this._listen("pointermove", (event) => this._pointerMove(event));
    this._listen("pointerup", (event) => this._pointerUp(event));
    this._listen("pointercancel", () => this._cancelPointer());
    this._listen("lostpointercapture", () => this._cancelPointer());
    this._listen("pointerleave", () => {
      if (!this.pointer) this._setHover(null);
    });
    this._listen("wheel", (event) => this._wheel(event), { passive: false });
    this._listen("keydown", (event) => this._keyDown(event));
    this._listen("blur", () => this._setHover(null));
    this.resizeObserver = new ResizeObserver(() => this._resize());
    this.resizeObserver.observe(canvas);
    this._resize();
  }

  _listen(type, callback, options) {
    this.canvas.addEventListener(type, callback, options);
    this.listeners.push([type, callback, options]);
  }

  _layoutKey(options) {
    return JSON.stringify([
      options.layout,
      Boolean(options.focus),
      options.focusHops,
      options.focus ? options.selected : null,
      options.depth,
      options.role,
      options.cluster,
      String(options.query || "").trim(),
    ]);
  }

  setData(nodes, edges, options = {}) {
    if (this.disposed) return;
    const next = { ...DEFAULTS, ...options };
    next.layout = next.layout === "clusters" ? "clusters" : "depth";
    next.focusHops = Number(next.focusHops) === 2 ? 2 : 1;
    next.selected = next.selected == null ? null : String(next.selected);
    const key = this._layoutKey(next);
    const changed =
      !this.hasData ||
      this.sourceNodes !== nodes ||
      this.sourceEdges !== edges ||
      this.layoutKey !== key;
    this.sourceNodes = nodes;
    this.sourceEdges = edges;
    this.layoutKey = key;
    this.nodes = Array.isArray(nodes) ? nodes.slice() : [];
    this.edges = Array.isArray(edges) ? edges.slice() : [];
    this.options = next;
    this.selected = next.selected;
    this.nodeMap = new Map(this.nodes.map((node) => [String(node.gid), node]));
    this.highlighted = new Set((next.highlightIds || []).map(String));
    this.hasData = true;
    if (changed) this._rebuild();
    else {
      this._setHover(null);
      if (this.status)
        this.status.selectedVisible = this.positions.has(this.selected);
      this._publishStatus();
      this._schedule();
    }
  }

  _rebuild() {
    const opts = this.options;
    const query = String(opts.query || "").trim();
    const maxDepth = Number.isFinite(Number(opts.depth))
      ? Number(opts.depth)
      : 4;
    const matching = this.nodes.filter(
      (node) =>
        Number(node.depth) <= maxDepth &&
        (!opts.role || opts.role === "all" || node.role === opts.role) &&
        (opts.cluster == null ||
          opts.cluster === "" ||
          opts.cluster === "all" ||
          String(node.cluster_id) === String(opts.cluster)) &&
        (!query || String(node.gid).includes(query)),
    );
    const allowed = new Set(matching.map((node) => String(node.gid)));
    const validEdges = this.edges.filter(
      (edge) => allowed.has(String(edge.src)) && allowed.has(String(edge.dst)),
    );
    let ids = allowed;
    let focusCount = matching.length;
    let truncated = false;
    this.distances = new Map();
    if (opts.focus) {
      ids = new Set();
      if (this.selected && allowed.has(this.selected)) {
        const neighbors = new Map(
          matching.map((node) => [String(node.gid), new Set()]),
        );
        for (const edge of validEdges) {
          const src = String(edge.src),
            dst = String(edge.dst);
          neighbors.get(src).add(dst);
          neighbors.get(dst).add(src);
        }
        this.distances.set(this.selected, 0);
        let frontier = [this.selected];
        for (let hop = 1; hop <= opts.focusHops; hop += 1) {
          const next = [];
          for (const gid of frontier) {
            for (const peer of neighbors.get(gid)) {
              if (!this.distances.has(peer)) {
                this.distances.set(peer, hop);
                next.push(peer);
              }
            }
          }
          frontier = next;
        }
        const candidates = [...this.distances.keys()].sort(
          (a, b) =>
            this.distances.get(a) - this.distances.get(b) ||
            priority(this.nodeMap.get(b)) - priority(this.nodeMap.get(a)) ||
            idOrder(a, b),
        );
        focusCount = candidates.length;
        truncated = candidates.length > FOCUS_LIMIT;
        ids = new Set(candidates.slice(0, FOCUS_LIMIT));
      } else {
        focusCount = 0;
      }
    }
    this.visibleNodes = matching.filter((node) => ids.has(String(node.gid)));
    this.visibleEdges = validEdges.filter(
      (edge) => ids.has(String(edge.src)) && ids.has(String(edge.dst)),
    );
    this.edgePairs = new Set(
      this.visibleEdges.map((edge) => `${edge.src}>${edge.dst}`),
    );
    this.neighborIds = new Map(
      this.visibleNodes.map((node) => [String(node.gid), new Set()]),
    );
    for (const edge of this.visibleEdges) {
      this.neighborIds.get(String(edge.src)).add(String(edge.dst));
      this.neighborIds.get(String(edge.dst)).add(String(edge.src));
    }
    this.logMaxAmount = Math.max(
      1,
      ...this.visibleEdges.map((edge) =>
        Math.log1p(Math.max(0, Number(edge.sum_kzt) || 0)),
      ),
    );
    this.positions.clear();
    this.islands = [];
    if (opts.layout === "clusters") this._clusterLayout();
    else this._depthLayout();
    this._setHover(null);
    this.status = {
      visibleNodes: this.visibleNodes.length,
      visibleEdges: this.visibleEdges.length,
      totalNodes: this.nodes.length,
      totalEdges: this.edges.length,
      matchingNodes: matching.length,
      matchingEdges: validEdges.length,
      focusNodes: opts.focus ? focusCount : null,
      focusHops: opts.focus ? opts.focusHops : null,
      focusLimit: opts.focus ? FOCUS_LIMIT : null,
      truncated,
      omittedNodes: opts.focus ? Math.max(0, focusCount - ids.size) : 0,
      selectedVisible: this.selected != null && ids.has(this.selected),
      layout: opts.layout,
      mode: opts.focus ? "focus" : "network",
      filtered: matching.length !== this.nodes.length,
    };
    this.fit();
  }

  _nodeOrder(a, b) {
    const aId = String(a.gid),
      bId = String(b.gid);
    return (
      Number(bId === this.selected) - Number(aId === this.selected) ||
      (this.distances.get(aId) ?? 9) - (this.distances.get(bId) ?? 9) ||
      priority(b) - priority(a) ||
      idOrder(aId, bId)
    );
  }

  _depthLayout() {
    const groups = Array.from({ length: 5 }, () => []);
    for (const node of this.visibleNodes) {
      groups[clamp(Math.trunc(Number(node.depth) || 0), 0, 4)].push(node);
    }
    if (this.options.focus) {
      this._focusDepthLayout(groups);
      return;
    }
    this.depthColumns = groups.map((_, depth) => ({
      depth,
      x: 104 + depth * 248,
    }));
    this.bounds = { x: 0, y: 0, width: 1200, height: 550 };
    for (let depth = 0; depth < groups.length; depth += 1) {
      const group = groups[depth].sort((a, b) => this._nodeOrder(a, b));
      if (!group.length) continue;
      const rows = Math.min(
        group.length,
        Math.ceil(Math.sqrt(group.length * 2.4)),
      );
      const columns = Math.ceil(group.length / rows);
      const dx = Math.min(46, 168 / Math.max(1, columns - 1));
      const dy = Math.min(46, 398 / Math.max(1, rows - 1));
      const slots = [];
      for (let column = 0; column < columns; column += 1) {
        for (let row = 0; row < rows; row += 1) {
          const x = (column - (columns - 1) / 2) * dx;
          const y = (row - (rows - 1) / 2) * dy;
          slots.push({ x, y, radius: Math.hypot(x * 1.8, y) });
        }
      }
      slots.sort((a, b) => a.radius - b.radius || a.y - b.y || a.x - b.x);
      group.forEach((node, index) =>
        this.positions.set(String(node.gid), {
          x: 104 + depth * 248 + slots[index].x,
          y: 282 + slots[index].y,
        }),
      );
    }
  }

  _focusDepthLayout(groups) {
    const width = Math.max(560, this.width - 46);
    const height = Math.max(330, this.height - 42);
    const active = groups
      .map((nodes, depth) => ({ nodes, depth }))
      .filter((group) => group.nodes.length);
    this.bounds = { x: 0, y: 0, width, height };
    const center = height / 2 + 6;
    this.depthColumns = active.map((group, index) => ({
      depth: group.depth,
      x:
        active.length === 1
          ? width / 2
          : width * (0.15 + (index * 0.7) / (active.length - 1)),
    }));
    if (active.length === 1 && this.visibleNodes.length <= 25) {
      const group = active[0].nodes
        .slice()
        .sort((a, b) => this._nodeOrder(a, b));
      const selected = group.find((node) => String(node.gid) === this.selected);
      const peers = group.filter((node) => node !== selected);
      if (selected)
        this.positions.set(String(selected.gid), { x: width / 2, y: center });
      peers.forEach((node, index) => {
        const angle = -Math.PI / 2 + (index * TAU) / Math.max(peers.length, 1);
        this.positions.set(String(node.gid), {
          x: width / 2 + Math.cos(angle) * width * 0.34,
          y: center + Math.sin(angle) * height * 0.33,
        });
      });
      return;
    }
    for (let column = 0; column < active.length; column += 1) {
      const group = active[column].nodes
        .slice()
        .sort((a, b) => this._nodeOrder(a, b));
      const rows =
        group.length <= 9
          ? group.length
          : Math.ceil(Math.sqrt(group.length * 2.5));
      const columns = Math.ceil(group.length / rows);
      const dy = Math.min(
        group.length <= 9 ? 88 : 46,
        (height - 132) / Math.max(1, rows - 1),
      );
      const dx = Math.min(42, (width * 0.15) / Math.max(1, columns - 1));
      const slots = [];
      for (let c = 0; c < columns; c += 1) {
        for (let row = 0; row < rows; row += 1) {
          const x = (c - (columns - 1) / 2) * dx;
          const y = (row - (rows - 1) / 2) * dy;
          slots.push({ x, y, radius: Math.hypot(x * 1.8, y) });
        }
      }
      slots.sort((a, b) => a.radius - b.radius || a.y - b.y || a.x - b.x);
      group.forEach((node, index) =>
        this.positions.set(String(node.gid), {
          x: this.depthColumns[column].x + slots[index].x,
          y: center + slots[index].y,
        }),
      );
    }
  }

  _clusterLayout() {
    const byCluster = new Map();
    for (const node of this.visibleNodes) {
      const key = String(node.cluster_id);
      if (!byCluster.has(key)) byCluster.set(key, []);
      byCluster.get(key).push(node);
    }
    const groups = [...byCluster]
      .map(([id, nodes]) => ({
        id,
        nodes: nodes.sort((a, b) => this._nodeOrder(a, b)),
        weight: nodes.length + 7,
      }))
      .sort((a, b) => b.weight - a.weight || idOrder(a.id, b.id));
    this.bounds = { x: 0, y: 0, width: 1200, height: 550 };
    const partition = (items, x, y, width, height) => {
      if (!items.length) return;
      if (items.length === 1) {
        const group = items[0];
        const island = {
          id: group.id,
          x: x + 5,
          y: y + 5,
          width: width - 10,
          height: height - 10,
          nodes: group.nodes,
        };
        this.islands.push(island);
        const radiusX = Math.max(0, island.width / 2 - 16);
        const radiusY = Math.max(0, island.height / 2 - 26);
        group.nodes.forEach((node, index) => {
          const theta = index * 2.399963229728653;
          const radius =
            group.nodes.length === 1
              ? 0
              : Math.sqrt(index / (group.nodes.length - 1));
          this.positions.set(String(node.gid), {
            x: island.x + island.width / 2 + Math.cos(theta) * radiusX * radius,
            y:
              island.y +
              island.height / 2 +
              6 +
              Math.sin(theta) * radiusY * radius,
          });
        });
        return;
      }
      const total = items.reduce((sum, item) => sum + item.weight, 0);
      let split = 1,
        partial = items[0].weight;
      while (split < items.length - 1 && partial < total / 2) {
        if (
          Math.abs(partial - total / 2) <
          Math.abs(partial + items[split].weight - total / 2)
        )
          break;
        partial += items[split].weight;
        split += 1;
      }
      const ratio = clamp(partial / total, 0.12, 0.88);
      if (width >= height * 1.1) {
        partition(items.slice(0, split), x, y, width * ratio, height);
        partition(
          items.slice(split),
          x + width * ratio,
          y,
          width * (1 - ratio),
          height,
        );
      } else {
        partition(items.slice(0, split), x, y, width, height * ratio);
        partition(
          items.slice(split),
          x,
          y + height * ratio,
          width,
          height * (1 - ratio),
        );
      }
    };
    partition(groups, 20, 20, 1160, 510);
  }

  setSelection(gid) {
    if (this.disposed) return;
    const selected = gid == null ? null : String(gid);
    if (this.selected === selected) return;
    this.selected = selected;
    this.options.selected = selected;
    this.layoutKey = this._layoutKey(this.options);
    if (this.options.focus) this._rebuild();
    else {
      if (this.status) {
        this.status.selectedVisible =
          selected != null && this.positions.has(selected);
        this._publishStatus();
      }
      this._schedule();
    }
  }

  zoomBy(factor) {
    if (this.disposed || !Number.isFinite(factor) || factor <= 0) return;
    this._zoomAt(factor, this.width / 2, this.height / 2);
  }

  _zoomAt(factor, x, y) {
    const current = this.transform;
    const next = clamp(current.scale * factor, 0.12, 8);
    const ratio = next / current.scale;
    this.transform = {
      scale: next,
      x: x - (x - current.x) * ratio,
      y: y - (y - current.y) * ratio,
    };
    this._setHover(null);
    this._publishStatus();
    this._schedule();
  }

  fit() {
    if (this.disposed) return;
    const padX = this.width < 600 ? 14 : 32,
      padY = 30;
    const bounds = this.bounds;
    const scale = clamp(
      Math.min(
        (this.width - padX * 2) / bounds.width,
        (this.height - padY * 2) / bounds.height,
      ),
      0.12,
      3,
    );
    this.transform = {
      scale,
      x: (this.width - bounds.width * scale) / 2 - bounds.x * scale,
      y: (this.height - bounds.height * scale) / 2 - bounds.y * scale,
    };
    this.fitScale = scale;
    this._publishStatus();
    this._schedule();
  }

  _publishStatus() {
    if (!this.status) return;
    this.onStatus({
      ...this.status,
      zoom: this.transform.scale / (this.fitScale || 1),
      scale: this.transform.scale,
    });
  }

  _resize() {
    if (this.disposed) return;
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width || 950));
    const height = Math.max(1, Math.round(rect.height || 420));
    const dpr = clamp(window.devicePixelRatio || 1, 1, 2);
    if (
      this.width === width &&
      this.height === height &&
      this.dpr === dpr &&
      this.canvas.width === Math.round(width * dpr)
    )
      return;
    this.width = width;
    this.height = height;
    this.dpr = dpr;
    this.canvas.width = Math.round(width * dpr);
    this.canvas.height = Math.round(height * dpr);
    if (this.hasData && this.options.focus && this.options.layout === "depth")
      this._depthLayout();
    this.fit();
  }

  _screen(gid) {
    const point = this.positions.get(gid);
    return point
      ? {
          x: point.x * this.transform.scale + this.transform.x,
          y: point.y * this.transform.scale + this.transform.y,
        }
      : null;
  }

  _radius(node) {
    const scale = this.transform.scale;
    const density =
      this.visibleNodes.length > 700
        ? 0.44
        : this.visibleNodes.length > 180
          ? 0.68
          : 1;
    const base = (5 + priority(node) * 6) * density;
    return (
      clamp(
        base * Math.sqrt(scale),
        this.visibleNodes.length > 700 ? 1.8 : 3,
        18,
      ) + (String(node.gid) === this.selected ? 2.2 : 0)
    );
  }

  _schedule() {
    if (this.disposed || this.frame != null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      if (!this.disposed) this._draw();
    });
  }

  _draw() {
    const ctx = this.ctx,
      width = this.width,
      height = this.height;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#121a15";
    ctx.fillRect(0, 0, width, height);
    const glow = ctx.createRadialGradient(
      width * 0.48,
      height * 0.5,
      10,
      width * 0.48,
      height * 0.5,
      width * 0.55,
    );
    glow.addColorStop(0, "#1b2e2088");
    glow.addColorStop(1, "#121a1500");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#768c7624";
    for (let x = 14; x < width; x += 22) {
      for (let y = 14; y < height; y += 22) ctx.fillRect(x, y, 1, 1);
    }
    if (!this.visibleNodes.length) {
      ctx.fillStyle = "#a1afa5";
      ctx.font = "13px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(
        this.hasData
          ? this.options.focus && !this.selected
            ? "Выберите клиента, чтобы увидеть его связи"
            : "Нет клиентов по выбранным фильтрам"
          : "Загрузка наблюдаемых связей…",
        width / 2,
        height / 2,
      );
      return;
    }
    if (this.options.layout === "clusters") this._drawIslands();
    else this._drawDepthBands();
    const activeId = this.hovered || this.selected;
    const highlightedEdges = [];
    this.edgeLabelBoxes = [];
    for (const edge of this.visibleEdges) {
      if (String(edge.src) === activeId || String(edge.dst) === activeId)
        highlightedEdges.push(edge);
      else this._drawEdge(edge, false);
    }
    for (const edge of highlightedEdges) this._drawEdge(edge, true);
    const selectedNodes = [];
    for (const node of this.visibleNodes) {
      const gid = String(node.gid);
      if (gid === this.selected || gid === this.hovered)
        selectedNodes.push(node);
      else this._drawNode(node);
    }
    for (const node of selectedNodes) this._drawNode(node);
    this._drawLabels();
  }

  _drawDepthBands() {
    const ctx = this.ctx;
    const columns = this.depthColumns || [];
    ctx.save();
    ctx.font =
      "500 10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    columns.forEach((column, index) => {
      const x = column.x * this.transform.scale + this.transform.x;
      if (x < -80 || x > this.width + 80) return;
      ctx.fillStyle = column.depth === 0 ? "#a0be9a" : "#83998b";
      ctx.fillText(
        column.depth === 0 ? "SEED · ГЛУБИНА 0" : `ГЛУБИНА ${column.depth}`,
        x,
        19,
      );
      if (index < columns.length - 1) {
        const separator =
          ((column.x + columns[index + 1].x) / 2) * this.transform.scale +
          this.transform.x;
        ctx.strokeStyle = "#33443855";
        ctx.setLineDash([3, 7]);
        ctx.beginPath();
        ctx.moveTo(separator, 39);
        ctx.lineTo(separator, this.height - 22);
        ctx.stroke();
      }
    });
    ctx.restore();
  }

  _drawIslands() {
    const ctx = this.ctx;
    ctx.save();
    for (const island of this.islands) {
      const x = island.x * this.transform.scale + this.transform.x;
      const y = island.y * this.transform.scale + this.transform.y;
      const width = island.width * this.transform.scale;
      const height = island.height * this.transform.scale;
      if (x + width < 0 || y + height < 0 || x > this.width || y > this.height)
        continue;
      const active = island.nodes.some(
        (node) => String(node.gid) === this.selected,
      );
      roundedRect(ctx, x, y, width, height, 12);
      ctx.fillStyle = active ? "#26482a36" : "#23322935";
      ctx.fill();
      ctx.strokeStyle = active ? "#72c75466" : "#536c5448";
      ctx.lineWidth = active ? 1.1 : 0.8;
      ctx.stroke();
      if (width > 45 && height > 30) {
        ctx.fillStyle = active ? "#b3d5a6" : "#7e9683";
        ctx.font =
          "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.fillText(
          width > 138
            ? `СООБЩЕСТВО ${island.id} · ${island.nodes.length}`
            : `#${island.id}`,
          x + 10,
          y + 8,
        );
      }
    }
    ctx.restore();
  }

  _drawEdge(edge, highlighted) {
    const srcId = String(edge.src),
      dstId = String(edge.dst);
    const a = this._screen(srcId),
      b = this._screen(dstId);
    if (!a || !b) return;
    if (
      Math.max(a.x, b.x) < -60 ||
      Math.min(a.x, b.x) > this.width + 60 ||
      Math.max(a.y, b.y) < -60 ||
      Math.min(a.y, b.y) > this.height + 60
    )
      return;
    const ctx = this.ctx;
    const source = this.nodeMap.get(srcId),
      target = this.nodeMap.get(dstId);
    const weight =
      Math.log1p(Math.max(0, Number(edge.sum_kzt) || 0)) / this.logMaxAmount;
    const many = this.visibleNodes.length > 500;
    ctx.save();
    ctx.strokeStyle = highlighted
      ? color(this.nodeMap.get(this.hovered || this.selected) || source)
      : "#64846e";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.globalAlpha = highlighted ? 0.86 : many ? 0.12 : 0.25;
    ctx.lineWidth = highlighted
      ? 1 + weight * 1.8
      : (many ? 0.32 : 0.5) + weight * 0.65;
    if (srcId === dstId) {
      const r = this._radius(source);
      ctx.beginPath();
      ctx.arc(a.x + r, a.y - r, r + 5, 0.3, TAU - 0.5);
      ctx.stroke();
      ctx.restore();
      return;
    }
    const dx = b.x - a.x,
      dy = b.y - a.y;
    const distance = Math.hypot(dx, dy) || 1;
    const ux = dx / distance,
      uy = dy / distance;
    const startR = this._radius(source) + 2,
      endR = this._radius(target) + 4;
    const start = { x: a.x + ux * startR, y: a.y + uy * startR };
    const end = { x: b.x - ux * endR, y: b.y - uy * endR };
    const sameColumn =
      this.options.layout === "depth" &&
      Number(source.depth) === Number(target.depth);
    const reciprocal = this.edgePairs.has(`${dstId}>${srcId}`);
    const bend = reciprocal ? (idOrder(srcId, dstId) < 0 ? 18 : -18) : 0;
    const c1 = sameColumn
      ? { x: start.x + 30 + Math.abs(dy) * 0.16, y: start.y + dy * 0.28 }
      : { x: start.x + dx * 0.43, y: start.y + bend };
    const c2 = sameColumn
      ? { x: end.x + 30 + Math.abs(dy) * 0.16, y: end.y - dy * 0.28 }
      : { x: end.x - dx * 0.43, y: end.y + bend };
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, end.x, end.y);
    ctx.stroke();
    if (
      highlighted ||
      (this.transform.scale > 1 &&
        distance > 34 &&
        this.visibleEdges.length < 3500)
    ) {
      const angle = Math.atan2(end.y - c2.y, end.x - c2.x);
      const size = highlighted ? 6.2 : 4;
      ctx.beginPath();
      ctx.moveTo(end.x, end.y);
      ctx.lineTo(
        end.x - Math.cos(angle - 0.45) * size,
        end.y - Math.sin(angle - 0.45) * size,
      );
      ctx.lineTo(
        end.x - Math.cos(angle + 0.45) * size,
        end.y - Math.sin(angle + 0.45) * size,
      );
      ctx.closePath();
      ctx.fill();
    }
    if (highlighted && this.visibleNodes.length <= 25 && distance > 55) {
      this._drawEdgeAmount(edge, start, c1, c2, end);
    }
    ctx.restore();
  }

  _drawEdgeAmount(edge, start, c1, c2, end) {
    const ctx = this.ctx;
    const count = Math.max(0, Number(edge.n_tx) || 0);
    const label = `${shortMoney(edge.sum_kzt)} · ${count.toLocaleString("ru-RU")} оп.`;
    ctx.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    const width = ctx.measureText(label).width + 10;
    let placement = null,
      best = Infinity;
    const candidates = [
      [0.5, -1],
      [0.37, -1],
      [0.63, -1],
      [0.45, 1],
      [0.55, 1],
    ];
    for (const [t, side] of candidates) {
      const u = 1 - t;
      const x =
        u ** 3 * start.x +
        3 * u ** 2 * t * c1.x +
        3 * u * t ** 2 * c2.x +
        t ** 3 * end.x;
      const y =
        u ** 3 * start.y +
        3 * u ** 2 * t * c1.y +
        3 * u * t ** 2 * c2.y +
        t ** 3 * end.y +
        side * 13;
      const box = { x: x - width / 2, y: y - 9, width, height: 18 };
      if (
        box.x < 5 ||
        box.x + width > this.width - 5 ||
        box.y < 39 ||
        box.y > this.height - 24
      )
        continue;
      let score = this.edgeLabelBoxes.filter(
        (other) =>
          box.x < other.x + other.width &&
          box.x + box.width > other.x &&
          box.y < other.y + other.height &&
          box.y + box.height > other.y,
      ).length;
      for (const node of this.visibleNodes) {
        const point = this._screen(String(node.gid)),
          radius = this._radius(node) + 7;
        if (
          point.x + radius > box.x &&
          point.x - radius < box.x + box.width &&
          point.y + radius > box.y &&
          point.y - radius < box.y + box.height
        )
          score += 10;
      }
      if (score < best) {
        placement = { x, y, box };
        best = score;
      }
      if (!score) break;
    }
    if (!placement) return;
    this.edgeLabelBoxes.push(placement.box);
    ctx.globalAlpha = 1;
    roundedRect(
      ctx,
      placement.box.x,
      placement.box.y,
      placement.box.width,
      placement.box.height,
      4,
    );
    ctx.fillStyle = "#18261bdd";
    ctx.fill();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#bfdaa9";
    ctx.fillText(label, placement.x, placement.y);
  }

  _drawNode(node) {
    const gid = String(node.gid),
      point = this._screen(gid);
    if (
      !point ||
      point.x < -30 ||
      point.y < -30 ||
      point.x > this.width + 30 ||
      point.y > this.height + 30
    )
      return;
    const ctx = this.ctx,
      radius = this._radius(node);
    const selected = gid === this.selected,
      hovered = gid === this.hovered;
    const connected = this.neighborIds
      .get(this.hovered || this.selected)
      ?.has(gid);
    const dim =
      (this.hovered || this.selected) && !selected && !hovered && !connected;
    ctx.save();
    ctx.globalAlpha = dim ? 0.57 : 1;
    if (selected) {
      ctx.shadowColor = color(node);
      ctx.shadowBlur = 22;
      ctx.fillStyle = color(node) + "22";
      ctx.beginPath();
      ctx.arc(point.x, point.y, radius + 7, 0, TAU);
      ctx.fill();
      ctx.shadowBlur = 0;
    }
    if (node.is_seed || selected || this.highlighted?.has(gid)) {
      ctx.strokeStyle = selected ? color(node) : "#b0caa3";
      ctx.globalAlpha = selected ? 0.92 : 0.48;
      ctx.lineWidth = selected ? 1.3 : 0.8;
      ctx.beginPath();
      ctx.arc(point.x, point.y, radius + (selected ? 5 : 3), 0, TAU);
      ctx.stroke();
    }
    ctx.globalAlpha = dim ? 0.54 : 1;
    ctx.fillStyle = color(node);
    ctx.strokeStyle = selected || hovered ? "#edffe7" : "#16251b";
    ctx.lineWidth = selected || hovered ? 1.5 : 1;
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, TAU);
    ctx.fill();
    ctx.stroke();
    if (selected && radius > 7) {
      ctx.fillStyle = "#153013";
      ctx.beginPath();
      ctx.arc(point.x, point.y, 2.2, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }

  _drawLabels() {
    const ctx = this.ctx;
    const sparse = this.visibleNodes.length <= 36;
    const showFull = this.visibleNodes.length <= 12;
    const occupied = [];
    const candidates = this.visibleNodes
      .slice()
      .sort((a, b) => this._nodeOrder(a, b));
    ctx.save();
    ctx.textBaseline = "middle";
    for (const node of candidates) {
      const gid = String(node.gid),
        selected = gid === this.selected,
        hovered = gid === this.hovered;
      if (
        !selected &&
        !hovered &&
        !sparse &&
        !(this.transform.scale > 1.6 && priority(node) > 0.72)
      )
        continue;
      const point = this._screen(gid);
      if (
        !point ||
        point.x < 0 ||
        point.x > this.width ||
        point.y < 30 ||
        point.y > this.height - 10
      )
        continue;
      const text = selected || hovered || showFull ? gid : `…${gid.slice(-6)}`;
      ctx.font = `${selected ? "600" : "500"} ${selected ? 12 : 10}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      const size = ctx.measureText(text).width,
        radius = this._radius(node);
      const choices =
        selected || showFull
          ? [
              { x: point.x - size / 2, y: point.y + radius + 17 },
              { x: point.x - size / 2, y: point.y - radius - 16 },
              { x: point.x + radius + 10, y: point.y },
            ]
          : [
              { x: point.x + radius + 10, y: point.y },
              { x: point.x - radius - 10 - size, y: point.y },
              { x: point.x - size / 2, y: point.y + radius + 16 },
            ];
      let placement = null;
      for (const choice of choices) {
        const x = clamp(choice.x, 9, Math.max(9, this.width - size - 9));
        const y = clamp(choice.y, 43, this.height - 13);
        const box = { x: x - 4, y: y - 9, width: size + 8, height: 18 };
        const overlapsLabel = occupied.some(
          (other) =>
            box.x < other.x + other.width &&
            box.x + box.width > other.x &&
            box.y < other.y + other.height &&
            box.y + box.height > other.y,
        );
        const overlapsNode = this.visibleNodes.some((other) => {
          if (String(other.gid) === gid) return false;
          const p = this._screen(String(other.gid)),
            r = this._radius(other) + 3;
          return (
            p.x + r > box.x &&
            p.x - r < box.x + box.width &&
            p.y + r > box.y &&
            p.y - r < box.y + box.height
          );
        });
        if (!overlapsLabel && !overlapsNode) {
          placement = { x, y, box };
          break;
        }
      }
      if (!placement && !selected && !hovered) continue;
      if (!placement) {
        const x = clamp(
          point.x - size / 2,
          9,
          Math.max(9, this.width - size - 9),
        );
        const y = clamp(point.y + radius + 17, 43, this.height - 13);
        placement = {
          x,
          y,
          box: { x: x - 4, y: y - 9, width: size + 8, height: 18 },
        };
      }
      occupied.push(placement.box);
      ctx.textAlign = "left";
      if (selected || showFull) {
        roundedRect(
          ctx,
          placement.box.x,
          placement.box.y,
          placement.box.width,
          placement.box.height,
          4,
        );
        ctx.fillStyle = selected ? "#152419ed" : "#152018d9";
        ctx.fill();
      }
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#152018";
      ctx.strokeText(text, placement.x, placement.y);
      ctx.fillStyle = selected ? "#d5f6c7" : hovered ? "#f0f7ef" : "#b1c6b8";
      ctx.fillText(text, placement.x, placement.y);
    }
    ctx.restore();
  }

  _point(event) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  _hit(x, y) {
    let hit = null,
      closest = Infinity;
    for (const node of this.visibleNodes) {
      const point = this._screen(String(node.gid));
      const distance = Math.hypot(point.x - x, point.y - y);
      const radius =
        this._radius(node) + (this.pointer?.type === "touch" ? 5 : 3);
      if (distance <= Math.max(radius, 7) && distance < closest) {
        closest = distance;
        hit = node;
      }
    }
    return hit;
  }

  _setHover(node, event) {
    const gid = node ? String(node.gid) : null;
    const changed = gid !== this.hovered;
    this.hovered = gid;
    if (node && event) {
      const point = this._point(event);
      this.onHover(node, {
        ...point,
        clientX: event.clientX,
        clientY: event.clientY,
      });
    } else if (changed || !node) {
      this.onHover(null, null);
    }
    if (!this.pointer) this.canvas.style.cursor = node ? "pointer" : "grab";
    if (changed) this._schedule();
  }

  _pointerDown(event) {
    if (this.pointer || event.button > 0) return;
    const point = this._point(event);
    this.pointer = {
      id: event.pointerId,
      type: event.pointerType,
      x: point.x,
      y: point.y,
      startX: this.transform.x,
      startY: this.transform.y,
      moved: false,
    };
    this.canvas.setPointerCapture(event.pointerId);
    this.canvas.style.cursor = "grabbing";
    this._setHover(null);
  }

  _pointerMove(event) {
    const point = this._point(event);
    if (this.pointer && this.pointer.id === event.pointerId) {
      const dx = point.x - this.pointer.x,
        dy = point.y - this.pointer.y;
      if (Math.hypot(dx, dy) > 4) this.pointer.moved = true;
      if (this.pointer.moved) {
        this.transform.x = this.pointer.startX + dx;
        this.transform.y = this.pointer.startY + dy;
        this._schedule();
      }
    } else if (!this.pointer) {
      this._setHover(this._hit(point.x, point.y), event);
    }
  }

  _pointerUp(event) {
    if (!this.pointer || this.pointer.id !== event.pointerId) return;
    const moved = this.pointer.moved;
    const point = this._point(event);
    const node = !moved ? this._hit(point.x, point.y) : null;
    this._cancelPointer();
    if (node) {
      const gid = String(node.gid);
      this.setSelection(gid);
      this.onSelect(gid);
      this.canvas.focus({ preventScroll: true });
    }
  }

  _cancelPointer() {
    if (!this.pointer) return;
    const id = this.pointer.id;
    this.pointer = null;
    if (this.canvas.hasPointerCapture(id))
      this.canvas.releasePointerCapture(id);
    this.canvas.style.cursor = "grab";
  }

  _wheel(event) {
    event.preventDefault();
    const point = this._point(event);
    this._zoomAt(
      Math.exp(-clamp(event.deltaY, -160, 160) * 0.002),
      point.x,
      point.y,
    );
  }

  _keyDown(event) {
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      this.zoomBy(1.2);
      return;
    }
    if (event.key === "-") {
      event.preventDefault();
      this.zoomBy(1 / 1.2);
      return;
    }
    if (event.key === "0" || event.key === "Home") {
      event.preventDefault();
      this.fit();
      return;
    }
    const direction = {
      ArrowRight: [1, 0],
      ArrowLeft: [-1, 0],
      ArrowDown: [0, 1],
      ArrowUp: [0, -1],
    }[event.key];
    if (!direction || !this.visibleNodes.length) return;
    event.preventDefault();
    const origin = this._screen(this.selected) || {
      x: this.width / 2,
      y: this.height / 2,
    };
    let best = null,
      score = Infinity;
    for (const node of this.visibleNodes) {
      if (String(node.gid) === this.selected) continue;
      const point = this._screen(String(node.gid));
      const dx = point.x - origin.x,
        dy = point.y - origin.y;
      const projection = dx * direction[0] + dy * direction[1];
      if (projection <= 0) continue;
      const side = Math.abs(dx * direction[1] - dy * direction[0]);
      const candidate = Math.hypot(dx, dy) + side * 2;
      if (candidate < score) {
        best = node;
        score = candidate;
      }
    }
    if (best) {
      const gid = String(best.gid);
      this.setSelection(gid);
      this.onSelect(gid);
    }
  }

  destroy() {
    if (this.disposed) return;
    this.disposed = true;
    this._cancelPointer();
    if (this.frame != null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.resizeObserver.disconnect();
    for (const [type, callback, options] of this.listeners)
      this.canvas.removeEventListener(type, callback, options);
    this.listeners = [];
    this.onHover(null, null);
  }
}
