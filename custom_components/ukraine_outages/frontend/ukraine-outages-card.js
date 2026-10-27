// «Графік відключень»: the dashboard card of the Ukraine Power Outages integration.
// Served by the integration itself (card.py) and loaded on every dashboard page.
//
//   type: custom:ukraine-outages-card
//   devices: [<device id>]        # one: the full card; several: one compact row each
//   title: Дім                    # optional
//   show_tomorrow: true           # optional
//
// Text from operators (titles, notes) is set as text, never as HTML; links are https only.

const CARD = "ukraine-outages-card";
const EDITOR = "ukraine-outages-card-editor";
const KYIV = "Europe/Kyiv";
const REFRESH_MS = 5 * 60 * 1000;
const TICK_MS = 30 * 1000;

const STRINGS = {
  uk: {
    name: "Графік відключень",
    description: "Світло зараз, відлік до вимкнення чи увімкнення і графік на сьогодні й завтра.",
    on: "Світло є",
    off: "Немає світла",
    maybe: "Можливе відключення",
    unknown: "Невідомо",
    off_at: "Вимкнуть о {time}",
    off_tomorrow: "Вимкнуть завтра о {time}",
    on_at: "Увімкнуть о {time}",
    on_tomorrow: "Увімкнуть завтра о {time}",
    left: "через {left}",
    nothing: "Відключень за графіком не видно",
    today: "Сьогодні",
    tomorrow: "Завтра",
    not_published: "Ще не опубліковано",
    no_outages: "Без відключень",
    without: "без світла {hours}",
    picture: "Графік на картинці оператора",
    updated: "Оновлено {time}",
    source: "джерело: {name}",
    source_down: "Сайт оператора не відповідає — показано останній відомий графік",
    hours: "год",
    minutes: "хв",
    pick: "Оберіть у налаштуваннях картки чергу (пристрій інтеграції).",
    missing: "Чергу не знайдено: можливо, її видалили.",
    devices: "Черги",
    title: "Заголовок",
    show_tomorrow: "Показувати завтра",
  },
  en: {
    name: "Outage schedule",
    description: "Power now, a countdown to the next outage or to power back, and today's and tomorrow's schedule.",
    on: "Power on",
    off: "No power",
    maybe: "Possible outage",
    unknown: "Unknown",
    off_at: "Off at {time}",
    off_tomorrow: "Off tomorrow at {time}",
    on_at: "Back at {time}",
    on_tomorrow: "Back tomorrow at {time}",
    left: "in {left}",
    nothing: "No scheduled outages ahead",
    today: "Today",
    tomorrow: "Tomorrow",
    not_published: "Not published yet",
    no_outages: "No outages",
    without: "{hours} without power",
    picture: "The schedule is on the operator's picture",
    updated: "Updated {time}",
    source: "source: {name}",
    source_down: "The operator's site does not answer — showing the last known schedule",
    hours: "h",
    minutes: "min",
    pick: "Choose a group (a device of the integration) in the card's settings.",
    missing: "Group not found: it may have been removed.",
    devices: "Groups",
    title: "Title",
    show_tomorrow: "Show tomorrow",
  },
};

function strings(hass) {
  const language = (hass && (hass.locale?.language || hass.language)) || "uk";
  return STRINGS[language.split("-")[0]] || STRINGS.en;
}

function fill(text, values) {
  return text.replace(/\{(\w+)\}/g, (_, key) => (key in values ? values[key] : ""));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function clock(iso, language) {
  return new Intl.DateTimeFormat(language, { hour: "2-digit", minute: "2-digit", timeZone: KYIV, hourCycle: "h23" }).format(
    new Date(iso),
  );
}

function kyivDate(iso) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: KYIV }).format(new Date(iso));
}

function span(minutes, t) {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (!h) return `${m} ${t.minutes}`;
  return m ? `${h} ${t.hours} ${m} ${t.minutes}` : `${h} ${t.hours}`;
}

