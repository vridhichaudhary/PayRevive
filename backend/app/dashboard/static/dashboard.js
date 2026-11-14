"use strict";

const org = "00000000-0000-0000-0000-000000000001";
const base = `/v1/organizations/${org}`;
const size = 20;
let offset = 0;
let requestSequence = 0;
let detailSequence = 0;
const byId = (id) => document.getElementById(id);
const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const money = (minor) => {
  if (typeof minor !== "string") return currency.format(minor / 100);
  const value = BigInt(minor);
  const parts = currency.formatToParts(value / 100n);
  return parts.map(({ type, value: text }) =>
    type === "fraction" ? String(value % 100n).padStart(2, "0") : text).join("");
};
const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const date = (value) => value ? new Date(value).toISOString().slice(0, 16).replace("T", " ") : "—";
const label = (value) => String(value ?? "Unknown").replaceAll("_", " ");

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value != null) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function button(value, click, className) {
  const node = element("button", value, className);
  node.type = "button";
  node.addEventListener("click", click);
  return node;
}

async function fetchJSON(path, method = "GET") {
  const response = await fetch(path, { method, cache: "no-store" });
  if (!response.ok) {
    const error = new Error(`Request failed (HTTP ${response.status})`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function facts(values) {
  const list = element("dl");
  for (const [key, value] of values) list.append(element("dt", key), element("dd", value));
  return list;
}

function trace(title, values, describe) {
  const group = element("details");
  group.append(element("summary", `${title} (${values.length})`));
  for (const item of values) {
    const entry = element("div", null, "record");
    entry.append(element("div", describe(item)));
    const raw = element("details");
    raw.append(element("summary", "Record fields"), element("pre", JSON.stringify(item, null, 2)));
    entry.append(raw);
    group.append(entry);
  }
  return group;
}

async function timeline(id) {
  const sequence = ++detailSequence;
  const section = byId("detail");
  const body = byId("detail-body");
  section.hidden = false;
  body.textContent = "Loading payment…";
  section.scrollIntoView({ block: "start" });
  try {
    const data = await fetchJSON(`${base}/payments/${id}`);
    if (sequence !== detailSequence) return;
    body.replaceChildren(
      element("h3", data.reference),
      facts([["Status", label(data.status)], ["Amount", money(data.amount_minor)]]),
      trace("Attempts", data.attempts, (x) => `Attempt ${x.number} · ${label(x.status)}`),
      trace("Events", data.events, (x) => `${date(x.occurred_at)} UTC · ${label(x.status)} · ${label(x.reason)}`),
      trace("Feature snapshots", data.feature_snapshots, (x) => `Dataset ${x.source_dataset_id}`),
      trace("Recovery decisions", data.recovery_decisions, (x) => `${label(x.proposal.kind)} · ${label(x.eligibility.reason)}`),
      trace("Recovery actions", data.recovery_actions, (x) => label(x.status)),
      trace("Recovery outcomes", data.recovery_outcomes, (x) => `Retry cost ${money(x.cost_minor)}`),
      trace("Audit trail", data.audit, (x) => `${date(x.created_at)} UTC · ${x.kind}`),
    );
    body.querySelector("details").open = true;
  } catch (error) {
    if (sequence === detailSequence) body.textContent = error.message;
  }
}

function approvals(rows) {
  const target = byId("approval-rows");
  target.replaceChildren();
  if (!rows.length) target.append(element("p", "No actions awaiting approval.", "subtle"));
  for (const row of rows) {
    const node = element("div", null, "record section-head");
    const open = button(row.reference, () => timeline(row.payment_id), "link");
    const approve = button("Approve simulator retry", async () => {
      if (!window.confirm(`Approve the simulator retry for ${row.reference}?`)) return;
      approve.disabled = true;
      try {
        const result = await fetchJSON(`${base}/recovery/actions/${row.id}/approve`, "POST");
        byId("action-message").textContent = `Action status: ${label(result.status)}`;
        await overview();
        await timeline(row.payment_id);
      } catch (error) {
        byId("action-message").textContent = error.message;
      } finally {
        approve.disabled = false;
      }
    });
    node.append(open, element("span", `Due ${date(row.due_at)} UTC`, "subtle"), approve);
    target.append(node);
  }
}

async function overview() {
  const sequence = ++requestSequence;
  const status = byId("status").value;
  try {
    const data = await fetchJSON(`${base}/dashboard?status=${status}&limit=${size}&offset=${offset}`);
    if (sequence !== requestSequence) return;
    byId("connection").textContent = "Connected to the local payment database.";
    for (const [statusKey, key] of [["failed", "risk"], ["succeeded", "settled"], ["pending", "pending"]]) {
      const record = data.summary[statusKey];
      byId(key).textContent = money(record?.amount_minor ?? 0);
      byId(`${key}-count`).textContent = `${record?.count ?? 0} payments`;
    }
    byId("payment-rows").replaceChildren(...data.payments.map((payment) => {
      const tr = element("tr");
      const reference = element("td");
      reference.append(button(payment.reference, () => timeline(payment.id), "link"));
      const state = element("td");
      state.append(element("span", label(payment.status), `badge ${payment.status}`));
      tr.append(reference, state, element("td", money(payment.amount_minor)), element("td", date(payment.created_at)));
      return tr;
    }));
    byId("payment-empty").hidden = data.payments.length > 0;
    byId("payment-empty").textContent = status === "all" && offset === 0 ?
      "No payments yet. Run the dashboard seed command from the setup guide." : "No payments on this page.";
    byId("previous").disabled = offset === 0;
    byId("next").disabled = !data.has_more;
    byId("page").textContent = `Page ${Math.floor(offset / size) + 1}`;
    approvals(data.pending_approvals);
  } catch (error) {
    if (sequence === requestSequence) byId("connection").textContent = error.message;
  }
}

function table(columns, rows) {
  const wrapper = element("div", null, "scroll");
  const tableNode = element("table");
  const header = element("tr");
  for (const column of columns) {
    const cell = element("th", column);
    cell.scope = "col";
    header.append(cell);
  }
  const head = element("thead");
  head.append(header);
  const body = element("tbody");
  for (const row of rows) {
    const tr = element("tr");
    tr.append(...row.map((value) => element("td", value)));
    body.append(tr);
  }
  tableNode.append(head, body);
  wrapper.append(tableNode);
  return wrapper;
}

function replayCard(kind, data, target) {
  if (kind === "detection") {
    const evaluation = data.evaluation;
    target.append(
      element("p", `Dataset ${data.provenance.dataset_id} · ${evaluation.eligible_windows} eligible cohort days`, "subtle"),
      table(["Rule", "Incidents caught", "False alerts"], Object.entries(evaluation.methods).map(([name, row]) =>
        [label(name), `${row.incidents_detected}/${evaluation.incident_count}`, row.false_alert_windows])),
    );
    const rows = evaluation.methods.rolling.incidents;
    if (rows.length) target.append(table(["Scope", "Target", "First alert UTC"], rows.map((row) =>
      [row.scope, row.target, date(row.first_alert_at)])));
  } else if (kind === "diagnosis") {
    const model = data.selected_model;
    const example = data.shap.example;
    target.append(
      element("p", `Dataset ${data.provenance.dataset_id} · test month ${data.months.test.slice(0, 7)}`, "subtle"),
      facts([["Selected model", label(model)], ["Macro F1", data.test[model].macro_f1.toFixed(3)],
        ["Accepted accuracy", pct(data.test_selective.accepted_accuracy)], ["Ambiguous coverage", pct(data.test_selective.coverage)]]),
      element("h3", `Example: ${label(example.predicted_class)}`),
      element("p", `Model probability ${pct(example.probability)} · training background ${pct(example.base_value)}`, "subtle"),
      facts(Object.entries(example.contributions).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 3)
        .map(([name, value]) => [label(name), `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)} pp`])),
    );
  } else {
    target.append(
      element("p", `${data.config.runs} matched seeds · ${data.config.customers} customers per cohort · ${data.config.horizon_hours}h horizon`, "subtle"),
      table(["Comparison", "Mean net difference", "95% interval"], data.contrasts.filter((row) =>
        row.metric === "net_incremental_minor").map((row) => [
        `${label(row.policy)} vs ${label(row.comparison)}`,
        money(row.mean_difference_paise_per_cohort),
        `${money(row.ci95_low_paise)} to ${money(row.ci95_high_paise)}`,
      ])),
    );
  }
}

async function loadReport(kind) {
  const target = byId(kind);
  target.textContent = "Loading report…";
  try {
    const data = await fetchJSON(`/dashboard/reports/${kind}`);
    target.replaceChildren();
    replayCard(kind, data, target);
  } catch (error) {
    target.textContent = error.status === 404 ? "Report not generated yet. See the setup guide." : "Report unavailable. Check the generated report and refresh.";
  }
}

async function loadRules() {
  const target = byId("rules");
  try {
    const data = await fetchJSON(`${base}/recovery/policy`);
    target.replaceChildren(
      element("p", data.version, "subtle"),
      facts(Object.entries(data.rules).map(([name, value]) => [label(name), value])),
    );
  } catch (error) {
    target.textContent = error.message;
  }
}

function refresh() {
  overview();
  loadRules();
  for (const kind of ["detection", "diagnosis", "experiment"]) loadReport(kind);
}

byId("status").addEventListener("change", () => { offset = 0; overview(); });
byId("previous").addEventListener("click", () => { offset = Math.max(0, offset - size); overview(); });
byId("next").addEventListener("click", () => { offset += size; overview(); });
byId("refresh").addEventListener("click", refresh);
byId("close-detail").addEventListener("click", () => { ++detailSequence; byId("detail").hidden = true; });
refresh();
