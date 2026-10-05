import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import { dayTicks, moveSlots, slotInput, slotLayout } from "./schedule.js";
import "./workspace.css";

const root = document.getElementById("organizer-root");
const base = root.dataset.api;
const csrf = root.dataset.csrf;

function errorText(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(errorText).join(" ");
  if (value && typeof value === "object")
    return Object.entries(value)
      .map(([key, val]) => `${key}: ${errorText(val)}`)
      .join(" ");
  return "The change could not be saved.";
}
async function api(url, method = "GET", data) {
  let response;
  try {
    response = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
  } catch {
    throw new Error(
      "Connection lost. Your saved draft is unchanged. Reconnect and retry.",
    );
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(errorText(body.errors || body.detail || body));
  return body;
}
function pretty(start, zone, date = false) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone: zone,
    ...(date ? { month: "short", day: "numeric" } : {}),
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(start));
}

// A rejected edit stays in its form for correction/retry. Publication and event
// switching remain blocked until it is saved or explicitly discarded.
function AutoForm({ values, onCommit, onDirty, busy, children, label }) {
  const [fields, setFields] = useState(values);
  const [dirty, setDirty] = useState(false);
  const [revision, setRevision] = useState(0);
  const form = useRef(null);
  const latest = useRef({ fields, dirty, onCommit });
  latest.current = { fields, dirty, onCommit };
  const valueKey = JSON.stringify(values);
  useEffect(() => {
    setFields(JSON.parse(valueKey));
    setDirty(false);
    onDirty(false);
  }, [valueKey]);
  useEffect(() => () => onDirty(false), []);
  async function commit() {
    if (!latest.current.dirty || !form.current.reportValidity()) return;
    const ok = await latest.current.onCommit(latest.current.fields);
    if (ok) {
      setDirty(false);
      onDirty(false);
    }
  }
  useEffect(() => {
    if (!revision) return;
    const timer = setTimeout(() => {
      if (form.current?.checkValidity()) commit();
    }, 900);
    return () => clearTimeout(timer);
  }, [revision]);
  function change(event) {
    const target = event.target;
    const value = target.type === "checkbox" ? target.checked : target.value;
    setFields((current) => ({ ...current, [target.name]: value }));
    setDirty(true);
    onDirty(true);
    setRevision((current) => current + 1);
  }
  return (
    <form
      ref={form}
      aria-label={label}
      onChange={change}
      onSubmit={(event) => {
        event.preventDefault();
        commit();
      }}
    >
      <fieldset disabled={busy}>
        {children(fields)}
        <p className="muted small">
          Valid edits save automatically after a short pause.
        </p>
        <div className="actions">
          <button type="submit" disabled={!dirty}>
            Save now / retry
          </button>
          {dirty && (
            <button
              type="button"
              onClick={() => {
                setFields(values);
                setDirty(false);
                onDirty(false);
                setRevision(0);
              }}
            >
              Discard unsaved edit
            </button>
          )}
        </div>
      </fieldset>
    </form>
  );
}

