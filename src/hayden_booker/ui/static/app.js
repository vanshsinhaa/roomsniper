"use strict";

const REFRESH_MS = 30000;
const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const TIME_VALUES = Array.from({ length: 48 }, (_, index) => {
  const hour = Math.floor(index / 2);
  const minute = index % 2 === 0 ? "00" : "30";
  return `${String(hour).padStart(2, "0")}:${minute}`;
});

const state = {
  bookings: [],
  filter: "all",
  search: "",
  status: null,
  timer: null,
  config: null,
  configBaseline: null,
  configSaving: false,
  toastTimer: null,
};

const $ = (id) => document.getElementById(id);

/* Command shown in a health card tooltip when the check has no remedy of its own. */
const FALLBACK_COMMANDS = {
  config: "hayden-booker config validate",
  secret: "hayden-booker secret set-school-id",
  auth: "hayden-booker auth check",
  scheduler: "hayden-booker doctor",
  database: "hayden-booker history",
  activity: "hayden-booker run --dry-run",
  lock: "hayden-booker doctor",
};

function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = options.text;
  if (options.html !== undefined) node.innerHTML = options.html;
  for (const [key, value] of Object.entries(options.attrs || {})) {
    if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Hayden-Dashboard": "1",
    },
    body: JSON.stringify(payload),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || `${response.status} ${response.statusText}`);
  return result;
}

/* Formatting ---------------------------------------------------------------- */

function formatDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatClock(value) {
  const [hour, minute] = value.split(":").map(Number);
  const suffix = hour >= 12 ? "PM" : "AM";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelve}:${String(minute).padStart(2, "0")} ${suffix}`;
}

function formatStamp(iso) {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function humanStatus(status) {
  return status.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

/* Status -------------------------------------------------------------------- */

function renderStatus(status) {
  state.status = status;
  const pill = $("status-pill");
  pill.dataset.state = status.overall;
  $("status-label").textContent = status.overall_label;
  $("generated-at").textContent = `Updated ${formatStamp(status.generated_at_utc)}`;

  const config = status.config || {};
  $("brand-sub").textContent = config.valid
    ? `${config.timezone} · ${config.live_booking_enabled ? "live booking on" : "dry run only"}`
    : "configuration invalid";

  const checks = $("checks");
  checks.replaceChildren(
    ...status.checks.map((check) => {
      const card = el("div", {
        className: "card",
        attrs: { "data-state": check.state, tabindex: "0" },
      });
      card.appendChild(
        el("div", { className: "card-head" }, [
          el("div", { className: "card-title", text: check.label }),
          el("span", {
            className: "chip",
            text: check.state,
            attrs: { "data-state": check.state },
          }),
        ]),
      );
      card.appendChild(el("div", { className: "card-detail", text: check.detail }));
      if (check.remedy && check.state !== "ok") {
        card.appendChild(renderRemedy(check.remedy));
      }
      card.appendChild(renderTooltip(check));
      card.addEventListener("mouseenter", () => placeTooltip(card));
      card.addEventListener("focus", () => placeTooltip(card));
      return card;
    }),
  );

  renderBanner(status);
  renderStats(status);
  renderUpcoming(status);
}

function renderRemedy(remedy) {
  const wrapper = el("div", { className: "card-remedy" });
  const parts = remedy.split(/`([^`]+)`/g);
  parts.forEach((part, index) => {
    if (!part) return;
    wrapper.appendChild(index % 2 === 1 ? el("code", { text: part }) : document.createTextNode(part));
  });
  return wrapper;
}

function commandFor(check) {
  const match = /`([^`]+)`/.exec(check.remedy || "");
  if (match) return match[1];
  return FALLBACK_COMMANDS[check.key] || "hayden-booker doctor";
}

function tooltipText(check) {
  if (check.remedy) return check.remedy.replace(/`/g, "");
  return check.state === "ok"
    ? "Nothing to fix. Run this to re-check it yourself."
    : "Run this to investigate.";
}

