// Real HTTP/browser workflow against a disposable Django database.
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const assert = require("node:assert/strict");
const repo = path.resolve(__dirname, "../..");
const python = process.env.PYTHON_BIN || path.join(repo, "venv/bin/python");
const fixtureScript = path.join(repo, "fuinoise_live/tests/browser_fixture.py");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fuinoise-timeline-"));
const env = {
  ...process.env,
  PYTHONPATH: repo,
  FUINOISE_BROWSER_TEST_DIR: dir,
};
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stage = (action) =>
  execFileSync(python, [fixtureScript, action], { env, cwd: repo });
let server, browser;
(async () => {
  const port = await new Promise((resolve) => {
    const socket = net.createServer();
    socket.listen(0, "127.0.0.1", () => {
      const port = socket.address().port;
      socket.close(() => resolve(port));
    });
  });
  const origin = `http://127.0.0.1:${port}`;
  const log = fs.openSync(path.join(dir, "server.log"), "w");
  server = spawn(python, [fixtureScript, "serve", String(port)], {
    env,
    cwd: repo,
    stdio: ["ignore", log, log],
  });
  fs.closeSync(log);
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null)
      throw new Error(
        "Preview server failed: " +
          fs.readFileSync(path.join(dir, "server.log"), "utf8"),
      );
    try {
      if (
        (await fetch(origin)).ok &&
        fs.existsSync(path.join(dir, "fixture.json"))
      )
        break;
    } catch {}
    await pause(200);
    if (i === 99) throw new Error("Preview server did not start.");
  }
  const fixture = JSON.parse(
    fs.readFileSync(path.join(dir, "fixture.json"), "utf8"),
  );
  browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_BROWSER_CHANNEL
      ? { channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL }
      : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1400 },
  });
  await context.addCookies([
    {
      name: "sessionid",
      value: fixture.session_id,
      url: origin,
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  const page = await context.newPage();
  page.setDefaultTimeout(9000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const publicContext = await browser.newContext({
    timezoneId: "America/Los_Angeles",
  });
  const publicPage = await publicContext.newPage();
  const publicURL = `${origin}/events/${fixture.event_id}/`;
  await page.goto(origin + "/organizer/");
  await page.getByLabel("Choose event").selectOption(String(fixture.event_id));
  await page.getByText("Private draft loaded.", { exact: true }).waitFor();
  const current = async () =>
    (
      await context.request.get(
        `${origin}/organizer/api/events/${fixture.event_id}/draft/`,
      )
    ).json();
  const changed = async (operation) => {
    const response = page.waitForResponse(
      (response) =>
        response.url().includes("/organizer/api/events/") &&
        response.request().method() === "PUT",
    );
    const results = await Promise.all([response, operation()]);
    return results[0].json();
  };
  const waitSaved = async () => {
    await page
      .getByText("Draft saved. Public schedule unchanged.", { exact: true })
      .waitFor();
  };
  await page
    .locator(".request-card")
    .first()
    .dragTo(page.locator(".timeline-card.open").first());
  await waitSaved();
  assert.equal(
    (await current()).slots.filter((slot) => slot.streamer_id).length,
    1,
  );
  await publicPage.goto(publicURL);
  assert.equal(
    await publicPage
      .getByRole("heading", { name: "Open slot", exact: true })
      .count(),
    3,
  );
  assert.equal(
    await publicPage.getByText("Private browser preference").count(),
    0,
  );
  await page.bringToFront();
  await page.locator(".timeline-scroll").evaluate((element) => {
    element.scrollTop = 7 * 120;
  });
  const performer = page.locator(".timeline-card.assigned").first();
  await changed(() =>
    performer
      .locator(".drag-handle")
      .dragTo(
        page.locator(`.time-tick[data-start="${fixture.day}T09:00:00.000Z"]`),
      ),
  );
  await waitSaved();
  assert.ok(
    (await current()).slots
      .find((slot) => slot.streamer_id)
      .start.includes("T09:00:00"),
  );
  await performer.locator(".drag-handle").dragTo(
    page.locator(".timeline-card.open").filter({
      has: page.getByRole("button", {
        name: "Edit Open slot, 11:00 AM UTC",
        exact: true,
      }),
    }),
    { targetPosition: { x: 20, y: 5 } },
  );
  await page.getByRole("alert").filter({ hasText: "overlap" }).waitFor();
  assert.ok(
    (await current()).slots
      .find((slot) => slot.streamer_id)
      .start.includes("T09:00:00"),
  );
  await performer.getByRole("button").click();
  await changed(() =>
    page.getByLabel("Start in event time").fill(`${fixture.day}T08:30`),
  );
  await waitSaved();
  assert.ok(
    (await current()).slots
      .find((slot) => slot.streamer_id)
      .start.includes("T08:30:00"),
  );
  // Network failure retains unsaved form edits and blocks publication until retry.
  await page.route("**/draft/", (route) =>
    route.request().method() === "PUT"
      ? route.abort("failed")
      : route.continue(),
  );
  await page
    .getByLabel("Public slot note")
    .fill("Browser note after reconnect");
  await page
    .getByRole("alert")
    .filter({ hasText: "Connection lost" })
    .waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Publish schedule" }).isEnabled(),
    false,
  );
  assert.equal(
    (await current()).slots.find((slot) => slot.streamer_id).raid_slot_note,
    "",
  );
  await page.unroute("**/draft/");
  await page.getByRole("button", { name: "Save now / retry" }).first().click();
  await waitSaved();
  assert.equal(
    (await current()).slots.find((slot) => slot.streamer_id).raid_slot_note,
    "Browser note after reconnect",
  );
  // Keyboard assignment adds another performance for the same streamer.
  await page
    .locator(".request-card")
    .getByRole("button", { name: "Browser Musician", exact: true })
    .click();
  const unassigned = (await current()).slots.find((slot) => !slot.streamer_id);
  await page.getByLabel("Assign to slot").selectOption(String(unassigned.id));
  await page
    .getByRole("button", { name: "Assign request", exact: true })
    .click();
  await waitSaved();
  assert.equal(
    (await current()).slots.filter((slot) => slot.streamer_id).length,
    2,
  );
  await page.reload();
  await page.getByLabel("Choose event").selectOption(String(fixture.event_id));
  await page.getByText("Private draft loaded.", { exact: true }).waitFor();
  assert.equal(await page.locator(".timeline-card.assigned").count(), 2);
  await page.getByRole("button", { name: "Publish schedule" }).click();
  await page
    .getByText(
      "Published. The public schedule and confirmed performances are updated.",
      { exact: true },
    )
    .waitFor();
  await publicPage.reload();
  assert.equal(
    await publicPage
      .getByRole("heading", { name: "Browser Musician", exact: true })
      .count(),
    2,
  );
  assert.equal(
    await publicPage.getByText("Browser note after reconnect").count(),
    1,
  );
  assert.match(
    await publicPage.locator(".visitor-time").first().textContent(),
    /Your time:.*P[DS]T/,
  );
  assert.equal(
    await publicPage
      .getByText("A biography pulled from Twitch", { exact: true })
      .count(),
    2,
  );
  assert.equal(
    await publicPage.getByText("Live on Twitch", { exact: true }).count(),
    2,
  );
  await publicPage.clock.install();
  await publicPage.reload();
  await publicPage.clock.fastForward(180001);
  assert.equal(
    await publicPage
      .getByText("Live status unavailable", { exact: true })
      .count(),
    2,
  );
  assert.equal(await publicPage.locator(".twitch-now").count(), 0);
  const fallbackContext = await browser.newContext({
    javaScriptEnabled: false,
  });
  const fallbackPage = await fallbackContext.newPage();
  await fallbackPage.goto(`${origin}/events/${fixture.event_id}/`);
  assert.equal(await fallbackPage.locator(".slot-list .slot").count(), 3);
  assert.equal(await fallbackPage.locator(".visitor-time:visible").count(), 0);
  assert.equal(
    await fallbackPage.getByText("Browser note after reconnect").count(),
    1,
  );
  await fallbackContext.close();
  // A streamer cancellation blocks the older draft and needs explicit recovery.
  stage("cancel");
  await page.getByRole("button", { name: "Reload draft" }).click();
  await page
    .getByRole("heading", { name: "This draft needs review" })
    .waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Publish schedule" }).isEnabled(),
    false,
  );
  await page
    .getByText("Reset draft from the current schedule", { exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Discard draft and reload schedule",
      exact: true,
    })
    .click();
  await page
    .getByText("Draft reset from the current schedule.", { exact: true })
    .waitFor();
  assert.equal(await page.locator(".timeline-card.assigned").count(), 1);
  // Review legacy durations together, then create/edit a new event through UI.
  await page.getByLabel("Choose event").selectOption(String(fixture.legacy_id));
  await page
    .getByRole("heading", { name: "Review existing slot lengths" })
    .waitFor();
  await page.getByRole("button", { name: "Confirm reviewed lengths" }).click();
  await waitSaved();
  assert.equal(
    await page
      .getByRole("heading", { name: "Review existing slot lengths" })
      .count(),
    0,
  );
  await page.getByRole("button", { name: "New event", exact: true }).click();
  const create = page
    .getByRole("heading", { name: "Create a private event" })
    .locator("..");
  await create
    .getByLabel("Event name", { exact: true })
    .fill("Created in browser");
  await create.getByLabel("Event date", { exact: true }).fill(fixture.day);
  await create
    .getByRole("button", { name: "Create event", exact: true })
    .click();
  await page
    .getByText("Private event created. Add its lineup below.", { exact: true })
    .waitFor();
  await page
    .getByRole("option", { name: /Created in browser/ })
    .waitFor({ state: "attached" });
  const createdId = await page.getByLabel("Choose event").inputValue();
  await page.getByText("Add an open slot", { exact: true }).click();
  await page.getByLabel("Slot time", { exact: true }).fill("13:00");
  await page.getByRole("button", { name: "Add slot", exact: true }).click();
  await waitSaved();
  await page.getByText("Event settings", { exact: true }).click();
  const settingsForm = page.getByRole("form", { name: "Event settings" });
  await changed(() =>
    settingsForm
      .getByLabel("Event name", { exact: true })
      .fill("Edited private event"),
  );
  await waitSaved();
  const created = await (
    await context.request.get(
      `${origin}/organizer/api/events/${createdId}/draft/`,
    )
  ).json();
  assert.equal(created.event.name, "Edited private event");
  assert.equal(created.slots.length, 1);
  assert.equal(
    (
      await publicContext.request.get(`${origin}/events/${createdId}/`)
    ).status(),
    404,
  );
  await page.getByText("Signup and public visibility", { exact: true }).click();
  await page
    .getByRole("button", { name: "Open or update signup with this lineup" })
    .click();
  await page
    .getByText(
      "Reviewed lineup released for eligible streamer signup. Public event remains hidden.",
      { exact: true },
    )
    .waitFor();
  assert.equal(
    (
      await publicContext.request.get(`${origin}/events/${createdId}/`)
    ).status(),
    404,
  );
  const streamerContext = await browser.newContext();
  await streamerContext.addCookies([
    {
      name: "sessionid",
      value: fixture.streamer_session_id,
      url: origin,
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  const signupPage = await streamerContext.newPage();
  await signupPage.goto(`${origin}/events/${createdId}/signup/`);
  assert.equal(await signupPage.getByRole("checkbox").count(), 1);
  await signupPage.getByRole("checkbox").check();
  await signupPage.getByRole("button", { name: "Submit preferences" }).click();
  await signupPage
    .getByRole("heading", { name: "Your account", exact: true })
    .waitFor();
  assert.equal(
    await signupPage
      .getByRole("region", { name: "Confirmed performances" })
      .getByRole("link", { name: "Edited private event", exact: true })
      .count(),
    0,
  );
  await page.bringToFront();
  await page.getByRole("button", { name: "Publish schedule" }).click();
  await page
    .getByText(
      "Published. The public schedule and confirmed performances are updated.",
      { exact: true },
    )
    .waitFor();
  await publicPage.goto(`${origin}/events/${createdId}/`);
  await publicPage
    .getByRole("heading", { name: "Edited private event", exact: true })
    .waitFor();
  await page.bringToFront();
  await page.getByText("Import a pasted lineup", { exact: true }).click();
  await page
    .getByLabel("Pasted lineup", { exact: true })
    .fill(
      "*07.09.2026* Pre-Pary: browser_musician 1p: 2p: browser_musician 3p: unknown_login",
    );
  await page
    .getByRole("button", { name: "Review pasted lineup", exact: true })
    .click();
  await page
    .getByRole("region", { name: "Imported slot 1" })
    .getByLabel("Exclude row")
    .check();
  await page
    .getByLabel("Imported event date", { exact: true })
    .fill(fixture.day);
  const unmatchedRow = page.getByRole("region", { name: "Imported slot 4" });
  const twitchUsername = unmatchedRow.getByLabel("Twitch username");
  const lookupButton = unmatchedRow.getByRole("button", {
    name: "Look up Twitch channel",
  });
  assert.equal(await twitchUsername.inputValue(), "unknown_login");
  await twitchUsername.fill("   ");
  assert.equal(await lookupButton.isDisabled(), true);
  let lookupRequests = 0;
  const lookupRoute = "**/organizer/api/events/*/twitch/lookup/";
  await page.route(lookupRoute, async (route) => {
    lookupRequests += 1;
    assert.deepEqual(route.request().postDataJSON(), {
      login: "unknown_login",
    });
    await route.fulfill({
      json: {
        profile: { display_name: "Unknown channel" },
        streamer_id: null,
        lookup_token: "browser-test-match",
      },
    });
  });
  await twitchUsername.fill("invalid channel!");
  await lookupButton.click();
  await page
    .getByText(
      "Enter a Twitch username using only letters, numbers, and underscores.",
      { exact: true },
    )
    .waitFor();
  assert.equal(lookupRequests, 0);
  await twitchUsername.fill(" Unknown_Login ");
  await lookupButton.click();
  await unmatchedRow.getByLabel("Name shown on Fuinoise").waitFor();
  assert.equal(lookupRequests, 1);
  await page.unroute(lookupRoute);
  await page
    .getByRole("region", { name: "Imported slot 4" })
    .getByLabel("Channel match")
    .selectOption("");
  await page
    .getByLabel("Replace the entire working lineup.", { exact: false })
    .check();
  await page.setViewportSize({ width: 352, height: 900 });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    true,
    "Expanded import review fits a phone viewport",
  );
  await page.screenshot({
    path: path.join(dir, "import-352.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1400 });
  await page
    .getByLabel("I reviewed the dates, times, lengths, and channel matches.", {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", { name: "Apply reviewed import", exact: true })
    .click();
  await page
    .getByText("Reviewed import saved privately. Public schedule unchanged.", {
      exact: true,
    })
    .waitFor();
  await publicPage.reload();
  assert.equal(
    await publicPage
      .getByRole("heading", { name: "Open slot", exact: true })
      .count(),
    1,
  );
  assert.equal(await page.locator(".timeline-card").count(), 3);
  await page
    .getByRole("button", { name: "Publish schedule", exact: true })
    .click();
  await page
    .getByText(
      "Published. The public schedule and confirmed performances are updated.",
      { exact: true },
    )
    .waitFor();
  await publicPage.reload();
  assert.equal(
    await publicPage
      .getByRole("heading", { name: "Open slot", exact: true })
      .count(),
    2,
  );
  assert.equal(
    await publicPage
      .getByRole("heading", { name: "Browser Musician", exact: true })
      .count(),
    1,
  );
  // Reload a populated event at each viewport; capture settled screenshots.
  await page.getByLabel("Choose event").selectOption(String(fixture.event_id));
  await page.getByText("Private draft loaded.", { exact: true }).waitFor();
  for (const width of [1440, 768, 392, 352]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "Overflow at " + width,
    );
    await page.screenshot({
      path: path.join(dir, `timeline-${width}.png`),
      fullPage: true,
    });
  }
  // Read alerts without JavaScript and queue a failed Discord delivery safely.
  const inboxContext = await browser.newContext({
    javaScriptEnabled: false,
    viewport: { width: 352, height: 900 },
  });
  await inboxContext.addCookies([
    {
      name: "sessionid",
      value: fixture.streamer_session_id,
      url: origin,
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  const inboxPage = await inboxContext.newPage();
  await inboxPage.goto(`${origin}/notifications/`);
  assert.equal(
    await inboxPage
      .getByRole("heading", { name: "Performance canceled", exact: true })
      .count(),
    1,
  );
  assert.ok(
    (await inboxPage
      .getByRole("heading", { name: "Performance confirmed", exact: true })
      .count()) >= 2,
  );
  assert.equal(
    await inboxPage.getByText("For organizers", { exact: false }).count(),
    0,
  );
  const firstNotice = inboxPage.locator(".notification-card").first();
  const firstId = await firstNotice.getAttribute("id");
  await firstNotice
    .getByRole("button", { name: "Mark as read", exact: true })
    .click();
  await inboxPage
    .locator(`#${firstId}`)
    .getByText("Read", { exact: false })
    .waitFor();
  await inboxPage.reload();
  assert.equal(
    await inboxPage
      .locator(`#${firstId}`)
      .getByRole("button", { name: "Mark as read", exact: true })
      .count(),
    0,
  );
  assert.ok(
    await inboxPage.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await inboxPage.screenshot({
    path: path.join(dir, "notifications-352.png"),
    fullPage: true,
  });
  assert.equal(
    (
      await inboxContext.request.get(`${origin}/organizer/notifications/`)
    ).status(),
    403,
  );
  assert.equal(
    (
      await publicContext.request.get(`${origin}/notifications/`, {
        maxRedirects: 0,
      })
    ).status(),
    302,
  );
  stage("fail_notifications");
  await page.goto(`${origin}/organizer/notifications/?status=failed`);
  await page
    .getByRole("heading", { name: "Discord deliveries", exact: true })
    .waitFor();
  const failedDelivery = page.locator(".notification-card").first();
  const deliveryId = await failedDelivery.getAttribute("id");
  await failedDelivery
    .getByRole("button", { name: "Queue retry", exact: true })
    .click();
  await page
    .getByText("Retry queued for the next delivery run.", {
      exact: true,
    })
    .waitFor();
  await page
    .locator(`#${deliveryId}`)
    .getByText("Waiting to send", { exact: true })
    .waitFor();
  for (const width of [1440, 352]) {
    await page.setViewportSize({ width, height: 900 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: path.join(dir, `deliveries-${width}.png`),
      fullPage: true,
    });
  }
  await inboxContext.close();
  assert.deepEqual(errors, []);
  console.log(
    "Passed browser workflow: Timeline editing and publication, reviewed import, public information, private notifications and persisted read marks without JavaScript, organizer delivery retry, and desktop/phone layouts.",
  );
  console.log("Screenshots: " + dir);
})()
  .catch((error) => {
    console.error(error.stack);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      server.kill("SIGTERM");
      await new Promise((resolve) => server.once("exit", resolve));
    }
    for (const file of [
      "browser.sqlite3",
      "browser.sqlite3-journal",
      "fixture.json",
    ])
      fs.rmSync(path.join(dir, file), { force: true });
  });