function EventFields({ fields, catalog }) {
  return (
    <>
      <label>
        Event name
        <input
          name="name"
          required
          maxLength="255"
          value={fields.name}
          onChange={() => {}}
        />
      </label>
      <div className="form-pair">
        <label>
          Event date
          <input
            name="date"
            type="date"
            required
            value={fields.date}
            onChange={() => {}}
          />
        </label>
        <label>
          Default minutes
          <input
            name="default_slot_duration_minutes"
            type="number"
            required
            min="1"
            step="1"
            value={fields.default_slot_duration_minutes}
            onChange={() => {}}
          />
        </label>
      </div>
      <label>
        Community
        <select
          name="community_id"
          required
          value={fields.community_id}
          onChange={() => {}}
        >
          {catalog.communities.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Event time zone
        <select
          name="event_time_zone"
          value={fields.event_time_zone}
          onChange={() => {}}
        >
          {catalog.time_zones.map((zone) => (
            <option key={zone}>{zone}</option>
          ))}
        </select>
      </label>
      <label>
        Description
        <textarea
          name="description"
          value={fields.description}
          onChange={() => {}}
        />
      </label>
      <label className="check">
        <input
          name="signup_before_publication"
          type="checkbox"
          checked={fields.signup_before_publication}
          onChange={() => {}}
        />
        Allow signup before public publication
      </label>
    </>
  );
}
function eventInput(fields) {
  return {
    ...fields,
    community_id: Number(fields.community_id),
    default_slot_duration_minutes: Number(fields.default_slot_duration_minutes),
  };
}

function App() {
  const [catalog, setCatalog] = useState(null);
  const [work, setWork] = useState(null);
  const workRef = useRef(null);
  workRef.current = work;
  const [selected, setSelected] = useState(null);
  const [selectedRequest, setSelectedRequest] = useState(null);
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [dirty, setDirty] = useState({});
  const hasEdits = Object.values(dirty).some(Boolean);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Loading…");
  const [newEvent, setNewEvent] = useState(false);
  const railRef = useRef(null);
  const dragging = useRef(null);
  const markEvent = useCallback(
    (value) => setDirty((current) => ({ ...current, event: value })),
    [],
  );
  const markSlot = useCallback(
    (value) => setDirty((current) => ({ ...current, slot: value })),
    [],
  );
  const blocked = busy || hasEdits || work?.stale;

  async function run(
    action,
    success = "Draft saved. Public schedule unchanged.",
  ) {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setStatus("Saving…");
    try {
      const data = await action();
      if (data) {
        setWork(data);
        workRef.current = data;
      }
      setStatus(success);
      return true;
    } catch (failure) {
      setError(failure.message);
      setStatus("Change not saved.");
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function refreshCatalog() {
    const data = await api(base);
    setCatalog(data);
    return data;
  }
  useEffect(() => {
    refreshCatalog()
      .then(() => setStatus("Choose an event to organize."))
      .catch((failure) => {
        setError(failure.message);
        setStatus("Unable to load events.");
      });
  }, []);
  async function loadEvent(id) {
    if (busyRef.current || hasEdits) return;
    if (!id) {
      setWork(null);
      setSelected(null);
      return;
    }
    const ok = await run(
      () => api(`${base}${id}/draft/`),
      "Private draft loaded.",
    );
    if (ok) {
      setSelected(null);
      setSelectedRequest(null);
      setDay(workRef.current.event.date);
      setDirty({});
    }
  }
  async function save(slots, event = workRef.current.event) {
    const current = workRef.current;
    return run(() =>
      api(`${base}${current.event_id}/draft/`, "PUT", {
        version: current.version,
        event: eventInput(event),
        slots: slots.map(slotInput),
      }),
    );
  }
  async function action(name) {
    const current = workRef.current;
    const ok = await run(
      () =>
        api(`${base}${current.event_id}/${name}/`, "POST", {
          version: current.version,
        }),
      name === "publish"
        ? "Published. The public schedule and confirmed performances are updated."
        : name === "signup"
          ? "Reviewed lineup released for eligible streamer signup. Public event remains hidden."
          : "Draft reset from the current schedule.",
    );
    if (ok) {
      setSelected(null);
      setDirty({});
      await refreshCatalog().catch(() => {});
    }
  }
  async function assign(requestId, slotId) {
    if (blocked) return;
    const item = work.requests.find((request) => request.id === requestId);
    if (!item || item.status !== "submitted") return;
    const ok = await run(() =>
      api(`${base}${work.event_id}/assign/`, "POST", {
        version: work.version,
        request_id: requestId,
        request_version: item.version,
        slot_id: slotId,
      }),
    );
    if (ok) {
      setSelected(slotId);
      setSelectedRequest(requestId);
    }
  }
  function startDrag(event, type, id) {
    if (blocked) {
      event.preventDefault();
      return;
    }
    const payload = { type, id, eventId: work.event_id };
    dragging.current = payload;
    event.dataTransfer.setData(
      "application/x-fuinoise",
      JSON.stringify(payload),
    );
    event.dataTransfer.effectAllowed = "move";
  }
  function dropped(event, target) {
    event.preventDefault();
    event.stopPropagation();
    if (blocked) return;
    let payload;
    try {
      payload = JSON.parse(
        event.dataTransfer.getData("application/x-fuinoise"),
      );
    } catch {
      payload = dragging.current;
    }
    dragging.current = null;
    if (!payload || payload.eventId !== work.event_id) return;
    if (payload.type === "request") {
      if (target.slotId) assign(payload.id, target.slotId);
      else
        setError(
          "Drop a request onto an open slot. Add a slot first if needed.",
        );
    } else if (payload.type === "slot" && target.start) {
      save(moveSlots(work.slots, payload.id, target.start));
      setSelected(payload.id);
    }
  }
  const zone = work?.event.event_time_zone || "UTC";
  const ticks = useMemo(
    () => (work && day ? dayTicks(day, zone) : []),
    [day, zone, work?.event_id],
  );
  useEffect(() => {
    if (!ticks.length || !railRef.current) return;
    const slots = work.slots
      .map((slot) => slotLayout(slot, ticks))
      .filter(Boolean);
    railRef.current.scrollTop = Math.max(
      0,
      slots.length
        ? Math.min(...slots.map((slot) => slot.top)) - 60
        : ticks.findIndex((tick) => tick.local.slice(11, 13) === "10") * 30,
    );
  }, [day, work?.event_id, zone]);
  const slot = work?.slots.find((item) => item.id === selected);
  const activeRequest = work?.requests.find(
    (item) => item.id === selectedRequest,
  );
  const requestedSlots = new Set(
    activeRequest?.preferences.map((pref) => pref.slot_id).filter(Boolean),
  );
  const eventDefaults = catalog && {
    name: "",
    date: new Date().toISOString().slice(0, 10),
    description: "",
    community_id: catalog.communities[0]?.id || "",
    event_time_zone: catalog.communities[0]?.default_time_zone || "UTC",
    default_slot_duration_minutes: 60,
    signup_before_publication: false,
  };
  return (
    <div className="organizer">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">Organizer workspace</p>
          <h1>Timeline</h1>
        </div>
        <a href="/organizer/eligibility/">Review eligibility ↗</a>
      </div>
      <div className="workspace-toolbar">
        <label>
          Event
          <select
            aria-label="Choose event"
            disabled={busy || hasEdits}
            value={work?.event_id || ""}
            onChange={(event) => loadEvent(event.target.value)}
          >
            <option value="">Choose an event</option>
            {catalog?.events.map((item) => (
              <option key={item.id} value={item.id}>
                {item.date} · {item.name} ({item.publication_status})
              </option>
            ))}
          </select>
        </label>
        {catalog?.can_create && (
          <button
            disabled={busy || hasEdits || !catalog.communities.length}
            onClick={() => setNewEvent(!newEvent)}
          >
            New event
          </button>
        )}
        {work && (
          <>
            <span className="badge">{work.publication_status}</span>
            <button disabled={blocked} onClick={() => action("publish")}>
              Publish schedule
            </button>
            <button
              disabled={busy || hasEdits}
              onClick={() => loadEvent(work.event_id)}
            >
              Reload draft
            </button>
          </>
        )}
      </div>
      <p role="status" className="save-status">
        {status}
        {hasEdits && " Unsaved form edits."}
      </p>
      {error && (
        <div role="alert" className="workspace-error">
          {error}
        </div>
      )}
      {!catalog && (
        <button
          disabled={busy}
          onClick={() =>
            run(async () => {
              await refreshCatalog();
              return null;
            }, "Choose an event to organize.")
          }
        >
          Retry loading events
        </button>
      )}
      {catalog && !catalog.communities.length && (
        <p className="workspace-error">
          Create the Fuinoise community in maintenance Admin before creating
          events.
        </p>
      )}
      {newEvent && catalog && (
        <section className="workspace-panel new-event">
          <h2>Create a private event</h2>
          <CreateForm
            catalog={catalog}
            values={eventDefaults}
            busy={busy}
            create={async (fields) => {
              const ok = await run(
                () => api(base, "POST", eventInput(fields)),
                "Private event created. Add its lineup below.",
              );
              if (ok) {
                setNewEvent(false);
                setSelected(null);
                setDay(workRef.current.event.date);
                await refreshCatalog().catch(() => {});
              }
            }}
          />
        </section>
      )}
      {work?.stale && (
        <section className="workspace-error" role="alert">
          <h2>This draft needs review</h2>
          <p>
            The schedule changed after this draft started. Your draft is
            preserved below, but editing and publication are blocked.
          </p>
          <details>
            <summary>Reset draft from the current schedule</summary>
            <p>
              This discards all private edits. Review the displayed draft before
              resetting.
            </p>
            <button disabled={busy || hasEdits} onClick={() => action("reset")}>
              Discard draft and reload schedule
            </button>
          </details>
        </section>
      )}
      {work && (
        <div className="workspace-grid">
          <section
            className="workspace-panel requests-panel"
            aria-label="Streamer requests"
          >
            <h2>
              Requests{" "}
              <span className="muted">
                {
                  work.requests.filter((item) => item.status === "submitted")
                    .length
                }
              </span>
            </h2>
            <p className="muted small">
              Drag a card onto an open slot, or select it and use Assign.
              Preferred slots are highlighted.
            </p>
            {!work.requests.length && <p className="muted">No requests yet.</p>}
            {work.requests.map((item) => (
              <article
                key={item.id}
                className={`request-card ${item.id === selectedRequest ? "chosen" : ""}`}
                draggable={!blocked && item.status === "submitted"}
                onDragStart={(event) => startDrag(event, "request", item.id)}
                onDragEnd={() => {
                  dragging.current = null;
                }}
              >
                <button
                  className="card-title"
                  disabled={busy || hasEdits}
                  onClick={() => setSelectedRequest(item.id)}
                >
                  {item.name}
                </button>
                <p className="small">
                  {item.status} ·{" "}
                  {item.eligible ? "Eligible" : "Eligibility needs review"}
                </p>
                {item.notes && <p className="small">{item.notes}</p>}
                <ul className="preferences small">
                  {item.preferences.map((pref, index) => (
                    <li key={index}>
                      {pretty(pref.start, zone, true)} · {pref.duration_minutes}{" "}
                      min
                    </li>
                  ))}
                </ul>
              </article>
            ))}
            {activeRequest && (
              <RequestControls
                request={activeRequest}
                slots={work.slots}
                zone={zone}
                blocked={blocked}
                assign={assign}
                decline={async (notes) => {
                  await run(
                    () =>
                      api(
                        `${base}${work.event_id}/requests/${activeRequest.id}/decline/`,
                        "POST",
                        {
                          version: activeRequest.version,
                          organizer_notes: notes,
                        },
                      ),
                    "Request declined. Already confirmed performances remain assigned.",
                  );
                }}
              />
            )}
          </section>
          <section
            className="workspace-panel timeline-panel"
            aria-label="Schedule Timeline"
          >
            <div className="timeline-header">
              <h2>Working lineup</h2>
              <label>
                Timeline day
                <input
                  aria-label="Timeline day"
                  type="date"
                  required
                  value={day}
                  disabled={busy || hasEdits}
                  onChange={(event) => {
                    if (event.target.value) setDay(event.target.value);
                  }}
                />
              </label>
            </div>
            <p className="muted small">
              {zone} · Drag a slot by its ⋮⋮ handle to a new time. Conflicts
              keep the saved lineup intact.
            </p>
            <div ref={railRef} className="timeline-scroll">
              <div
                className="timeline-track"
                style={{ height: ticks.length * 30 }}
              >
                {ticks.map((tick, index) => (
                  <div
                    key={tick.start}
                    className={`time-tick ${index % 4 === 0 ? "hour-tick" : ""}`}
                    style={{ top: index * 30 }}
                    onDragOver={(event) => {
                      if (!blocked) event.preventDefault();
                    }}
                    onDrop={(event) => dropped(event, { start: tick.start })}
                    data-start={tick.start}
                  >
                    <span>{pretty(tick.start, zone)}</span>
                    <div className="drop-time" aria-hidden="true" />
                  </div>
                ))}
                {work.slots.map((item) => {
                  const layout = slotLayout(item, ticks);
                  if (!layout) return null;
                  return (
                    <div
                      key={item.id}
                      className={`timeline-card ${item.streamer_id ? "assigned" : "open"} ${item.id === selected ? "chosen" : ""} ${requestedSlots.has(item.source_slot_id) ? "preferred" : ""}`}
                      style={{ top: layout.top, height: layout.height }}
                      draggable={!blocked}
                      onDragStart={(event) => startDrag(event, "slot", item.id)}
                      onDragEnd={() => {
                        dragging.current = null;
                      }}
                      onDragOver={(event) => {
                        if (!blocked) event.preventDefault();
                      }}
                      onDrop={(event) => {
                        const tick =
                          ticks[
                            Math.min(
                              ticks.length - 1,
                              Math.max(
                                0,
                                Math.floor(
                                  (layout.top +
                                    event.clientY -
                                    event.currentTarget.getBoundingClientRect()
                                      .top) /
                                    30,
                                ),
                              ),
                            )
                          ];
                        dropped(event, { slotId: item.id, start: tick.start });
                      }}
                    >
                      <span
                        className="drag-handle"
                        draggable={!blocked}
                        aria-hidden="true"
                        title="Drag to move this slot"
                      >
                        ⋮⋮
                      </span>
                      <button
                        disabled={busy || hasEdits}
                        aria-label={`Edit ${item.name}, ${pretty(item.start, zone)}`}
                        onClick={() => {
                          setSelected(item.id);
                          if (innerWidth < 1000)
                            setTimeout(
                              () =>
                                document
                                  .getElementById("slot-details")
                                  ?.scrollIntoView({
                                    behavior: "smooth",
                                    block: "start",
                                  }),
                              0,
                            );
                        }}
                      >
                        <strong>
                          {layout.continuing && "↳ "}
                          {item.name}
                        </strong>
                        <span>
                          {pretty(item.start, zone)} ·{" "}
                          {item.duration_minutes ?? "Review duration"}
                          {item.duration_minutes ? " min" : ""}
                        </span>
                        {item.raid_slot_note && (
                          <span>{item.raid_slot_note}</span>
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
            {work.slots.some((item) => item.duration_minutes === null) && (
              <DurationReview
                slots={work.slots}
                zone={zone}
                defaultMinutes={work.event.default_slot_duration_minutes}
                busy={blocked}
                save={save}
              />
            )}
            <AddSlot
              busy={blocked}
              day={day}
              defaultMinutes={work.event.default_slot_duration_minutes}
              add={(fields) =>
                save([
                  ...work.slots,
                  {
                    id: null,
                    streamer_id: null,
                    local_start: `${fields.date}T${fields.time}:00`,
                    duration_minutes: Number(fields.minutes),
                    replay_url: "",
                    raid_slot_note: "",
                  },
                ])
              }
            />
          </section>
          <aside className="workspace-panel details-panel" aria-label="Details">
            <h2>Details</h2>
            {slot ? (
              <div id="slot-details">
                <h3>{slot.name}</h3>
                <AutoForm
                  key={slot.id}
                  label="Performance details"
                  values={{
                    local_start: slot.local_start,
                    duration_minutes: slot.duration_minutes ?? "",
                    streamer_id: slot.streamer_id ?? "",
                    replay_url: slot.replay_url,
                    raid_slot_note: slot.raid_slot_note,
                  }}
                  busy={busy || work.stale}
                  onDirty={markSlot}
                  onCommit={(fields) => {
                    const revised = {
                      ...slot,
                      duration_minutes: Number(fields.duration_minutes),
                      streamer_id: fields.streamer_id
                        ? Number(fields.streamer_id)
                        : null,
                      replay_url: fields.replay_url,
                      raid_slot_note: fields.raid_slot_note,
                    };
                    if (fields.local_start !== slot.local_start) {
                      delete revised.start;
                      revised.local_start = fields.local_start;
                    }
                    return save(
                      work.slots.map((item) =>
                        item.id === slot.id ? revised : item,
                      ),
                    );
                  }}
                >
                  {(fields) => (
                    <>
                      <label>
                        Start in event time
                        <input
                          name="local_start"
                          type="datetime-local"
                          step="1"
                          required
                          value={fields.local_start}
                          onChange={() => {}}
                        />
                      </label>
                      <label>
                        Planned minutes
                        <input
                          name="duration_minutes"
                          type="number"
                          required
                          min="1"
                          step="1"
                          value={fields.duration_minutes}
                          onChange={() => {}}
                        />
                      </label>
                      <label>
                        Performer
                        <select
                          name="streamer_id"
                          value={fields.streamer_id}
                          onChange={() => {}}
                        >
                          <option value="">Open slot</option>
                          {catalog.streamers.map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.display_name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <p className="muted small">
                        Use the request card’s Assign control to approve a
                        signup request. Manual selection also supports existing
                        lineup records.
                      </p>
                      <label>
                        Replay link
                        <input
                          name="replay_url"
                          type="url"
                          value={fields.replay_url}
                          onChange={() => {}}
                        />
                      </label>
                      <label>
                        Public slot note
                        <input
                          name="raid_slot_note"
                          maxLength="255"
                          value={fields.raid_slot_note}
                          onChange={() => {}}
                        />
                      </label>
                    </>
                  )}
                </AutoForm>
                <details>
                  <summary>Remove this slot</summary>
                  <p>
                    Removes it from the draft. Publication applies the removal
                    publicly.
                  </p>
                  <button
                    disabled={blocked}
                    onClick={() => {
                      save(
                        work.slots.filter((item) => item.id !== slot.id),
                      ).then((ok) => {
                        if (ok) setSelected(null);
                      });
                    }}
                  >
                    Remove slot from draft
                  </button>
                </details>
              </div>
            ) : (
              <p className="muted">
                Select a slot to edit its time, length, performer, or replay.
              </p>
            )}
            <details className="event-settings">
              <summary>Event settings</summary>
              <AutoForm
                label="Event settings"
                values={work.event}
                busy={busy || work.stale}
                onDirty={markEvent}
                onCommit={(fields) => save(work.slots, fields)}
              >
                {(fields) => <EventFields fields={fields} catalog={catalog} />}
              </AutoForm>
              <p className="muted small">
                Changing the time zone changes display and future local entries;
                existing performances keep their actual instants.
              </p>
            </details>
            <details className="visibility">
              <summary>Signup and public visibility</summary>
              {work.publication_status !== "published" && (
                <>
                  <p>
                    Release this reviewed lineup to eligible streamers for
                    signup. This updates their available slots immediately,
                    keeps the public event hidden, and sends no assignment
                    confirmation. Later edits stay private until another release
                    or publication.
                  </p>
                  <button disabled={blocked} onClick={() => action("signup")}>
                    Open or update signup with this lineup
                  </button>
                </>
              )}
              <p>
                These changes take effect immediately and make the current draft
                stale. Reset the draft afterward.
              </p>
              <button
                disabled={blocked}
                onClick={() =>
                  run(
                    () =>
                      api(`${base}${work.event_id}/visibility/`, "POST", {
                        schedule_version: work.schedule_version,
                        publication_status: "draft",
                        signup_before_publication:
                          !work.event.signup_before_publication,
                      }),
                    "Signup policy updated. Reset the draft before editing.",
                  ).then((ok) => {
                    if (ok) refreshCatalog().catch(() => {});
                  })
                }
              >
                {work.event.signup_before_publication
                  ? "Close early signup and hide event"
                  : "Open early signup and hide event"}
              </button>
              <button
                disabled={blocked}
                onClick={() =>
                  run(
                    () =>
                      api(`${base}${work.event_id}/visibility/`, "POST", {
                        schedule_version: work.schedule_version,
                        publication_status: "private",
                        signup_before_publication: false,
                      }),
                    "Event hidden and signup closed. Reset the draft before editing.",
                  ).then((ok) => {
                    if (ok) refreshCatalog().catch(() => {});
                  })
                }
              >
                Make event private and close signup
              </button>
            </details>
            <p className="muted small">
              Draft version {work.version}. Assignment confirmations happen when
              you publish.
            </p>
            <details>
              <summary>Discard the working draft</summary>
              <p>
                This replaces every private edit with the current published or
                saved schedule.
              </p>
              <button
                disabled={busy || hasEdits}
                onClick={() => action("reset")}
              >
                Discard private edits and reset
              </button>
            </details>
          </aside>
        </div>
      )}
    </div>
  );
}

function CreateForm({ catalog, values, busy, create }) {
  const [fields, setFields] = useState(values);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        create(fields);
      }}
      onChange={(event) => {
        const target = event.target;
        setFields((current) => ({
          ...current,
          [target.name]:
            target.type === "checkbox" ? target.checked : target.value,
        }));
      }}
    >
      <fieldset disabled={busy}>
        <EventFields fields={fields} catalog={catalog} />
        <button type="submit">Create event</button>
      </fieldset>
    </form>
  );
}
function DurationReview({ slots, zone, defaultMinutes, busy, save }) {
  const unknown = slots.filter((item) => item.duration_minutes === null);
  const [minutes, setMinutes] = useState({});
  return (
    <section className="workspace-error">
      <h3>Review existing slot lengths</h3>
      <p>
        These older slots have no planned duration. Review all their lengths
        together before editing or publishing.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save(
            slots.map((item) =>
              item.duration_minutes === null
                ? {
                    ...item,
                    duration_minutes: Number(
                      minutes[item.id] ?? defaultMinutes,
                    ),
                  }
                : item,
            ),
          );
        }}
      >
        <fieldset disabled={busy}>
          {unknown.map((item) => (
            <label key={item.id}>
              {item.name} · {pretty(item.start, zone, true)}
              <input
                aria-label={`Minutes for ${item.name}, ${pretty(item.start, zone)}`}
                type="number"
                min="1"
                step="1"
                required
                value={minutes[item.id] ?? defaultMinutes}
                onChange={(event) =>
                  setMinutes((current) => ({
                    ...current,
                    [item.id]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
          <button>Confirm reviewed lengths</button>
        </fieldset>
      </form>
    </section>
  );
}
function AddSlot({ busy, day, defaultMinutes, add }) {
  const [date, setDate] = useState(day);
  const [time, setTime] = useState("10:00");
  const [minutes, setMinutes] = useState(defaultMinutes);
  useEffect(() => setDate(day), [day]);
  useEffect(() => setMinutes(defaultMinutes), [defaultMinutes]);
  return (
    <details className="add-slot">
      <summary>Add an open slot</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          add({ date, time, minutes });
        }}
      >
        <fieldset disabled={busy}>
          <label>
            Slot date
            <input
              type="date"
              required
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
          </label>
          <div className="form-pair">
            <label>
              Slot time
              <input
                type="time"
                step="60"
                required
                value={time}
                onChange={(event) => setTime(event.target.value)}
              />
            </label>
            <label>
              Slot minutes
              <input
                type="number"
                min="1"
                step="1"
                required
                value={minutes}
                onChange={(event) => setMinutes(event.target.value)}
              />
            </label>
          </div>
          <button>Add slot</button>
        </fieldset>
      </form>
    </details>
  );
}
function RequestControls({ request, slots, zone, blocked, assign, decline }) {
  const [slotId, setSlotId] = useState("");
  const [notes, setNotes] = useState(request.organizer_notes);
  useEffect(() => {
    setSlotId("");
    setNotes(request.organizer_notes);
  }, [request.id, request.organizer_notes]);
  return (
    <div className="request-controls">
      <h3>Review {request.name}</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          assign(request.id, Number(slotId));
        }}
      >
        <fieldset disabled={blocked || request.status !== "submitted"}>
          <label>
            Assign to slot
            <select
              required
              value={slotId}
              onChange={(event) => setSlotId(event.target.value)}
            >
              <option value="">Choose a slot</option>
              {slots
                .filter(
                  (item) =>
                    !item.streamer_id ||
                    item.streamer_id === request.streamer_id,
                )
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {pretty(item.start, zone, true)} ·{" "}
                    {item.duration_minutes ?? "?"} min
                  </option>
                ))}
            </select>
          </label>
          <button>Assign request</button>
        </fieldset>
      </form>
      <details>
        <summary>Decline this request</summary>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            decline(notes);
          }}
        >
          <fieldset disabled={blocked || request.status !== "submitted"}>
            <label>
              Private organizer notes
              <textarea
                maxLength="2000"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </label>
            <button>Decline request</button>
          </fieldset>
        </form>
      </details>
    </div>
  );
}

createRoot(root).render(<App />);
