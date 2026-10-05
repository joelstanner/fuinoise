import test from "node:test";
import assert from "node:assert/strict";
import {
  dayTicks,
  localParts,
  slotInput,
  slotLayout,
  moveSlots,
} from "../src/schedule.js";

test("DST rails contain 23 and 25 hours and distinct repeated instants", () => {
  assert.equal(dayTicks("2026-03-08", "America/New_York").length, 92);
  const fall = dayTicks("2026-11-01", "America/New_York");
  assert.equal(fall.length, 100);
  const repeated = fall.filter((tick) => tick.local.endsWith("T01:30:00"));
  assert.equal(repeated.length, 2);
  assert.notEqual(repeated[0].start, repeated[1].start);
});
test("day rail covers zones on both sides of UTC, including quarter-hour offsets", () => {
  for (const zone of [
    "Pacific/Kiritimati",
    "Pacific/Pago_Pago",
    "Asia/Kathmandu",
  ]) {
    const ticks = dayTicks("2026-10-04", zone);
    assert.equal(ticks.length, 96);
    assert.ok(ticks.every((tick) => tick.local.startsWith("2026-10-04")));
    assert.ok(ticks[0].local.endsWith("T00:00:00"));
  }
});
test("overnight performances clip at the next day without losing actual duration", () => {
  const ticks = dayTicks("2026-10-05", "UTC");
  const layout = slotLayout(
    { start: "2026-10-04T23:30:00Z", duration_minutes: 90 },
    ticks,
  );
  assert.deepEqual(layout, { top: 0, height: 120, continuing: true });
  assert.equal(
    slotLayout({ start: "2026-10-04T20:00:00Z", duration_minutes: 60 }, ticks),
    null,
  );
});
test("a performance crossing spring DST occupies elapsed time on the rail", () => {
  const ticks = dayTicks("2026-03-08", "America/New_York");
  const layout = slotLayout(
    { start: "2026-03-08T01:30:00-05:00", duration_minutes: 60 },
    ticks,
  );
  assert.equal(layout.height, 120);
  assert.equal(
    localParts("2026-03-08T07:30:00Z", "America/New_York"),
    "2026-03-08T03:30:00",
  );
});
test("movement changes only the requested slot and preserves source data", () => {
  const slots = [
    { id: 1, start: "2026-10-04T10:00:00Z", streamer_id: 2 },
    { id: 2, start: "2026-10-04T11:00:00Z" },
  ];
  const moved = moveSlots(slots, 1, "2026-10-04T09:00:00Z");
  assert.equal(moved[0].start, "2026-10-04T09:00:00Z");
  assert.equal(moved[1], slots[1]);
  assert.equal(slots[0].start, "2026-10-04T10:00:00Z");
});
test("save payload excludes request provenance and preserves seconds/offsets", () => {
  const input = slotInput({
    id: 1,
    start: "2026-10-04T10:00:37+02:00",
    local_start: "ignored",
    duration_minutes: 60,
    streamer_id: 2,
    request_id: 44,
    source_slot_id: 99,
  });
  assert.equal(input.start, "2026-10-04T10:00:37+02:00");
  assert.equal(input.request_id, undefined);
  assert.equal(input.source_slot_id, undefined);
  assert.equal(input.local_start, undefined);
});
test("local entries are submitted for authoritative server DST validation", () => {
  const input = slotInput({
    id: null,
    local_start: "2026-11-01T01:30:00",
    duration_minutes: 60,
    streamer_id: null,
  });
  assert.equal(input.start, undefined);
  assert.equal(input.local_start, "2026-11-01T01:30:00");
});