function renderTooltip(check) {
  const command = commandFor(check);
  const copy = el("button", {
    className: "tip-copy",
    text: "Copy",
    attrs: { type: "button", "aria-label": `Copy command: ${command}` },
  });
  copy.addEventListener("click", (event) => {
    event.stopPropagation();
    copyCommand(command, copy);
  });
  return el("div", { className: "tip", attrs: { role: "tooltip" } }, [
    el("div", { className: "tip-label", text: check.state === "ok" ? "Check" : "Fix" }),
    el("div", { className: "tip-text", text: tooltipText(check) }),
    el("div", { className: "tip-command" }, [el("code", { text: command }), copy]),
  ]);
}

/* Flip the tooltip below its card when it would collide with the sticky header. */
function placeTooltip(card) {
  const tip = card.querySelector(".tip");
  if (!tip) return;
  const needsFlip = card.getBoundingClientRect().top - tip.offsetHeight < 84;
  tip.classList.toggle("tip-below", needsFlip);
}

async function copyCommand(command, button) {
  let copied = false;
  try {
    // Available because 127.0.0.1 counts as a secure context.
    await navigator.clipboard.writeText(command);
    copied = true;
  } catch (error) {
    copied = legacyCopy(command);
  }
  button.textContent = copied ? "Copied" : "Ctrl+C";
  button.dataset.copied = String(copied);
  setTimeout(() => {
    button.textContent = "Copy";
    delete button.dataset.copied;
  }, 1600);
}

function legacyCopy(command) {
  const field = el("textarea", { text: command });
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (error) {
    copied = false;
  }
  field.remove();
  return copied;
}

function renderBanner(status) {
  const banner = $("banner");
  const problems = status.checks.filter((check) => check.state === "error" || check.state === "attention");
  if (problems.length === 0) {
    banner.classList.add("hidden");
    return;
  }
  const worst = problems.some((check) => check.state === "error") ? "error" : "attention";
  banner.className = worst === "error" ? "banner" : "banner warn";
  banner.replaceChildren(
    el("strong", {
      text:
        worst === "error"
          ? "The booker cannot run unattended right now."
          : "The booker is running, but something needs a look.",
    }),
    el(
      "ul",
      {},
      problems.map((check) =>
        el("li", {}, [
          el("strong", { text: `${check.label}: ` }),
          document.createTextNode(check.detail),
          check.remedy ? renderRemedy(check.remedy) : null,
        ]),
      ),
    ),
  );
  banner.classList.remove("hidden");
}

function renderStats(status) {
  const counts = (status.occurrences && status.occurrences.by_status) || {};
  const confirmed = counts.CONFIRMED || 0;
  const review = (counts.MANUAL_REVIEW_REQUIRED || 0) + (counts.UNKNOWN_RESULT || 0);
  const failed = state.bookings.filter((booking) => booking.outcome === "failed").length;
  const scheduler = status.checks.find((check) => check.key === "scheduler") || {};
  const nextRun = (scheduler.extra && scheduler.extra.next_run) || "—";

  const tiles = [
    { value: String(confirmed), label: "Rooms confirmed" },
    { value: String((status.occurrences && status.occurrences.total) || 0), label: "Attempts recorded" },
    { value: String(failed), label: "Failed attempts" },
    { value: String(review), label: "Need manual review" },
    { value: nextRun, label: "Next automatic run" },
  ];

  $("stats").replaceChildren(
    ...tiles.map((tile) =>
      el("div", { className: "stat" }, [
        el("div", { className: "stat-value", text: tile.value }),
        el("div", { className: "stat-label", text: tile.label }),
      ]),
    ),
  );
}

function renderUpcoming(status) {
  const upcoming = status.upcoming || [];
  $("upcoming").replaceChildren(
    ...upcoming.map((item) =>
      el("span", { className: "upcoming-chip" }, [
        el("strong", { text: formatDate(item.target_date) }),
        document.createTextNode(
          `${formatClock(item.start_time)} – ${formatClock(item.end_time)} · ${item.schedule_id}`,
        ),
      ]),
    ),
  );
}

