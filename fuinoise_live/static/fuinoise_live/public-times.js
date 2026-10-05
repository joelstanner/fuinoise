export function visitorTime(iso, timeZone, locale) {
  const instant = new Date(iso);
  if (!Number.isFinite(instant.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(locale, {
      ...(timeZone ? { timeZone } : {}),
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(instant);
  } catch {
    return "";
  }
}

if (typeof document !== "undefined") {
  for (const element of document.querySelectorAll("[data-visitor-time]")) {
    const formatted = visitorTime(element.dataset.visitorTime);
    if (formatted) {
      element.textContent = `Your time: ${formatted}`;
      element.hidden = false;
    }
  }
  for (const badge of document.querySelectorAll("[data-live-expires-ms]")) {
    const remaining = Number(badge.dataset.liveExpiresMs);
    if (Number.isFinite(remaining) && remaining >= 0)
      setTimeout(() => {
        badge.textContent = "Live status unavailable";
        badge.classList.remove("live", "offline");
        badge.classList.add("unknown");
        badge.closest(".slot-main")?.querySelector(".twitch-now")?.remove();
      }, remaining);
  }
}