function hhmm(minute) {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

function deviceIds(config) {
  if (Array.isArray(config.devices)) return config.devices.filter((d) => typeof d === "string");
  if (typeof config.devices === "string") return [config.devices];
  if (typeof config.device === "string") return [config.device];
  return [];
}

/** Half-hour cells of a day: "off", "maybe", "on" or "unknown" (not published, or hours unreadable). */
function cells(day) {
  const out = [];
  for (let i = 0; i < 48; i += 1) {
    const from = i * 30;
    const to = from + 30;
    if (!day.published || day.unknown) {
      out.push("unknown");
      continue;
    }
    const hits = day.outages.filter((o) => o.from < to && o.to > from);
    out.push(hits.some((o) => !o.possible) ? "off" : hits.length ? "maybe" : "on");
  }
  return out;
}

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
  .title { font-size: 1.15em; font-weight: 500; }
  .sub, .foot, .ticks, .note, .day-head .sum { color: var(--secondary-text-color); font-size: 0.85em; }
  .status { display: flex; gap: 12px; align-items: center; margin: 12px 0; padding: 10px 12px; border-radius: 10px;
            background: color-mix(in srgb, var(--state-color) 14%, transparent); }
  .status ha-icon { color: var(--state-color); --mdc-icon-size: 32px; }
  .status .state { font-size: 1.2em; font-weight: 500; }
  .on { --state-color: var(--success-color, #43a047); }
  .off { --state-color: var(--error-color, #db4437); }
  .maybe { --state-color: var(--warning-color, #ffa600); }
  .unknown { --state-color: var(--disabled-text-color, #9e9e9e); }
  .day { margin-top: 12px; }
  .day-head { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .bar { position: relative; display: grid; grid-template-columns: repeat(48, 1fr); gap: 1px; height: 22px;
         border-radius: 6px; overflow: hidden; }
  .bar.small { height: 10px; margin-top: 4px; }
  .cell { background: var(--state-color); opacity: 0.85; }
  .cell.on { opacity: 0.35; }
  .cell.maybe { background: repeating-linear-gradient(135deg, var(--state-color) 0 3px, transparent 3px 6px); opacity: 1; }
  .cell.unknown { background: var(--divider-color, #e0e0e0); opacity: 1; }
  .now { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--primary-text-color); }
  .ticks { display: flex; justify-content: space-between; margin-top: 2px; }
  .note { margin-top: 4px; }
  .note a { color: var(--primary-color); }
  .foot { margin-top: 12px; }
  .warn { color: var(--warning-color, #ffa600); }
  .row { padding: 8px 0; border-top: 1px solid var(--divider-color, #e0e0e0); }
  .row:first-of-type { border-top: 0; }
  .row-head { display: flex; flex-wrap: wrap; justify-content: space-between; column-gap: 12px; }
  .row-name { font-weight: 500; flex: none; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--state-color); margin-right: 6px; }
  .empty { color: var(--secondary-text-color); }
`;

class UkraineOutagesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = {};
    this._errors = {};
    this._fetchedAt = 0;
    this._signature = "";
  }

  static getConfigElement() {
    return document.createElement(EDITOR);
  }

  static getStubConfig(hass) {
    const entity = Object.values(hass.entities || {}).find((e) => e.platform === "ukraine_outages" && e.device_id);
    return entity ? { devices: [entity.device_id] } : { devices: [] };
  }

  setConfig(config) {
    this._config = { show_tomorrow: true, ...config };
    this._devices = deviceIds(this._config);
    this._data = {};
    this._fetchedAt = 0;
    this._signature = "";
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // Refetch when any entity of these devices changed (a fetch, an outage starting or ending).
    const ids = new Set(this._devices);
    const signature = Object.values(hass.entities || {})
      .filter((e) => ids.has(e.device_id))
      .map((e) => hass.states[e.entity_id]?.last_updated || "")
      .join("|");
    if (signature !== this._signature || Date.now() - this._fetchedAt > REFRESH_MS) {
      this._signature = signature;
      this._fetch();
    }
  }

  connectedCallback() {
    this._timer = setInterval(() => {
      if (Date.now() - this._fetchedAt > REFRESH_MS) this._fetch();
      else this._render();
    }, TICK_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return this._devices && this._devices.length > 1 ? 1 + this._devices.length : this._config?.show_tomorrow === false ? 4 : 6;
  }

  async _fetch() {
    if (!this._hass || !this._devices.length || this._loading) return;
    this._loading = true;
    this._fetchedAt = Date.now();
    await Promise.all(
      this._devices.map(async (id) => {
        try {
          this._data[id] = await this._hass.callWS({ type: "ukraine_outages/card", device_id: id });
          delete this._errors[id];
        } catch (err) {
          this._errors[id] = err;
        }
      }),
    );
    this._loading = false;
    this._render();
  }

  _minuteNow(data) {
    // The server's minute of the day, moved on by the time since it was fetched.
    return Math.min(1440, data.now_minute + (Date.now() - this._fetchedAt) / 60000);
  }

  _status(data, t, language) {
    const now = Date.now();
    const current = data.current && Date.parse(data.current.end) > now ? data.current : null;
    const today = kyivDate(data.now);
    if (current) {
      const tomorrow = kyivDate(current.end) !== today;
      return {
        state: current.possible ? "maybe" : "off",
        label: current.possible ? t.maybe : t.off,
        when: `${fill(tomorrow ? t.on_tomorrow : t.on_at, { time: clock(current.end, language) })} · ${fill(t.left, {
          left: span((Date.parse(current.end) - now) / 60000, t),
        })}`,
      };
    }
    const next = data.next && Date.parse(data.next.start) > now ? data.next : null;
    if (next) {
      const tomorrow = kyivDate(next.start) !== today;
      return {
        state: "on",
        label: t.on,
        when: `${fill(tomorrow ? t.off_tomorrow : t.off_at, { time: clock(next.start, language) })} · ${fill(t.left, {
          left: span((Date.parse(next.start) - now) / 60000, t),
        })}`,
      };
    }
    const published = data.days.some((d) => d.published);
    return { state: published ? "on" : "unknown", label: published ? t.on : t.unknown, when: t.nothing };
  }

  _bar(day, nowMinute, small) {
    const bar = el("div", small ? "bar small" : "bar");
    for (const state of cells(day)) bar.append(el("div", `cell ${state}`));
    if (nowMinute !== null) {
      const now = el("div", "now");
      now.style.left = `calc(${(nowMinute / 1440) * 100}% - 1px)`;
      bar.append(now);
    }
    return bar;
  }

  _day(day, name, nowMinute, t) {
    const box = el("div", "day");
    const head = el("div", "day-head");
    head.append(el("span", "", name));
    const definite = day.outages.filter((o) => !o.possible).reduce((sum, o) => sum + (o.to - o.from), 0);
    head.append(
      el("span", "sum", !day.published ? t.not_published : day.unknown ? t.picture : definite ? fill(t.without, { hours: span(definite, t) }) : t.no_outages),
    );
    box.append(head, this._bar(day, nowMinute, false));
    const ticks = el("div", "ticks");
    for (const h of [0, 6, 12, 18, 24]) ticks.append(el("span", "", `${h}`));
    box.append(ticks);
    if (day.published && !day.unknown && day.outages.length) {
      const text = day.outages.map((o) => `${hhmm(o.from)}–${hhmm(o.to)}${o.possible ? "?" : ""}`).join(", ");
      box.append(el("div", "note", text));
    }
    for (const note of day.notes || []) box.append(el("div", "note", note));
    if (day.image_url && day.image_url.startsWith("https://")) {
      const line = el("div", "note");
      const link = el("a", "", t.picture);
      link.href = day.image_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      line.append(link);
      box.append(line);
    }
    return box;
  }

  _full(card, data, t, language) {
    const head = el("div", "head");
    head.append(el("div", "title", this._config.title || data.title));
    head.append(el("div", "sub", `${data.operator}`));
    card.append(head);

    const status = this._status(data, t, language);
    const box = el("div", `status ${status.state}`);
    const icon = document.createElement("ha-icon");
    icon.setAttribute("icon", status.state === "on" ? "mdi:transmission-tower" : status.state === "unknown" ? "mdi:help-circle-outline" : "mdi:transmission-tower-off");
    const text = el("div");
    text.append(el("div", "state", status.label), el("div", "sub", status.when));
    box.append(icon, text);
    card.append(box);

    const [today, tomorrow] = data.days;
    card.append(this._day(today, t.today, this._minuteNow(data), t));
    if (this._config.show_tomorrow !== false && tomorrow) card.append(this._day(tomorrow, t.tomorrow, null, t));

    const foot = el("div", "foot");
    const parts = [];
    const updated = data.days.map((d) => d.updated_at).filter(Boolean).sort().pop();
    if (updated) parts.push(fill(t.updated, { time: clock(updated, language) }));
    if (data.source.name) parts.push(fill(t.source, { name: data.source.name }));
    foot.textContent = parts.join(" · ");
    card.append(foot);
    if (!data.source.ok) card.append(el("div", "foot warn", t.source_down));
  }

  _row(card, data, t, language) {
    const row = el("div", "row");
    const status = this._status(data, t, language);
    const head = el("div", `row-head ${status.state}`);
    const name = el("span", "row-name");
    name.append(el("span", "dot"), document.createTextNode(data.title));
    head.append(name, el("span", "sub", `${status.label} · ${status.when}`));
    row.append(head, this._bar(data.days[0], this._minuteNow(data), true));
    card.append(row);
  }

  _render() {
    if (!this._config) return;
    const t = strings(this._hass);
    const language = (this._hass && (this._hass.locale?.language || this._hass.language)) || "uk";
    const root = this.shadowRoot;
    root.replaceChildren();
    const style = el("style");
    style.textContent = STYLE;
    const card = document.createElement("ha-card");
    root.append(style, card);
    if (!this._devices.length) {
      card.append(el("div", "empty", t.pick));
      return;
    }
    if (this._devices.length > 1 && this._config.title) card.append(el("div", "title", this._config.title));
    for (const id of this._devices) {
      const data = this._data[id];
      if (!data) {
        if (this._errors[id]) card.append(el("div", "empty", t.missing));
        continue;
      }
      if (this._devices.length === 1) this._full(card, data, t, language);
      else this._row(card, data, t, language);
    }
  }
}

class UkraineOutagesCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config, devices: deviceIds(config) };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    const t = strings(this._hass);
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => t[schema.name] || schema.name;
      this._form.addEventListener("value-changed", (ev) => {
        const config = { ...ev.detail.value };
        delete config.device;
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.append(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
    this._form.schema = [
      { name: "devices", selector: { device: { integration: "ukraine_outages", multiple: true } } },
      { name: "title", selector: { text: {} } },
      { name: "show_tomorrow", selector: { boolean: {} } },
    ];
  }
}

if (!customElements.get(CARD)) customElements.define(CARD, UkraineOutagesCard);
if (!customElements.get(EDITOR)) customElements.define(EDITOR, UkraineOutagesCardEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  const t = STRINGS[(navigator.language || "uk").split("-")[0]] || STRINGS.uk;
  window.customCards.push({
    type: CARD,
    name: t.name,
    description: t.description,
    preview: true,
    documentationURL: "https://github.com/fastiuk/ukraine-energy-monitor#картка",
  });
}
