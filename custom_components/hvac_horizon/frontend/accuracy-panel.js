// HVAC Horizon accuracy panel. Home Assistant loads this module as a custom panel and gives the element its
// `hass` object. Every request goes through hass.callApi, which adds the signed-in user's authorization.
// All values are written with textContent, so data from the server is never parsed as HTML.

const STYLE = `
  :host { display: block; padding: 16px; color: var(--primary-text-color); background: var(--primary-background-color); min-height: 100vh; box-sizing: border-box; font: 15px/1.4 var(--paper-font-body1_-_font-family, system-ui, sans-serif); }
  h1 { font-size: 20px; margin: 0 0 12px; font-weight: 500; }
  .muted { color: var(--secondary-text-color); }
  table { border-collapse: collapse; width: 100%; max-width: 640px; margin-top: 12px; }
  th, td { padding: 6px 8px; border-bottom: 1px solid var(--divider-color); text-align: right; }
  th:first-child, td:first-child { text-align: left; }
  button { font: inherit; color: var(--primary-color); background: none; border: 0; padding: 4px 0; cursor: pointer; text-decoration: underline; }
  ul { padding-left: 20px; }
`;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function reason(error) {
  return error && error.message ? error.message : "error";
}

class HvacHorizonAccuracy extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._entryId = null;
    this._started = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._start();
  }

  set panel(panel) {
    const ids = (panel && panel.config && panel.config.entry_ids) || [];
    this._entryId = ids.length === 1 ? ids[0] : null;
    this._start();
  }

  _start() {
    if (this._started || !this._hass) return;
    this._started = true;
    if (this._entryId) this._showEntry(this._entryId, false);
    else this._showList();
  }

  _frame(statusText) {
    const root = this.shadowRoot;
    root.replaceChildren();
    root.appendChild(element("style", STYLE));
    root.appendChild(element("h1", "Forecast accuracy"));
    const status = element("p", statusText, "muted");
    root.appendChild(status);
    return status;
  }

  async _showList() {
    const status = this._frame("Loading.");
    try {
      const entries = await this._hass.callApi("GET", "hvac_horizon/entries");
      if (!entries.length) {
        status.textContent = "No HVAC Horizon entry is loaded.";
        return;
      }
      status.textContent = "Pick an entry to see its forecast accuracy.";
      const list = element("ul");
      for (const entry of entries) {
        const item = element("li");
        const button = element("button", entry.title);
        button.addEventListener("click", () => this._showEntry(entry.entry_id, true));
        item.appendChild(button);
        list.appendChild(item);
      }
      this.shadowRoot.appendChild(list);
    } catch (error) {
      status.textContent = "Could not load the entry list (" + reason(error) + ").";
    }
  }

  async _showEntry(entryId, fromList) {
    const status = this._frame("Loading.");
    try {
      const body = await this._hass.callApi("GET", "hvac_horizon/accuracy/" + encodeURIComponent(entryId));
      const pairs = body.accuracy_pairs || [];
      status.textContent = pairs.length ? "Last " + pairs.length + " backtest runs, oldest first." : "No backtest runs yet.";
      const median = body.median_abs_error_min;
      const line = element("p", "Median absolute error: ");
      line.appendChild(element("strong", median === null || median === undefined ? "-" : Number(median).toFixed(1)));
      line.appendChild(document.createTextNode(" min"));
      this.shadowRoot.appendChild(line);
      if (body.range_runs) {
        const share = Number(body.range_coverage_pct).toFixed(0);
        this.shadowRoot.appendChild(element("p", "Measured time inside the forecast range: " + share + " % of " + body.range_runs + " runs."));
      }
      const table = element("table");
      const thead = element("thead");
      const head = element("tr");
      for (const title of ["Run", "Predicted, min", "Measured, min", "Error, min"]) head.appendChild(element("th", title));
      thead.appendChild(head);
      table.appendChild(thead);
      const rows = element("tbody");
      pairs.forEach((pair, index) => {
        const row = element("tr");
        row.appendChild(element("td", String(index + 1)));
        for (const key of ["predicted_min", "measured_min", "error_min"]) row.appendChild(element("td", Number(pair[key]).toFixed(1)));
        rows.appendChild(row);
      });
      table.appendChild(rows);
      this.shadowRoot.appendChild(table);
      if (fromList) {
        const back = element("button", "All entries");
        back.addEventListener("click", () => this._showList());
        this.shadowRoot.appendChild(back);
      }
    } catch (error) {
      status.textContent = "Could not load the accuracy data (" + reason(error) + ").";
    }
  }
}

customElements.define("hvac-horizon-accuracy", HvacHorizonAccuracy);
