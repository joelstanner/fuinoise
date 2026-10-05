import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const source = readFileSync(
  new URL(
    "../../fuinoise_live/static/fuinoise_live/public-times.js",
    import.meta.url,
  ),
  "utf8",
);
const { visitorTime } = await import(
  "data:text/javascript;base64," + Buffer.from(source).toString("base64")
);

test("visitor times preserve instant, date, and offset across overnight schedules", () => {
  const iso = "2026-10-11T01:00:00+00:00";
  assert.match(
    visitorTime(iso, "America/Los_Angeles", "en-US"),
    /Oct 10, 2026.*6:00 PM PDT/,
  );
  assert.match(
    visitorTime(iso, "Asia/Tokyo", "en-US"),
    /Oct 11, 2026.*10:00 AM GMT\+9/,
  );
});
test("visitor DST repeats use the correct offsets", () => {
  assert.match(
    visitorTime("2026-11-01T05:30:00Z", "America/New_York", "en-US"),
    /1:30 AM EDT/,
  );
  assert.match(
    visitorTime("2026-11-01T06:30:00Z", "America/New_York", "en-US"),
    /1:30 AM EST/,
  );
});
test("invalid times and unsupported zones leave server event times intact", () => {
  assert.equal(visitorTime("invalid", "UTC", "en-US"), "");
  assert.equal(visitorTime("2026-10-04T10:00Z", "not-a-zone", "en-US"), "");
});