/* Schedule settings --------------------------------------------------------- */

function editableSnapshot(config) {
  return {
    live_booking_enabled: Boolean(config.live_booking_enabled),
    schedules: (config.schedules || []).map((schedule) => ({
      id: schedule.id,
      enabled: Boolean(schedule.enabled),
      weekday: schedule.weekday,
      start_time: schedule.start_time,
      end_time: schedule.end_time,
      room_preferences: [...schedule.room_preferences],
      exact_time_required: true,
    })),
  };
}

function cloneConfig(config) {
  return JSON.parse(JSON.stringify(config));
}

function configIsDirty() {
  if (!state.config || !state.configBaseline) return false;
  return JSON.stringify(editableSnapshot(state.config)) !== JSON.stringify(state.configBaseline);
}

function minuteValue(value) {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
}

function preferredRoom(schedule) {
  const rooms = state.config.known_rooms || [];
  const current = schedule.room_preferences || [];
  const defaultOrder =
    current.length === rooms.length && current.every((room, index) => room === rooms[index]);
  return defaultOrder ? "" : current[0] || "";
}

function setPreferredRoom(schedule, room) {
  const rooms = state.config.known_rooms || [];
  schedule.room_preferences = room ? [room, ...rooms.filter((item) => item !== room)] : [...rooms];
}

function selectControl(id, value, options, label) {
  const select = el("select", {
    className: "config-select",
    attrs: { id, "aria-label": label },
  });
  for (const option of options) {
    const node = el("option", { text: option.label, attrs: { value: option.value } });
    node.selected = option.value === value;
    select.appendChild(node);
  }
  return select;
}

function fieldControl(label, control, hint = "") {
  const children = [el("span", { className: "field-label", text: label }), control];
  if (hint) children.push(el("span", { className: "field-hint", text: hint }));
  return el("label", { className: "config-field" }, children);
}

