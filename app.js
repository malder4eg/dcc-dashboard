const state = { data: null, period: null, sortKey: "leaders", sortDir: -1 };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[ch]);
}

function fmt(value, format) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (format === "ratio2") return Number(value).toFixed(2);
  if (format === "ratio3") return Number(value).toFixed(3).replace(/^0/, "");
  if (format === "time") {
    const seconds = Math.round(Number(value));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}:${String(minutes).padStart(2, "0")}`;
  }
  return String(Math.round(Number(value)));
}

function better(cat, a, b) {
  if (a === null || a === undefined || b === null || b === undefined || a === b) return 0;
  return cat.lower ? (a < b ? 1 : -1) : (a > b ? 1 : -1);
}

function heat(cat, value, values) {
  const nums = values.filter(v => v !== null && v !== undefined).map(Number);
  if (value === null || value === undefined || nums.length < 2) return "";
  const min = Math.min(...nums), max = Math.max(...nums);
  let rank = max === min ? .5 : (Number(value) - min) / (max - min);
  if (cat.lower) rank = 1 - rank;
  const light = 97 - rank * 57;
  const sat = 18 + rank * 42;
  const text = rank > .64 ? "#fff" : "#172033";
  return `background:hsl(148 ${sat}% ${light}%);color:${text}`;
}

function currentView() {
  if (state.period !== "season") return state.data.weeks[state.period];
  const cats = state.data.categories;
  const names = [...new Set(Object.values(state.data.weeks).flatMap(w => w.teams.map(t => t.name)))].sort();
  const teams = names.map(name => {
    const stats = Object.fromEntries(cats.map(c => [c.key, 0]));
    const raw = {};
    let hasData = false;
    Object.values(state.data.weeks).forEach(week => {
      const team = week.teams.find(t => t.name === name);
      if (!team) return;
      cats.forEach(cat => {
        const value = team.stats[cat.key];
        if (value !== null && value !== undefined && !["GAA", "SV%"].includes(cat.key)) {
          stats[cat.key] += Number(value);
          hasData = true;
        }
      });
      Object.entries(team.raw || {}).forEach(([key, value]) => {
        if (value !== null && value !== undefined) raw[key] = (raw[key] || 0) + Number(value);
      });
    });
    stats.GAA = raw[8] ? (raw[4] || 0) * 3600 / raw[8] : null;
    stats["SV%"] = (raw[6] || raw[4]) ? (raw[6] || 0) / ((raw[6] || 0) + (raw[4] || 0)) : null;
    if (!hasData) cats.forEach(cat => stats[cat.key] = null);
    return { name, stats, raw, ineligible_goalies: false };
  });
  return { teams, pairs: [] };
}

function leaderCounts(teams) {
  const out = Object.fromEntries(teams.map(t => [t.name, 0]));
  state.data.categories.forEach(cat => {
    const vals = teams.map(t => t.stats[cat.key]).filter(v => v !== null && v !== undefined);
    if (!vals.length) return;
    const best = cat.lower ? Math.min(...vals) : Math.max(...vals);
    teams.forEach(t => { if (t.stats[cat.key] === best) out[t.name] += 1; });
  });
  return out;
}

function renderTabs() {
  const el = document.getElementById("week-tabs");
  const weeks = Object.keys(state.data.weeks).sort((a, b) => Number(a) - Number(b));
  const buttons = [{ key: "season", label: "Сезон" }, ...weeks.map(w => ({ key: w, label: `Неделя ${w}${Number(w) === state.data.current_week ? " · сейчас" : ""}` }))];
  el.innerHTML = buttons.map(x => `<button data-period="${x.key}" class="${state.period === x.key ? "active" : ""}">${x.label}</button>`).join("");
  el.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
    state.period = button.dataset.period;
    renderAll();
  }));
}

function renderTable() {
  const view = currentView();
  const cats = state.data.categories;
  const leaders = leaderCounts(view.teams);
  const rows = [...view.teams].sort((a, b) => {
    let av, bv;
    if (state.sortKey === "name") [av, bv] = [a.name, b.name];
    else if (state.sortKey === "leaders") [av, bv] = [leaders[a.name], leaders[b.name]];
    else [av, bv] = [a.stats[state.sortKey], b.stats[state.sortKey]];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return av.localeCompare(bv) * state.sortDir;
    return (Number(av) - Number(bv)) * state.sortDir;
  });
  const table = document.getElementById("league-table");
  table.innerHTML = `<thead><tr><th data-key="name">Команда</th>${cats.map(c => `<th data-key="${c.key}">${c.label}${c.lower ? " ↓" : ""}</th>`).join("")}<th data-key="leaders">Лучший</th></tr></thead><tbody>` + rows.map(team => {
    const warning = team.ineligible_goalies ? `<span class="goalie-warning" title="Не выполнен недельный минимум выходов вратарей"> ⚠</span>` : "";
    return `<tr><td><span class="team-name">${esc(team.name)}</span>${warning}</td>${cats.map(cat => {
      const values = view.teams.map(t => t.stats[cat.key]);
      const value = team.stats[cat.key];
      return `<td style="${heat(cat, value, values)}">${fmt(value, cat.format)}</td>`;
    }).join("")}<td class="leader-count">${leaders[team.name]}</td></tr>`;
  }).join("") + "</tbody>";
  table.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = key === "name" ? 1 : -1; }
    renderTable();
  }));
  const hasValues = view.teams.some(t => t.stats.G !== null && t.stats.G !== undefined);
  document.getElementById("week-note").textContent = state.period === "season"
    ? "Сумма всех доступных недель; GAA и SV% пересчитаны по общим GA, SV и минутам."
    : hasValues ? "Текущий срез или окончательный результат матчапа." : "ESPN ещё не насчитал статистику этой недели.";
}

function teamOptions() {
  const teams = currentView().teams.map(t => t.name).sort();
  const a = document.getElementById("team-a"), b = document.getElementById("team-b");
  const oldA = a.value, oldB = b.value;
  const html = teams.map(name => `<option>${esc(name)}</option>`).join("");
  a.innerHTML = html; b.innerHTML = html;
  a.value = teams.includes(oldA) ? oldA : (teams.find(x => /mighty ducks/i.test(x)) || teams[0]);
  b.value = teams.includes(oldB) && oldB !== a.value ? oldB : (teams.find(x => x !== a.value) || teams[0]);
}

function renderCompare() {
  const view = currentView();
  const aName = document.getElementById("team-a").value;
  const bName = document.getElementById("team-b").value;
  const a = view.teams.find(t => t.name === aName), b = view.teams.find(t => t.name === bName);
  if (!a || !b) return;
  let winsA = 0, winsB = 0, ties = 0;
  const rows = state.data.categories.map(cat => {
    const result = better(cat, a.stats[cat.key], b.stats[cat.key]);
    if (result > 0) winsA++; else if (result < 0) winsB++; else ties++;
    const av = Number(a.stats[cat.key] || 0), bv = Number(b.stats[cat.key] || 0);
    const max = Math.max(Math.abs(av), Math.abs(bv), 1);
    return `<div class="compare-row"><div class="value ${result > 0 ? "win" : ""}">${fmt(a.stats[cat.key], cat.format)}</div><div class="bar"><div class="fill ${result > 0 ? "win" : ""}" style="width:${Math.abs(av) / max * 100}%"></div></div><div class="cat">${cat.label}${cat.lower ? " ↓" : ""}</div><div class="bar right"><div class="fill ${result < 0 ? "win" : ""}" style="width:${Math.abs(bv) / max * 100}%"></div></div><div class="value right ${result < 0 ? "win" : ""}">${fmt(b.stats[cat.key], cat.format)}</div></div>`;
  }).join("");
  document.getElementById("compare").innerHTML = `<div class="scoreline"><div class="side"><strong>${esc(a.name)}</strong><b>${winsA}</b></div><span>${ties ? `${ties} нич.` : "—"}</span><div class="side"><strong>${esc(b.name)}</strong><b>${winsB}</b></div></div>${rows}`;
}

function setOpponent() {
  if (state.period === "season") state.period = String(state.data.current_week);
  const view = currentView();
  const a = document.getElementById("team-a").value;
  const pair = (view.pairs || []).find(x => x.includes(a));
  if (pair) document.getElementById("team-b").value = pair.find(x => x !== a);
  renderAll(false);
}

function renderAll(refreshOptions = true) {
  renderTabs();
  renderTable();
  if (refreshOptions) teamOptions();
  renderCompare();
}

async function init() {
  try {
    const response = await fetch(`data.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    state.period = String(state.data.current_week);
    document.title = `${state.data.league} — категорийная таблица`;
    document.getElementById("updated").textContent = state.data.updated
      ? `Обновлено ${state.data.updated}`
      : "Данные будут доступны после первой синхронизации ESPN";
    document.getElementById("team-a").addEventListener("change", renderCompare);
    document.getElementById("team-b").addEventListener("change", renderCompare);
    document.getElementById("current-opponent").addEventListener("click", setOpponent);
    renderAll();
  } catch (error) {
    document.querySelector("main").innerHTML = `<div class="error"><strong>Не удалось загрузить данные.</strong><br>${error.message}</div>`;
  }
}

init();
