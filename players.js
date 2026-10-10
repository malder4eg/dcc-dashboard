/* Independent roster view: the original category table works without it. */
(() => {
  let league, players, page = 0;
  const size = 50;
  const $ = id => document.getElementById(id);
  const detailCache = new Map();
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[c]);
  async function read(path) {
    const response = await fetch(`${path}?v=${Date.now()}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function render() {
    const teamId = $("roster-team").value;
    const position = $("player-position").value;
    const search = $("player-search").value.trim().toLowerCase();
    const team = league.teams.find(t => String(t.id) === teamId);
    const slots = new Map((team?.roster || []).map(r => [r.player_id, r.slot]));
    const list = players.filter(p => {
      const available = ["FREEAGENT", "WAIVERS"].includes(p.availability) || p.availability == null;
      const group = teamId === "available" ? available :
        teamId === "waivers" ? p.availability === "WAIVERS" : String(p.on_team_id) === teamId;
      return group && (!position || p.positions.includes(position)) &&
        (!search || p.name.toLowerCase().includes(search));
    }).sort((a, b) => team ? a.name.localeCompare(b.name) : (b.percent_owned || 0) - (a.percent_owned || 0));
    page = Math.min(page, Math.max(0, Math.ceil(list.length / size) - 1));
    const proTeams = new Map(league.pro_teams.map(t => [t.id, t.abbrev || t.name]));
    $("roster-table").innerHTML = `<thead><tr><th>Игрок</th><th>NHL</th><th>Позиции</th><th>Слот / доступность</th><th>Травма</th><th>% owned</th></tr></thead><tbody>` +
      list.slice(page * size, (page + 1) * size).map(p => `<tr>
        <td><button class="player-name" data-player="${p.id}">${escape(p.name)}</button></td>
        <td>${escape(proTeams.get(p.pro_team_id) || p.pro_team_id || "-")}</td>
        <td>${escape(p.positions.join(" / "))}</td>
        <td>${escape(slots.get(p.id) || (p.availability === "FREEAGENT" ? "FA" : p.availability || "FA / WAIVERS"))}</td>
        <td>${escape(p.injury_status || "-")}</td>
        <td>${p.percent_owned == null ? "-" : Number(p.percent_owned).toFixed(1)}</td>
      </tr>`).join("") + `</tbody>`;
    $("players-count").textContent = list.length ? `${page * size + 1}-${Math.min((page + 1) * size, list.length)} из ${list.length}` : "Игроков не найдено";
    $("players-prev").disabled = page === 0;
    $("players-next").disabled = (page + 1) * size >= list.length;
  }
  async function showPlayer(id) {
    const summary = players.find(p => p.id === id);
    const dialog = document.createElement("dialog");
    dialog.className = "player-detail";
    dialog.innerHTML = `<button class="secondary close-dialog">Закрыть</button><h2>${escape(summary.name)}</h2><div class="detail-body">Загрузка статистики…</div>`;
    document.body.append(dialog);
    dialog.querySelector(".close-dialog").onclick = () => dialog.close();
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
    try {
      if (!detailCache.has(summary.detail_file)) detailCache.set(summary.detail_file, read(summary.detail_file));
      const chunk = await detailCache.get(summary.detail_file);
      const p = chunk.players.find(p => p.id === id);
      if (!p) throw new Error("Состав изменился. Обновите страницу.");
      const cats = ["G", "A", "PTS", "PPP", "SHP", "FOW", "TOI", "SOG", "HIT", "BLK", "W", "SV", "SO", "GAA", "SV%"];
      const splits = {0: "Сезон", 1: "7 дней", 2: "15 дней", 3: "30 дней"};
      dialog.querySelector(".detail-body").innerHTML = `<p class="note">${escape(chunk.updated)}. TOI в минутах. Пустое значение - нет данных.</p>` +
        `<div class="table-card"><table><thead><tr><th>Период</th>${cats.map(c => `<th>${escape(c)}</th>`).join("")}</tr></thead><tbody>` +
        p.stats.map(s => `<tr><td>${escape(`${s.seasonId || s.externalId || ""} · ${splits[s.statSplitTypeId] || s.id} · ${s.statSourceId === 1 ? "прогноз" : s.statSourceId === 0 ? "факт" : "источник " + s.statSourceId}`)}</td>` +
          cats.map(c => {const v = s.categories[c]; return `<td>${v == null ? "-" : c === "TOI" ? (v / 60).toFixed(1) : ["GAA", "SV%"].includes(c) ? Number(v).toFixed(c === "GAA" ? 2 : 3) : escape(v)}</td>`;}).join("") + `</tr>`).join("") +
        `</tbody></table></div><p class="note"><a href="${escape(summary.detail_file)}">Исходные данные ESPN</a></p>`;
    } catch (error) {
      dialog.querySelector(".detail-body").textContent = `Не удалось загрузить статистику: ${error.message}`;
    }
  }
  async function init() {
    try {
      league = await read("analysis.json");
      const index = await read(league.player_index_file);
      if (index.updated !== league.updated) throw new Error("Идёт обновление. Обновите страницу через минуту.");
      players = index.players;
      $("roster-note").textContent = `${league.updated} · ${league.teams.length} команд · ${league.available_count} FA/waivers`;
      $("roster-team").innerHTML = league.teams.map(t => `<option value="${t.id}">${escape(t.name)}</option>`).join("") +
        `<option value="available">Все FA / waivers</option><option value="waivers">Только waivers</option>`;
      $("roster-team").value = String(league.teams.find(t => t.name === "Mighty Ducks")?.id || league.teams[0].id);
      for (const id of ["roster-team", "player-position", "player-search"]) $(id).addEventListener(id === "player-search" ? "input" : "change", () => {page = 0; render();});
      $("players-prev").onclick = () => {page--; render();};
      $("players-next").onclick = () => {page++; render();};
      $("roster-table").onclick = event => {const button = event.target.closest("[data-player]"); if (button) showPlayer(Number(button.dataset.player));};
      render();
    } catch (error) {
      $("roster-note").textContent = `Составы пока недоступны: ${error.message}`;
    }
  }
  init();
})();