function renderSchedule(schedule, index) {
  const prefix = `schedule-${index}`;
  const card = el("article", {
    className: "schedule-card",
    attrs: { "data-enabled": String(schedule.enabled) },
  });

  const enabled = el("input", {
    attrs: {
      id: `${prefix}-enabled`,
      type: "checkbox",
      "aria-label": `Enable ${schedule.id || `schedule ${index + 1}`}`,
    },
  });
  enabled.checked = schedule.enabled;
  enabled.addEventListener("change", () => {
    schedule.enabled = enabled.checked;
    renderConfig();
  });

  const remove = el("button", {
    className: "icon-button danger",
    text: "Remove",
    attrs: { type: "button", "aria-label": `Remove ${schedule.id || `schedule ${index + 1}`}` },
  });
  remove.addEventListener("click", () => {
    state.config.schedules.splice(index, 1);
    renderConfig();
  });

  const dayName = schedule.weekday || "monday";
  const header = el("div", { className: "schedule-card-head" }, [
    el("div", { className: "schedule-identity" }, [
      el("span", { className: "day-mark", text: dayName.slice(0, 3) }),
      el("div", {}, [
        el("h3", { text: schedule.id || "Untitled schedule" }),
        el("p", {
          text: `${formatClock(schedule.start_time)} – ${formatClock(schedule.end_time)}`,
        }),
      ]),
    ]),
    el("div", { className: "schedule-card-actions" }, [
      el("label", { className: "mini-toggle", attrs: { for: `${prefix}-enabled` } }, [
        enabled,
        el("span", { className: "toggle-track", attrs: { "aria-hidden": "true" } }, [el("span")]),
        el("span", { className: "mini-toggle-label", text: schedule.enabled ? "Active" : "Paused" }),
      ]),
      remove,
    ]),
  ]);

  const idInput = el("input", {
    className: "config-input",
    attrs: {
      id: `${prefix}-id`,
      type: "text",
      value: schedule.id,
      maxlength: "80",
      spellcheck: "false",
      autocomplete: "off",
    },
  });
  idInput.addEventListener("input", () => {
    schedule.id = idInput.value.trim();
    markConfigChanged();
  });

  const weekday = selectControl(
    `${prefix}-weekday`,
    schedule.weekday,
    WEEKDAYS.map((day) => ({ value: day, label: day[0].toUpperCase() + day.slice(1) })),
    "Weekday",
  );
  weekday.addEventListener("change", () => {
    schedule.weekday = weekday.value;
    renderConfig();
  });

  const timeOptions = TIME_VALUES.map((time) => ({ value: time, label: formatClock(time) }));
  const start = selectControl(`${prefix}-start`, schedule.start_time, timeOptions, "Start time");
  start.addEventListener("change", () => {
    schedule.start_time = start.value;
    renderConfig();
  });
  const end = selectControl(`${prefix}-end`, schedule.end_time, timeOptions, "End time");
  end.addEventListener("change", () => {
    schedule.end_time = end.value;
    renderConfig();
  });

  const selectedRoom = preferredRoom(schedule);
  const room = selectControl(
    `${prefix}-room`,
    selectedRoom,
    [
      { value: "", label: "Any available room" },
      ...(state.config.known_rooms || []).map((name) => ({ value: name, label: name })),
    ],
    "Preferred room",
  );
  room.addEventListener("change", () => {
    setPreferredRoom(schedule, room.value);
    renderConfig();
  });
  const fallbackCount = Math.max((schedule.room_preferences || []).length - 1, 0);
  const roomHint = selectedRoom
    ? `${fallbackCount} other Hayden rooms remain as fallbacks.`
    : `All ${(state.config.known_rooms || []).length} Hayden rooms are eligible.`;

  card.append(
    header,
    el("div", { className: "schedule-fields" }, [
      fieldControl("Schedule name", idInput, "Letters, numbers, hyphens, and underscores."),
      fieldControl("Day", weekday),
      fieldControl("Starts", start),
      fieldControl("Ends", end),
      fieldControl("Preferred room", room, roomHint),
    ]),
  );
  return card;
}

function renderConfig() {
  if (!state.config) return;
  const liveToggle = $("live-booking-toggle");
  liveToggle.disabled = state.configSaving;
  liveToggle.checked = state.config.live_booking_enabled;
  $("live-mode-label").textContent = state.config.live_booking_enabled ? "Live" : "Dry run";
  $("live-mode-description").textContent = state.config.live_booking_enabled
    ? "Live mode is on. Active schedules may submit a real booking on the next automatic run."
    : "Dry-run mode is on. The booker checks availability but does not submit a reservation.";
  $("schedule-settings").dataset.live = String(state.config.live_booking_enabled);

  const grid = $("schedule-grid");
  if (state.config.schedules.length === 0) {
    grid.replaceChildren(
      el("div", { className: "schedule-empty" }, [
        el("strong", { text: "No schedules yet" }),
        el("span", { text: "Add one before saving your configuration." }),
      ]),
    );
  } else {
    grid.replaceChildren(...state.config.schedules.map(renderSchedule));
  }
  $("add-schedule").disabled = state.configSaving;
  updateSaveState();
}

function markConfigChanged() {
  updateSaveState();
}

function updateSaveState() {
  const dirty = configIsDirty();
  const save = $("save-config");
  const discard = $("discard-config");
  save.disabled = !dirty || state.configSaving;
  save.textContent = state.configSaving ? "Saving…" : "Save changes";
  discard.classList.toggle("hidden", !dirty);
  discard.disabled = state.configSaving;
  $("save-state").textContent = state.configSaving
    ? "Validating configuration…"
    : dirty
      ? "Unsaved changes"
      : "All changes saved";
  $("save-state").dataset.dirty = String(dirty);
}

