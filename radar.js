/* NHL Fantasy Radar: public NHL API signals + ESPN DCC availability. */
(() => {
  const state = {
    data: null,
    availability: "available",
    position: "",
    sortKey: "score",
    sortDir: -1,
  };

  const $ = id => document.getElementById(id);

  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[c]);

  const sec = value => {
    if (value == null || !Number.isFinite(Number(value))) return "–";
    const total = Math.round(Number(value));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  };

  const num = value => value == null ? "–" : Number(value).toFixed(1).replace(/\.0$/, "");

  function matchesAvailability(row) {
    if (state.availability === "all") return true;
    if (state.availability === "available") {
      return ["FREEAGENT", "WAIVERS"].includes(row.espn_status);
    }
    return row.espn_status === state.availability;
  }

  function rows() {
    const list = state.data.signals.filter(row => {
      const availability = matchesAvailability(row);
      const position = !state.position || row.position === state.position;
      return availability && position;
    });

    const rank = {HIGH: 3, MEDIUM: 2, LOW: 1};
    return list.sort((a, b) => {
      let av;
      let bv;
      if (state.sortKey === "name") [av, bv] = [a.name, b.name];
      else if (state.sortKey === "team") [av, bv] = [a.nhl_team, b.nhl_team];
      else if (state.sortKey === "reliability") [av, bv] = [rank[a.reliability] || 0, rank[b.reliability] || 0];
      else [av, bv] = [Number(a[state.sortKey] ?? 0), Number(b[state.sortKey] ?? 0)];

      if (typeof av === "string") return av.localeCompare(bv) * state.sortDir;
      return (Number(av) - Number(bv)) * state.sortDir;
    });
  }

  function statusLabel(value) {
    if (value === "FREEAGENT") return "FA";
    if (value === "WAIVERS") return "WAIVERS";
    if (value === "ONTEAM") return "ONTEAM";
    return value || "UNKNOWN";
  }

  function renderNewSignals() {
    const box = $("radar-new");
    const items = state.data.new_signals || [];
    if (!items.length) {
      box.innerHTML = `<p class="note radar-empty">Новых сигналов с прошлого обновления нет.</p>`;
      return;
    }

    box.innerHTML = items.slice(0, 8).map(row => `
      <div class="radar-new-card">
        <strong>${esc(row.name)}</strong>
        <span>${esc(row.nhl_team || "–")} · ${esc(statusLabel(row.espn_status))}</span>
        <span>${esc((row.new_signal_types || []).join(", "))}</span>
        <b>${esc(row.recommendation)} · ${row.score}</b>
      </div>
    `).join("");
  }

  function renderTable() {
    const list = rows();
    const table = $("radar-table");

    table.innerHTML = `
      <thead>
        <tr>
          <th data-radar-sort="name">Игрок</th>
          <th data-radar-sort="team">NHL</th>
          <th>Поз.</th>
          <th>ESPN</th>
          <th data-radar-sort="score">Score</th>
          <th>TOI: было → сейчас</th>
          <th>PP / SH TOI</th>
          <th>SOG / HIT / BLK / FOW</th>
          <th>Сигнал</th>
          <th data-radar-sort="reliability">Надёжность</th>
          <th>Рекомендация</th>
          <th>Свежесть</th>
        </tr>
      </thead>
      <tbody>
        ${list.map(row => {
          const m = row.metrics || {};
          const signals = (row.signals || []).map(s => s.label).join(" · ");
          const cats = (row.categories || []).join(", ");
          const context = (row.team_context || []).slice(0, 2)
            .map(e => `${e.type}: ${e.player}`).join("; ");
          const title = [
            row.role_outlook,
            cats ? `Категории DCC: ${cats}` : "",
            context ? `Контекст состава: ${context}` : "",
          ].filter(Boolean).join(" | ");

          return `
            <tr>
              <td><span class="team-name" title="${esc(title)}">${esc(row.name)}</span></td>
              <td>${esc(row.nhl_team || "–")}</td>
              <td>${esc(row.position || "–")}</td>
              <td>${esc(statusLabel(row.espn_status))}</td>
              <td class="radar-score">${row.score}</td>
              <td>${sec(m.toi?.baseline)} → <strong>${sec(m.toi?.recent)}</strong></td>
              <td>PP ${sec(m.pp_toi?.recent)} · SH ${sec(m.sh_toi?.recent)}</td>
              <td>${num(m.sog?.recent)} / ${num(m.hit?.recent)} / ${num(m.blk?.recent)} / ${num(m.fow?.recent)}</td>
              <td class="radar-signal">${esc(signals)}</td>
              <td><span class="radar-confidence ${String(row.reliability || "").toLowerCase()}">${esc(row.reliability)}</span></td>
              <td><span class="radar-rec ${row.recommendation === "ADD CANDIDATE" ? "add" : "watch"}">${esc(row.recommendation)}</span></td>
              <td>${esc(row.latest_final_game_date || "–")}</td>
            </tr>
          `;
        }).join("")}
      </tbody>
    `;

    table.querySelectorAll("[data-radar-sort]").forEach(th => {
      th.addEventListener("click", () => {
        const key = th.dataset.radarSort;
        if (state.sortKey === key) state.sortDir *= -1;
        else {
          state.sortKey = key;
          state.sortDir = key === "name" || key === "team" ? 1 : -1;
        }
        renderTable();
      });
    });

    $("radar-count").textContent = `${list.length} игроков`;
  }

  function render() {
    $("radar-note").textContent =
      `${state.data.updated} · последняя финальная игра: ${state.data.latest_final_game_date || "нет данных"} · ` +
      `${state.data.counts?.signals_available ?? 0} доступных сигналов`;
    renderNewSignals();
    renderTable();
  }

  async function init() {
    try {
      const response = await fetch(`nhl/radar.json?v=${Date.now()}`, {cache: "no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = await response.json();

      $("radar-availability").addEventListener("change", event => {
        state.availability = event.target.value;
        renderTable();
      });
      $("radar-position").addEventListener("change", event => {
        state.position = event.target.value;
        renderTable();
      });

      render();
    } catch (error) {
      $("radar-note").textContent =
        `NHL Fantasy Radar пока недоступен: ${error.message}. Основной ESPN Dashboard продолжает работать.`;
      $("radar-new").innerHTML = "";
      $("radar-table").innerHTML = "";
      $("radar-count").textContent = "";
    }
  }

  init();
})();
