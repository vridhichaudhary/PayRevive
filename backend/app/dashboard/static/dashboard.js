"use strict";
const params = new URLSearchParams(location.search);
const org = params.get("org") || "00000000-0000-0000-0000-000000000001";
const base = `/v1/organizations/${org}`;
const wb = `${base}/workbench`;
const el = (id) => document.getElementById(id);
let offset = 0, incident = null, sequence = 0;
const money = (v) => {
  const n = BigInt(v), sign = n < 0n ? "-" : "", a = n < 0n ? -n : n;
  return `${sign}₹${(a/100n).toLocaleString("en-IN")}.${String(a%100n).padStart(2,"0")}`;
};
const label = (v) => String(v ?? "Unavailable").replaceAll("_"," ");
function node(tag, text, css) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (css) n.className = css;
  return n;
}
function link(text, href) { const n = node("a",text); n.href=href; return n; }
function button(text, action) { const n = node("button",text); n.onclick=action; return n; }
async function api(path, method="GET") {
  const r = await fetch(path,{method,cache:"no-store"});
  if (!r.ok) { let b = await r.json().catch(()=>({})); throw Error(b.detail || `HTTP ${r.status}`); }
  return r.json();
}
function stamp(data) {
  return node("p",`Source ${data.source.dataset_id} · report file updated ${data.generated_at || "unavailable"} · ${data.kind}`,"subtle");
}
async function timeline(id) {
  const d=await api(`${wb}/cases/${id}`), root=el("case-body");
  const content=node("div");
  const head=node("div",null,"panel");
  head.append(node("h2",d.reference),node("strong",money(d.amount_minor)),node("p",`Payment status · ${label(d.status)}`));
  head.append(link("Shareable case link",`?org=${org}#case/${id}`));
  const latest=d.recovery_decisions.at(-1);
  head.append(node("h3","Recovery decision"),node("p",latest ? `${label(latest.proposal.kind)} · ${label(latest.proposal.reason)} · ${label(latest.eligibility.reason)}` : "No recovery decision recorded"));
  const pending=d.recovery_actions.find(x=>x.status==="awaiting_approval");
  if(pending) {
    head.append(node("p",`Awaiting approval · due ${pending.due_at}`));
    const approve=button("Approve simulator retry",async()=>{
      if(!confirm(`Approve simulator retry for ${d.reference} (${money(d.amount_minor)})?`)) return;
      approve.disabled=true;
      try {
        const a=await api(`${base}/recovery/actions/${pending.id}/approve`,"POST");
        el("message").textContent=`Saved action status · ${label(a.status)}. Refresh to see the worker outcome.`;
        await timeline(id);
      } catch(e) { el("message").textContent=e.message; approve.disabled=false; }
    });
    head.append(approve);
  } else head.append(node("p",`Latest action · ${label(d.recovery_actions.at(-1)?.status)}`));
  head.append(node("p","Eligibility uses recorded evidence and consent. Simulator execution is independently checked.","subtle"));
  content.append(head);
  const list=node("div",null,"panel");
  list.append(node("h2","Case timeline"));
  for(const item of d.timeline) {
    const r=item.record, entry=node("article",null,"record");
    entry.append(node("small",new Date(item.at).toISOString()),node("h3",label(item.type)),
      node("p",label(r.kind || r.status || r.result || r.proposal?.kind)));
    if(r.reason) entry.append(node("p",label(r.reason)));
    const details=node("details");
    details.append(node("summary","Stored evidence"),node("pre",JSON.stringify(r,null,2)));
    entry.append(details); list.append(entry);
  }
  content.append(list); root.replaceChildren(content);
}
async function load() {
  const request=++sequence, route=location.hash.slice(1)||"overview";
  const view=route.startsWith("case/")?"case":route;
  for(const n of document.querySelectorAll(".view")) n.hidden=n.id!==view;
  el("title").textContent=({overview:"Payment overview",queue:"Recovery queue",incidents:"Incident exposures",strategy:"Strategy Lab",case:"Payment case"})[view]||"Page not found";
  el("message").textContent="Loading current data…"; document.querySelector("main").setAttribute("aria-busy","true");
  try {
    if(view==="case") await timeline(route.split("/")[1]);
    if(view==="overview") {
      const d=await api(`${base}/dashboard`);
      if(request!==sequence)return;
      el("metrics").replaceChildren(...["failed","succeeded","pending"].map(k=>{
        const x=d.summary[k]||{count:0,amount_minor:"0"}, c=node("article");
        c.append(node("p",`${label(k)} amount`),node("strong",money(x.amount_minor)),node("small",`${x.count} stored payments`));return c;
      }));
    }
    if(view==="queue") {
      const q=new URLSearchParams({search:el("search").value,status:el("status").value,action_status:el("action").value,offset,limit:20});
      if(incident)q.set("incident_id",incident);
      const d=await api(`${wb}/cases?${q}`);if(request!==sequence)return;
      el("exposure").replaceChildren();
      if(incident)el("exposure").append(node("span",`Incident ${incident} · exposure match `),button("Clear exposure",()=>{incident=null;offset=0;load();}));
      el("scope").textContent=`${d.count} matching payments · ${money(d.amount_minor)} · as of ${d.as_of}`;
      el("rows").replaceChildren(...d.cases.map(p=>{
        const tr=node("tr"),td=node("td");td.append(link(p.reference,`#case/${p.id}`));
        tr.append(td,node("td",p.status),node("td",money(p.amount_minor)));return tr;
      }));
      if(!d.cases.length)el("rows").append(node("tr","No payments match these filters."));
      el("next").disabled=!d.has_more;el("previous").disabled=offset===0;el("page").textContent=`Page ${offset/20+1}`;
    }
    if(view==="incidents"){
      const d=await api(`${wb}/incidents`);if(request!==sequence)return;
      el("incident-list").replaceChildren(stamp(d));
      for(const r of d.incidents) {
        const c=node("article",null,"panel");
        c.append(node("h2",`${label(r.scope)} / ${r.target}`),node("p",`${r.starts_at} → ${r.ends_at}`),
          node("p",r.first_alert_at ? `First replay alert ${r.first_alert_at}` : "Guided fixture or no replay alert recorded"),
          button("Investigate exposed subscriptions",()=>{incident=r.incident_id;offset=0;location.hash="queue";}));
        el("incident-list").append(c);
      }
      if(!d.incidents.length)el("incident-list").append(node("p","No incident windows in this report."));
    }
    if(view==="strategy"){
      const d=await api("/dashboard/reports/experiment");
      el("strategy-body").replaceChildren(node("p",`${d.config.runs} matched seeds · ${d.config.customers} customers per cohort · ${d.config.horizon_hours} hour horizon`));
      for(const r of d.contrasts.filter(x=>x.metric==="net_incremental_minor")){
        const c=node("article",null,"record");
        c.append(node("h3",`${label(r.policy)} vs ${label(r.comparison)}`),
          node("strong",`INR ${(r.mean_difference_paise_per_cohort/100).toLocaleString("en-IN",{maximumFractionDigits:2})}`),
          node("p",`95% seed-bootstrap interval INR ${(r.ci95_low_paise/100).toFixed(2)} to ${(r.ci95_high_paise/100).toFixed(2)}`));el("strategy-body").append(c);
      }
    }
    if(request===sequence)el("message").textContent="Updated from stored data.";
  } catch(e) {
    if(request===sequence)el("message").textContent=`Unable to refresh. Any visible previous data is stale. ${e.message}`;
  } finally {if(request===sequence)document.querySelector("main").setAttribute("aria-busy","false");}
}
el("filters").onsubmit=e=>{e.preventDefault();offset=0;load();};
el("previous").onclick=()=>{offset=Math.max(0,offset-20);load();};
el("next").onclick=()=>{offset+=20;load();};
el("refresh").onclick=load;
addEventListener("hashchange",load);
load();