function validateConfigDraft() {
  const schedules = state.config.schedules || [];
  if (schedules.length === 0) return "Add at least one schedule before saving.";
  const ids = new Set();
  const dailyTotals = {};
  for (const schedule of schedules) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(schedule.id)) {
      return `“${schedule.id || "Untitled schedule"}” needs a simple name using letters, numbers, hyphens, or underscores.`;
    }
    if (ids.has(schedule.id)) return `Schedule name “${schedule.id}” is used more than once.`;
    ids.add(schedule.id);
    const duration = minuteValue(schedule.end_time) - minuteValue(schedule.start_time);
    if (duration <= 0) return `${schedule.id} must end after it starts.`;
    if (duration > 240) return `${schedule.id} cannot be longer than four hours.`;
    if (schedule.enabled) {
      dailyTotals[schedule.weekday] = (dailyTotals[schedule.weekday] || 0) + duration;
      if (dailyTotals[schedule.weekday] > 240) {
        return `Active schedules on ${schedule.weekday} total more than four hours.`;
      }
    }
  }
  return "";
}

function showToast(message, kind = "success") {
  const toast = $("toast");
  if (state.toastTimer) clearTimeout(state.toastTimer);
  toast.textContent = message;
  toast.dataset.kind = kind;
  toast.classList.remove("hidden");
  state.toastTimer = setTimeout(() => toast.classList.add("hidden"), 3400);
}

async function saveConfig() {
  const issue = validateConfigDraft();
  if (issue) {
    showToast(issue, "error");
    return;
  }
  state.configSaving = true;
  renderConfig();
  try {
    const saved = await postJson("/api/config", editableSnapshot(state.config));
    state.config = saved;
    state.configBaseline = editableSnapshot(saved);
    showToast("Schedule settings saved. The next run will use them.");
    renderConfig();
    await refresh();
  } catch (error) {
    showToast(`Could not save: ${error.message}`, "error");
  } finally {
    state.configSaving = false;
    renderConfig();
  }
}

function addSchedule() {
  const schedules = state.config.schedules;
  const ids = new Set(schedules.map((schedule) => schedule.id));
  let number = 1;
  while (ids.has(`new-schedule-${number}`)) number += 1;
  const lastDay = schedules.length ? WEEKDAYS.indexOf(schedules.at(-1).weekday) : -1;
  schedules.push({
    id: `new-schedule-${number}`,
    enabled: true,
    weekday: WEEKDAYS[(lastDay + 1) % WEEKDAYS.length],
    start_time: "13:00",
    end_time: "14:00",
    room_preferences: [...state.config.known_rooms],
    exact_time_required: true,
  });
  renderConfig();
  const cards = $("schedule-grid").querySelectorAll(".schedule-card");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  cards[cards.length - 1]?.scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "center",
  });
}

function discardConfig() {
  if (!state.configBaseline) return;
  state.config = {
    ...state.config,
    ...cloneConfig(state.configBaseline),
  };
  renderConfig();
  showToast("Unsaved changes discarded.", "neutral");
}

/* Bookings ------------------------------------------------------------------ */

function visibleBookings() {
  const needle = state.search.trim().toLowerCase();
  return state.bookings.filter((booking) => {
    if (state.filter !== "all" && booking.outcome !== state.filter) return false;
    if (!needle) return true;
    return [booking.room, booking.target_date, booking.schedule_id, booking.status, booking.weekday]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(needle));
  });
}

