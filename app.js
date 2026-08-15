const state = {
  data: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  selectedDay: "all",
  selectedType: "all",
};

const COMMON_TIMEZONES = [
  "America/Los_Angeles","America/Denver","America/Chicago","America/New_York",
  "America/Phoenix","Pacific/Honolulu","America/Anchorage","Europe/London",
  "Europe/Paris","Asia/Tokyo","Australia/Sydney","UTC"
];

const $ = (id) => document.getElementById(id);
const el = {
  timezoneSelect:$("timezoneSelect"), tournamentName:$("tournamentName"),
  tournamentMeta:$("tournamentMeta"), lastUpdated:$("lastUpdated"),
  todaySummary:$("todaySummary"), liveSummary:$("liveSummary"),
  nextSummary:$("nextSummary"), dayTabs:$("dayTabs"), schedule:$("schedule"),
  statusBanner:$("statusBanner"), template:$("coverageTemplate"),
  refreshButton:$("refreshButton"), collectionStatus:$("collectionStatus"),
  sourceList:$("sourceList"),
};

async function loadSchedule() {
  try {
    const response = await fetch("/api/schedule", {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();

    // Default to the visitor's current local day when the schedule contains it.
    // This makes Saturday visitors land on Saturday instead of "All days".
    selectCurrentLocalDay();

    renderAll();
  } catch (error) {
    showStatus(`Could not load schedule: ${error.message}`);
  }
}

function showStatus(message) {
  el.statusBanner.textContent = message;
  el.statusBanner.hidden = false;
}

function initTimezonePicker() {
  [...new Set([state.timezone, ...COMMON_TIMEZONES])].forEach(zone => {
    const option = document.createElement("option");
    option.value = zone;
    option.textContent = zone.replaceAll("_"," ").replace("/", " / ");
    option.selected = zone === state.timezone;
    el.timezoneSelect.appendChild(option);
  });
  el.timezoneSelect.addEventListener("change", () => {
    state.timezone = el.timezoneSelect.value;
    selectCurrentLocalDay();
    renderAll();
  });
}

function selectCurrentLocalDay() {
  const today = localDayKey(new Date());
  const availableDays = new Set(coverage().map(item => localDayKey(item.start)));

  if (availableDays.has(today)) {
    state.selectedDay = today;
  } else {
    state.selectedDay = "all";
  }
}

function renderAll() {
  if (!state.data) return;
  renderHeader();
  renderDayTabs();
  renderSummary();
  renderSchedule();
  renderSources();
}

function renderHeader() {
  const t = state.data.tournament || {};
  el.tournamentName.textContent = t.name || "No tournament found";
  const dates = t.startDate && t.endDate ? `${t.startDate} – ${t.endDate}` : "";
  el.tournamentMeta.textContent = [t.course, t.location, dates].filter(Boolean).join(" • ");
  el.lastUpdated.textContent = state.data.lastUpdatedUtc
    ? formatDateTime(new Date(state.data.lastUpdatedUtc))
    : "Not yet";
}

function renderSummary() {
  const now = new Date();
  const all = coverage().sort((a,b) => a.start-b.start);
  const live = all.filter(x => getState(x, now) === "live");
  const upcoming = all.filter(x => x.start > now);
  const today = all.filter(x => localDayKey(x.start) === localDayKey(now));

  el.todaySummary.textContent = today.length ? `${today.length} coverage windows` : "No scheduled coverage";
  el.liveSummary.textContent = live.length
    ? live.map(item => item.provider).join(" + ")
    : "Nothing live";
  el.nextSummary.textContent = upcoming.length
    ? `${upcoming[0].provider} ${formatTime(upcoming[0].start)}`
    : "No upcoming coverage";
}

function renderDayTabs() {
  const days = [...new Set(coverage().map(x => localDayKey(x.start)))].sort();
  el.dayTabs.innerHTML = "";
  el.dayTabs.appendChild(dayButton("All days","all"));

  days.forEach(day => {
    const item = coverage().find(x => localDayKey(x.start) === day);
    const label = new Intl.DateTimeFormat("en-US", {
      weekday:"short", month:"short", day:"numeric", timeZone:state.timezone
    }).format(item.start);
    el.dayTabs.appendChild(dayButton(label, day));
  });
}

function dayButton(label, value) {
  const button = document.createElement("button");
  button.className = `day-tab ${state.selectedDay === value ? "active" : ""}`;
  button.textContent = label;
  button.onclick = () => {
    state.selectedDay = value;
    renderDayTabs();
    renderSchedule();
  };
  return button;
}

document.querySelectorAll(".filter-chip").forEach(button => {
  button.onclick = () => {
    document.querySelectorAll(".filter-chip").forEach(b => b.classList.remove("active"));
    button.classList.add("active");
    state.selectedType = button.dataset.filter;
    renderSchedule();
  };
});

function coverage() {
  return (state.data?.coverage || []).map(item => ({
    ...item,
    start:new Date(item.startUtc),
    end:new Date(item.endUtc),
  }));
}

function renderSchedule() {
  el.schedule.innerHTML = "";
  const items = coverage()
    .filter(x => state.selectedType === "all" || x.type === state.selectedType)
    .filter(x => state.selectedDay === "all" || localDayKey(x.start) === state.selectedDay)
    .sort((a,b) => {
      const aState = getState(a);
      const bState = getState(b);
      const rank = { live: 0, upcoming: 1, ended: 2 };

      if (rank[aState] !== rank[bState]) {
        return rank[aState] - rank[bState];
      }

      return a.start - b.start;
    });

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No complete coverage windows match these filters.";
    el.schedule.appendChild(empty);
    return;
  }

  const groups = new Map();
  items.forEach(item => {
    const key = localDayKey(item.start);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });

  for (const [, group] of groups) {
    const section = document.createElement("section");
    section.className = "day-section";
    const header = document.createElement("div");
    header.className = "day-section__header";
    header.innerHTML = `<h2>${formatFullDay(group[0].start)}</h2><p>${group.length} coverage windows</p>`;
    const list = document.createElement("div");

    group.forEach(item => list.appendChild(renderCard(item)));
    section.append(header, list);
    el.schedule.appendChild(section);
  }
}

function renderCard(item) {
  const node = el.template.content.cloneNode(true);
  const card = node.querySelector(".coverage-card");
  const status = getState(item);
  card.dataset.state = status;

  node.querySelector(".provider-badge").textContent = providerInitials(item.provider);
  node.querySelector(".provider-name").textContent = item.provider;
  node.querySelector(".feed-name").textContent = item.feed || "Coverage";
  node.querySelector(".time-range").textContent = `${formatTime(item.start)} – ${formatTime(item.end)}`;
  node.querySelector(".duration").textContent = humanDuration(item.start,item.end);
  node.querySelector(".round").textContent = item.round;
  node.querySelector(".coverage-type").textContent = item.type === "streaming" ? "Streaming" : "Television";

  const pill = node.querySelector(".state-pill");
  pill.className = `state-pill ${status}`;
  pill.textContent = status === "live" ? "Live" : status === "upcoming" ? "Upcoming" : "Finished";

  if (status === "live") {
    const pct = Math.max(0, Math.min(100, Math.round((Date.now()-item.start)/(item.end-item.start)*100)));
    const wrap = node.querySelector(".progress-wrap");
    wrap.hidden = false;
    node.querySelector(".progress-bar").style.width = `${pct}%`;
    node.querySelector(".progress-text").textContent = `${pct}% of scheduled window elapsed`;
  }

  const link = node.querySelector(".source-link");
  if (item.sourceUrl) link.href = item.sourceUrl; else link.remove();

  return node;
}

function renderSources() {
  const collection = state.data.collection || {};
  const warnings = collection.warnings || [];
  el.collectionStatus.textContent =
    `Collector confidence score: ${collection.score ?? "—"}. ` +
    (warnings.length ? `Warnings: ${warnings.join(" ")}` : "No validation warnings.");

  el.sourceList.innerHTML = "";
  (collection.sources || []).forEach(source => {
    const div = document.createElement("div");
    div.className = "source-item";
    const a = document.createElement("a");
    a.href = source.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = `${source.domain} — ${source.parsedWindows} parsed windows`;
    div.appendChild(a);
    el.sourceList.appendChild(div);
  });
}

function getState(item, now=new Date()) {
  if (now >= item.start && now < item.end) return "live";
  return now < item.start ? "upcoming" : "ended";
}
function providerInitials(p) {
  if (p.toLowerCase().includes("golf")) return "GC";
  if (p.toLowerCase().includes("espn")) return "ESPN";
  if (p.toLowerCase().includes("paramount")) return "P+";
  return p.split(/\s+/).map(x=>x[0]).join("").slice(0,4).toUpperCase();
}
function humanDuration(start,end) {
  const mins = Math.round((end-start)/60000), h=Math.floor(mins/60), m=mins%60;
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
}
function localDayKey(date) {
  // Do not rely on locale formatting such as en-CA producing YYYY-MM-DD.
  // Browsers can render locale dates differently. Build the key explicitly.
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: state.timezone,
  }).formatToParts(date);

  const values = {};
  for (const part of parts) {
    if (part.type !== "literal") values[part.type] = part.value;
  }

  return `${values.year}-${values.month}-${values.day}`;
}
function formatTime(date) {
  return new Intl.DateTimeFormat("en-US", {hour:"numeric",minute:"2-digit",timeZone:state.timezone}).format(date);
}
function formatFullDay(date) {
  return new Intl.DateTimeFormat("en-US", {weekday:"long",month:"long",day:"numeric",timeZone:state.timezone}).format(date);
}
function formatDateTime(date) {
  return new Intl.DateTimeFormat("en-US", {month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZone:state.timezone}).format(date);
}

initTimezonePicker();
loadSchedule();
setInterval(() => {
  if (!state.data) return;

  // If the visitor leaves the page open across midnight, move to the new day.
  const today = localDayKey(new Date());
  const selectedIsScheduleDay = state.selectedDay !== "all";
  if (selectedIsScheduleDay && state.selectedDay !== today) {
    selectCurrentLocalDay();
  }

  renderAll();
}, 30_000);
