"use strict";

const REFRESH_MS = 30000;

const state = {
  bookings: [],
  filter: "all",
  search: "",
  status: null,
  timer: null,
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

  const actions = el("div", { className: "detail-actions" }, [
    el("a", {
      className: "button primary",
      text: "Add to Google Calendar",
      attrs: {
        href: booking.calendar.google_url,
        target: "_blank",
        rel: "noopener noreferrer",
      },
    }),
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
    const [status, bookings] = await Promise.all([
      getJson("/api/status"),
      getJson("/api/bookings?limit=200"),
    ]);
    state.bookings = bookings.bookings || [];
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
  setAutoRefresh($("auto-refresh").checked);
  refresh();
});