function renderBookings() {
  const rows = visibleBookings();
  const body = $("bookings");
  $("bookings-empty").classList.toggle("hidden", rows.length > 0);
  body.replaceChildren(
    ...rows.map((booking) => {
      const row = el("tr");
      row.appendChild(
        el("td", { className: "date-cell" }, [
          el("strong", { text: formatDate(booking.target_date) }),
          el("span", { text: booking.weekday }),
        ]),
      );
      row.appendChild(
        el("td", {
          text: `${formatClock(booking.start_time)} – ${formatClock(booking.end_time)}`,
        }),
      );
      row.appendChild(el("td", { text: booking.room || "—" }));
      row.appendChild(el("td", { text: booking.schedule_id }));
      row.appendChild(
        el("td", {}, [
          el("span", {
            className: "badge",
            text: humanStatus(booking.status),
            attrs: {
              "data-outcome": booking.outcome,
              "data-acknowledged": String(Boolean(booking.acknowledged)),
            },
          }),
          booking.acknowledged
            ? el("span", { className: "badge reviewed", text: "Reviewed" })
            : null,
        ]),
      );
      row.appendChild(el("td", { className: "right", text: String(booking.attempt_count) }));
      row.appendChild(el("td", { className: "right muted", text: "›" }));
      row.addEventListener("click", () => openDrawer(booking.id));
      return row;
    }),
  );
}

/* Drawer -------------------------------------------------------------------- */

async function openDrawer(id) {
  const drawer = $("drawer");
  const scrim = $("scrim");
  drawer.classList.remove("hidden");
  scrim.classList.remove("hidden");
  $("drawer-body").replaceChildren(el("p", { className: "empty", text: "Loading…" }));
  try {
    const booking = await getJson(`/api/bookings/${encodeURIComponent(id)}`);
    $("drawer-body").replaceChildren(...renderDetail(booking));
  } catch (error) {
    $("drawer-body").replaceChildren(
      el("p", { className: "empty", text: `Could not load booking: ${error.message}` }),
    );
  }
}

function closeDrawer() {
  $("drawer").classList.add("hidden");
  $("scrim").classList.add("hidden");
}

function needsReview(booking) {
  const flagged = booking.status === "MANUAL_REVIEW_REQUIRED" || booking.status === "UNKNOWN_RESULT";
  return flagged && !booking.acknowledged;
}

function acknowledgeButton(booking) {
  const button = el("button", { className: "button", text: "Mark reviewed" });
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Marking…";
    try {
      const response = await fetch(
        `/api/bookings/${encodeURIComponent(booking.id)}/acknowledge`,
        { method: "POST", headers: { "X-Hayden-Dashboard": "1" } },
      );
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const updated = await response.json();
      $("drawer-body").replaceChildren(...renderDetail(updated));
      refresh();
    } catch (error) {
      button.disabled = false;
      button.textContent = `Failed: ${error.message}`;
    }
  });
  return button;
}

