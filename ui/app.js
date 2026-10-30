"use strict";
const $ = id => document.getElementById(id);
const state = {settings: {}, revision: "", token: "", airports: [], byCode: new Map(), origin: [], destination: [], dates: [], returns: [], dirty: false};
const cabinNames = {economy: "经济舱", premium_economy: "超级经济舱", business: "公务舱", first: "头等舱"};
const normalize = value => String(value).normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().trim();
const asList = value => value == null ? [] : Array.isArray(value) ? value : [value];
const localDay = new Date();
const today = `${localDay.getFullYear()}-${String(localDay.getMonth() + 1).padStart(2, "0")}-${String(localDay.getDate()).padStart(2, "0")}`;

function status(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "";
  $("status").hidden = false;
}
function dirty() { state.dirty = true; $("status").hidden = true; }
function airport(code) { return state.byCode.get(code) || {code, name: "机场代码", city: "", country: "", aliases: ""}; }
function chip(container, label, remove, aria) {
  const item = document.createElement("div"); item.className = "chip";
  const text = document.createElement("span"); text.textContent = label;
  const button = document.createElement("button"); button.type = "button"; button.textContent = "×";
  button.setAttribute("aria-label", aria || `移除 ${label}`); button.addEventListener("click", remove);
  item.append(text, button); container.append(item);
}
function formatDate(value) {
  return new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "short", day: "numeric"}).format(new Date(value + "T12:00:00"));
}
function update() {
  for (const side of ["origin", "destination"]) {
    const container = $(side + "-chips"); container.replaceChildren();
    for (const code of state[side]) {
      const a = airport(code), city = a.aliases.split(" ")[0] || a.city;
      chip(container, `${city ? city + " " : ""}${code}`, () => {state[side] = state[side].filter(c => c !== code); dirty(); update();});
    }
  }
  for (const [key, target] of [["dates", "depart-chips"], ["returns", "return-chips"]]) {
    const container = $(target); container.replaceChildren();
    for (const value of state[key]) chip(container, value, () => {state[key] = state[key].filter(d => d !== value); dirty(); update();}, `移除日期 ${value}`);
  }
  const roundTrip = document.querySelector('[name="trip-type"]:checked').value === "return";
  $("return-field").hidden = !roundTrip; $("return-summary-row").hidden = !roundTrip;
  $("return-date").min = state.dates[0] || today;
  $("route-summary").textContent = `${state.origin.join(" / ") || "出发地"} → ${state.destination.join(" / ") || "目的地"}`;
  $("depart-summary").textContent = state.dates.map(formatDate).join("、") || "尚未选择";
  $("return-summary").textContent = state.returns.map(formatDate).join("、") || "尚未选择";
  $("cabin-summary").textContent = cabinNames[$("cabin").value];
  $("date-count").textContent = state.dates.length ? `已选 ${state.dates.length} 个出发日期${roundTrip ? ` · ${state.returns.length} 个回程日期` : ""}` : "未选择日期，监控暂停";
}
function closeOptions(side) { $(side + "-options").hidden = true; $(side + "-search").setAttribute("aria-expanded", "false"); }
function selectAirport(side, code) {
  if (!state[side].includes(code)) {
    if (state[side].length >= 7) {status("每侧最多可选择 7 个机场。", true); return false;}
    state[side].push(code);
  }
  $(side + "-search").value = ""; closeOptions(side); dirty(); update(); $(side + "-search").focus();
  return true;
}
function suggestions(side) {
  const input = $(side + "-search"), list = $(side + "-options"), query = normalize(input.value);
  list.replaceChildren();
  if (!query) {closeOptions(side); return;}
  const words = query.split(/\s+/);
  const matches = state.airports.filter(a => !state[side].includes(a.code) && words.every(word => a.index.includes(word)));
  const rank = a => a.code.toLowerCase() === query ? 0 : a.code.toLowerCase().startsWith(query) ? 1 : normalize(a.city).startsWith(query) ? 2 : 3;
  matches.sort((a, b) => rank(a) - rank(b) || a.code.localeCompare(b.code));
  if (!matches.length && /^[a-z]{3}$/.test(query)) matches.push(airport(query.toUpperCase()));
  for (const a of matches.slice(0, 12)) {
    const option = document.createElement("button"); option.type = "button"; option.className = "airport-option";
    option.setAttribute("role", "option"); option.setAttribute("aria-selected", "false");
    const title = document.createElement("strong"); title.textContent = `${a.aliases ? a.aliases.split(" ").slice(0, 2).join(" · ") : a.city || a.name} · ${a.code}`;
    const detail = document.createElement("small"); detail.textContent = [a.name, a.city, a.country].filter(Boolean).join(" · ");
    option.append(title, detail); option.addEventListener("click", () => selectAirport(side, a.code));
    option.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") {e.preventDefault(); (option.nextElementSibling || list.firstElementChild).focus();}
      if (e.key === "ArrowUp") {e.preventDefault(); (option.previousElementSibling || input).focus();}
      if (e.key === "Escape") {closeOptions(side); input.focus();}
    });
    list.append(option);
  }
  if (!list.children.length) {const empty = document.createElement("p"); empty.className = "small"; empty.textContent = "未找到机场，请尝试英文城市名或三位机场代码。"; list.append(empty);}
  list.hidden = false; input.setAttribute("aria-expanded", "true");
}
function bind() {
  for (const side of ["origin", "destination"]) {
    const input = $(side + "-search"); input.addEventListener("input", () => {dirty(); suggestions(side);});
    input.addEventListener("focus", () => {if (input.value) suggestions(side);});
    input.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") {e.preventDefault(); $(side + "-options").querySelector("button")?.focus();}
      if (e.key === "Escape") closeOptions(side);
      if (e.key === "Enter") {
        e.preventDefault();
        const code = input.value.trim().toUpperCase();
        if (/^[A-Z]{3}$/.test(code)) selectAirport(side, code);
        else $(side + "-options").querySelector("button")?.focus();
      }
    });
  }
  document.addEventListener("click", e => {for (const side of ["origin", "destination"]) if (!$(side + "-field").contains(e.target)) closeOptions(side);});
  $("swap").addEventListener("click", () => { [state.origin, state.destination] = [state.destination, state.origin]; for (const side of ["origin", "destination"]) {$(side + "-search").value = ""; closeOptions(side);} dirty(); update();});
  for (const [input, key] of [["depart-date", "dates"], ["return-date", "returns"]]) {
    $(input).min = today;
    $(input).addEventListener("change", () => {
      const value = $(input).value;
      if (!value || !$(input).checkValidity()) {status("请选择有效的未来日期。", true); return;}
      state[key] = [...new Set([...state[key], value])].sort(); $(input).value = ""; dirty(); update();
    });
  }
  for (const element of document.querySelectorAll('[name="trip-type"]')) element.addEventListener("change", () => {if (element.value === "oneway") state.returns = []; dirty(); update();});
  $("clear-dates").addEventListener("click", () => {state.dates = []; state.returns = []; dirty(); update();});
  for (const id of ["cabin", "depart-after", "max-stops", "airlines"]) $(id).addEventListener("change", () => {dirty(); update();});
  $("trip-form").addEventListener("submit", save);
  window.addEventListener("beforeunload", e => {if (state.dirty) {e.preventDefault(); e.returnValue = "";}});
}
function collect() {
  for (const side of ["origin", "destination"]) {
    const pending = $(side + "-search").value.trim();
    if (/^[a-z]{3}$/i.test(pending)) {if (!selectAirport(side, pending.toUpperCase())) throw new Error("每侧最多可选择 7 个机场。");}
    else if (pending) throw new Error("请从搜索结果中选择具体机场，再保存行程。");
    if (!state[side].length) throw new Error("请选择出发机场和到达机场。");
  }
  if (state.origin.some(code => state.destination.includes(code))) throw new Error("出发地和目的地不能包含同一个机场。");
  const roundTrip = document.querySelector('[name="trip-type"]:checked').value === "return";
  if (roundTrip && state.dates.length && !state.returns.length) throw new Error("请选择至少一个回程日期，或切换为单程。");
  if (state.returns.length && (!state.dates.length || state.returns.some(date => date < state.dates[0]))) throw new Error("回程日期不能早于出发日期。");
  const settings = {...state.settings, origin: state.origin.length === 1 ? state.origin[0] : [...state.origin],
    destination: state.destination.length === 1 ? state.destination[0] : [...state.destination], dates: [...state.dates], cabin: $("cabin").value};
  if (roundTrip && state.returns.length) settings.return_dates = [...state.returns]; else delete settings.return_dates;
  if ($("depart-after").value) settings.depart_after = $("depart-after").value; else delete settings.depart_after;
  if ($("max-stops").value !== "") settings.max_stops = Number($("max-stops").value); else delete settings.max_stops;
  const airlines = $("airlines").value.split(/[,，\s]+/).filter(Boolean).map(value => value.toUpperCase());
  if (airlines.length) settings.airlines = [...new Set(airlines)]; else delete settings.airlines;
  return settings;
}
async function save(event) {
  event.preventDefault();
  try {
    const settings = collect(); $("save").disabled = true;
    const response = await fetch("/api/trip", {method: "POST", headers: {"Content-Type": "application/json", "X-AirTicket-Token": state.token}, body: JSON.stringify({settings, revision: state.revision})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存失败，请重试。");
    state.settings = settings; state.revision = data.revision; state.dirty = false;
    status(data.active_routes ? "行程已保存。运行监控时会使用这些设置；若使用 GitHub Actions，请提交更新后的 trip.yaml。" : "已保存。当前没有有效的出发日期，监控保持暂停。再添加日期即可恢复。");
  } catch (error) {status(error.message, true);}
  finally {$("save").disabled = false;}
}
async function start() {
  try {
    const responses = await Promise.all([fetch("/api/trip"), fetch("/api/airports")]);
    const [trip, catalog] = await Promise.all(responses.map(response => response.json()));
    if (!responses[0].ok) throw new Error(trip.error);
    if (!responses[1].ok) throw new Error("无法载入机场目录，请重新启动配置页面。");
    Object.assign(state, {settings: trip.settings, revision: trip.revision, token: trip.token});
    state.airports = catalog.airports.map(a => ({...a, index: normalize([a.code, a.city, a.name, a.country, a.aliases].join(" "))}));
    state.byCode = new Map(state.airports.map(a => [a.code, a]));
    state.origin = asList(trip.settings.origin).map(value => String(value).trim().toUpperCase()).filter(Boolean);
    state.destination = asList(trip.settings.destination).map(value => String(value).trim().toUpperCase()).filter(Boolean);
    state.dates = asList(trip.settings.dates).sort(); state.returns = asList(trip.settings.return_dates).sort();
    $("cabin").value = trip.settings.cabin || "economy";
    $("depart-after").value = trip.settings.depart_after || "";
    const stops = trip.settings.max_stops;
    if (stops != null && ![0, 1, 2, 3].includes(stops)) $("max-stops").add(new Option(`${stops} 次`, String(stops)));
    $("max-stops").value = stops == null ? "" : String(stops);
    $("airlines").value = asList(trip.settings.airlines).join(", ");
    if (state.returns.length) document.querySelector('[name="trip-type"][value="return"]').checked = true;
    bind(); update(); $("save").disabled = false;
  } catch (error) {status(`无法载入配置：${error.message}`, true);}
}
start();
