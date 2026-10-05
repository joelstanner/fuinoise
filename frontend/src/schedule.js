// Use actual instants on the rail: DST days may contain 23 or 25 hours.
export function localParts(instant, zone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(instant));
  const values = Object.fromEntries(parts.map((p) => [p.type, p.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}:${values.second}`;
}

export function dayTicks(day, zone) {
  const anchor = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(anchor)) return [];
  const ticks = [];
  for (let minutes = -18 * 60; minutes < 42 * 60; minutes += 15) {
    const start = new Date(anchor + minutes * 60000).toISOString();
    const local = localParts(start, zone);
    if (local.slice(0, 10) === day) ticks.push({ start, local });
  }
  return ticks;
}

export function slotInput(slot) {
  const input = {
    id: slot.id,
    duration_minutes: slot.duration_minutes,
    streamer_id: slot.streamer_id,
    replay_url: slot.replay_url || "",
    raid_slot_note: slot.raid_slot_note || "",
  };
  if (slot.start) input.start = slot.start;
  else input.local_start = slot.local_start;
  return input;
}

export function slotLayout(slot, ticks) {
  if (!ticks.length) return null;
  const first = Date.parse(ticks[0].start);
  const last = Date.parse(ticks.at(-1).start) + 15 * 60000;
  const start = Date.parse(slot.start);
  const end = start + (slot.duration_minutes || 15) * 60000;
  if (start >= last || end <= first) return null;
  return {
    top: ((Math.max(start, first) - first) / 60000) * 2,
    height: Math.max(
      30,
      ((Math.min(end, last) - Math.max(start, first)) / 60000) * 2,
    ),
    continuing: start < first,
  };
}

export function moveSlots(slots, id, start) {
  return slots.map((slot) => (slot.id === id ? { ...slot, start } : slot));
}