function renderDetail(booking) {
  const head = el("div", { className: "drawer-head" }, [
    el("div", {}, [
      el("div", { className: "drawer-title", text: booking.room || "No room selected" }),
      el("div", {
        className: "drawer-sub",
        text: `${booking.weekday}, ${formatDate(booking.target_date)} · ${formatClock(
          booking.start_time,
        )} – ${formatClock(booking.end_time)}`,
      }),
    ]),
    (() => {
      const close = el("button", { className: "button", text: "Close" });
      close.addEventListener("click", closeDrawer);
      return close;
    })(),
  ]);

  const googleAction = booking.calendar.synced
    ? el("span", { className: "button calendar-added", text: "Added to Google Calendar" })
    : el("a", {
        className: "button primary",
        text: "Add to Google Calendar",
        attrs: {
          href: booking.calendar.google_url,
          target: "_blank",
          rel: "noopener noreferrer",
        },
      });

  const actions = el("div", { className: "detail-actions" }, [
    googleAction,
    el("a", {
      className: "button",
      text: "Download .ics",
      attrs: { href: booking.calendar.ics_path },
    }),
    needsReview(booking) ? acknowledgeButton(booking) : null,
  ]);

  const rows = [
    ["Status", humanStatus(booking.status)],
    ["Reviewed", booking.acknowledged ? formatStamp(booking.acknowledged_at_utc) : "Not yet"],
    ["Schedule", booking.schedule_id],
    ["Duration", `${booking.duration_minutes} minutes`],
    ["Attempts", String(booking.attempt_count)],
    ["Confirmed at", formatStamp(booking.confirmed_at_utc)],
    ["Confirmation", booking.confirmation_reference || "—"],
    [
      "Google Calendar",
      booking.calendar.synced
        ? `Added ${formatStamp(booking.calendar.synced_at_utc)}`
        : booking.calendar.sync_error
          ? "Automatic add failed; manual option available above"
          : "Not added automatically",
    ],
    ["Last updated", formatStamp(booking.updated_at_utc)],
    ["Timezone", booking.timezone],
    ["Occurrence ID", booking.id],
  ];

  const summary = el(
    "div",
    { className: "detail-card" },
    rows.map(([label, value]) =>
      el("div", { className: "detail-row" }, [
        el("span", { text: label }),
        el("strong", { text: value }),
      ]),
    ),
  );

  if (booking.last_error_code) {
    summary.appendChild(
      el("div", {
        className: "error-note",
        text: `${booking.last_error_code}: ${booking.last_error_summary || ""}`,
      }),
    );
  }

  if (booking.calendar.sync_error) {
    summary.appendChild(
      el("div", {
        className: "error-note",
        text: `Google Calendar: ${booking.calendar.sync_error}`,
      }),
    );
  }

  const events = booking.events || [];
  const timeline = el("div", { className: "detail-card" }, [
    el("h3", { text: "Timeline" }),
    el(
      "ul",
      { className: "timeline" },
      events.map((event) =>
        el("li", {}, [
          el("div", { className: "event-name", text: humanStatus(event.event_type) }),
          el("div", {
            className: "event-time",
            text: `${formatStamp(event.occurred_at_utc)}${event.room ? ` · ${event.room}` : ""}`,
          }),
        ]),
      ),
    ),
  ]);

  return [head, actions, summary, timeline];
}

/* Wiring -------------------------------------------------------------------- */

async function refresh() {
  try {
    const shouldLoadConfig = !configIsDirty() && !state.configSaving;
    const [status, bookings, config] = await Promise.all([
      getJson("/api/status"),
      getJson("/api/bookings?limit=200"),
      shouldLoadConfig ? getJson("/api/config") : Promise.resolve(null),
    ]);
    state.bookings = bookings.bookings || [];
    if (config) {
      state.config = config;
      state.configBaseline = editableSnapshot(config);
      renderConfig();
    }
    renderStatus(status);
    renderBookings();
  } catch (error) {
    const pill = $("status-pill");
    pill.dataset.state = "error";
    $("status-label").textContent = "Dashboard offline";
    const banner = $("banner");
    banner.className = "banner";
    banner.replaceChildren(
      el("strong", { text: "Cannot reach the local dashboard service. " }),
      document.createTextNode(`${error.message}. Restart it with `),
      el("code", { text: "hayden-booker ui" }),
    );
    banner.classList.remove("hidden");
  }
}

function setAutoRefresh(enabled) {
  if (state.timer) clearInterval(state.timer);
  state.timer = enabled ? setInterval(refresh, REFRESH_MS) : null;
}

document.addEventListener("DOMContentLoaded", () => {
  $("refresh").addEventListener("click", refresh);
  $("save-config").addEventListener("click", saveConfig);
  $("discard-config").addEventListener("click", discardConfig);
  $("add-schedule").addEventListener("click", addSchedule);
  $("live-booking-toggle").addEventListener("change", (event) => {
    state.config.live_booking_enabled = event.target.checked;
    renderConfig();
  });
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  $("search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderBookings();
  });
  $("filters").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (!tab) return;
    state.filter = tab.dataset.filter;
    for (const node of $("filters").querySelectorAll(".tab")) {
      node.classList.toggle("is-active", node === tab);
    }
    renderBookings();
  });
  $("auto-refresh").addEventListener("change", (event) => setAutoRefresh(event.target.checked));
  window.addEventListener("beforeunload", (event) => {
    if (!configIsDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  setAutoRefresh($("auto-refresh").checked);
  refresh();
});
