import React, { useEffect, useState } from "react";

function rowDate(day, offset) {
  if (!day) return "";
  const instant = new Date(`${day}T00:00:00Z`);
  instant.setUTCDate(instant.getUTCDate() + offset);
  return Number.isFinite(instant.getTime())
    ? instant.toISOString().slice(0, 10)
    : "";
}

export default function ImportReview({
  event,
  version,
  catalog,
  disabled,
  preview,
  lookup,
  apply,
}) {
  const [source, setSource] = useState("");
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [replace, setReplace] = useState(false);
  useEffect(() => {
    setReview(null);
    setReviewed(false);
  }, [version]);
  async function parse() {
    setLoading(true);
    setError("");
    setReviewed(false);
    try {
      const data = await preview(source);
      setReview({
        ...data,
        version,
        rows: data.rows.map((row) => ({
          ...row,
          date: rowDate(data.date, row.day_offset),
          minutes: event.default_slot_duration_minutes,
          excluded: false,
          lookup_token: "",
          display_name: "",
          login: "",
          selection: row.needs_match ? "unmatched" : (row.streamer_id ?? ""),
        })),
      });
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }
  function update(index, changes) {
    setReview((current) => ({
      ...current,
      rows: current.rows.map((row, position) =>
        position === index ? { ...row, ...changes } : row,
      ),
    }));
    setReviewed(false);
  }
  function changeDate(day) {
    setReview((current) => ({
      ...current,
      date: day,
      rows: current.rows.map((row) => ({
        ...row,
        date: rowDate(day, row.day_offset),
      })),
    }));
    setReviewed(false);
  }
  async function match(index) {
    const row = review.rows[index];
    setLoading(true);
    setError("");
    try {
      const result = await lookup(row.login);
      update(index, {
        needs_match: false,
        streamer_id: result.streamer_id,
        lookup_token: result.lookup_token,
        display_name: result.profile.display_name,
        matched_name: result.profile.display_name,
        selection: result.streamer_id || "twitch",
      });
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }
  async function submit(e) {
    e.preventDefault();
    setError("");
    const rows = review.rows.filter((row) => !row.excluded);
    if (rows.some((row) => row.needs_match)) {
      setError(
        "Resolve every channel match, choose Open slot, or exclude its row.",
      );
      return;
    }
    const ok = await apply({
      version: review.version,
      event: { ...event, name: review.name, date: review.date },
      replace,
      reviewed,
      rows: rows.map((row) => ({
        local_start: `${row.date}T${row.time}:00`,
        duration_minutes: Number(row.minutes),
        streamer_id: row.streamer_id,
        lookup_token: row.lookup_token,
        display_name: row.display_name,
      })),
    });
    if (ok) {
      setReview(null);
      setReviewed(false);
    }
  }
  return (
    <details className="lineup-import workspace-panel">
      <summary>Import a pasted lineup</summary>
      <p>
        Review each date, time, length, and channel before applying it to the
        private draft. Empty time labels become open slots.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          parse();
        }}
      >
        <fieldset disabled={disabled || loading}>
          <label>
            Pasted lineup
            <textarea
              maxLength="12000"
              required
              value={source}
              placeholder={
                "2026-10-10 Title: Saturday train\n10a: musician 11a: 12p: another_channel"
              }
              onChange={(e) => {
                setSource(e.target.value);
                setReview(null);
                setReviewed(false);
              }}
            />
          </label>
          <button>Review pasted lineup</button>
        </fieldset>
      </form>
      {loading && <p role="status">Loading the review…</p>}
      {error && (
        <p role="alert" className="workspace-error">
          {error}
        </p>
      )}
      {review && (
        <form aria-label="Reviewed lineup" onSubmit={submit}>
          <fieldset disabled={disabled || loading}>
            {review.warnings.map((warning, index) => (
              <p className="workspace-error" key={index}>
                {warning}
              </p>
            ))}
            <div className="form-pair">
              <label>
                Imported event name
                <input
                  required
                  maxLength="255"
                  value={review.name}
                  onChange={(e) => {
                    setReview((current) => ({
                      ...current,
                      name: e.target.value,
                    }));
                    setReviewed(false);
                  }}
                />
              </label>
              <label>
                Imported event date
                <input
                  type="date"
                  required
                  value={review.date}
                  onChange={(e) => changeDate(e.target.value)}
                />
              </label>
            </div>
            {review.date_candidates.length > 1 && (
              <div className="actions">
                {review.date_candidates.map((day) => (
                  <button
                    key={day}
                    type="button"
                    onClick={() => changeDate(day)}
                  >
                    Use {day}
                  </button>
                ))}
              </div>
            )}
            <p className="muted small">
              All imported times use {event.event_time_zone}. Changing the
              imported event date updates the proposed dates below. Review any
              overnight rows.
            </p>
            {review.rows.map((row, index) => (
              <section
                className="import-row"
                key={index}
                aria-label={`Imported slot ${index + 1}`}
              >
                <div className="import-row-heading">
                  <strong>{row.source_name || "Open slot"}</strong>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={row.excluded}
                      onChange={(e) =>
                        update(index, { excluded: e.target.checked })
                      }
                    />
                    Exclude row
                  </label>
                </div>
                {row.issue && <p className="import-issue">{row.issue}</p>}
                <fieldset disabled={row.excluded}>
                  <div className="import-fields">
                    <label>
                      Slot date
                      <input
                        type="date"
                        required
                        value={row.date}
                        onChange={(e) =>
                          update(index, { date: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Slot time
                      <input
                        type="time"
                        required
                        value={row.time}
                        onChange={(e) =>
                          update(index, { time: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Minutes
                      <input
                        type="number"
                        min="1"
                        step="1"
                        required
                        value={row.minutes}
                        onChange={(e) =>
                          update(index, { minutes: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Channel match
                      <select
                        value={row.selection}
                        onChange={(e) => {
                          const selection = e.target.value;
                          update(index, {
                            selection,
                            streamer_id:
                              selection &&
                              selection !== "unmatched" &&
                              selection !== "twitch"
                                ? Number(selection)
                                : null,
                            needs_match: selection === "unmatched",
                            lookup_token:
                              selection === "twitch" ? row.lookup_token : "",
                          });
                        }}
                      >
                        <option value="unmatched" disabled>
                          Choose a channel
                        </option>
                        <option value="">Open slot</option>
                        {row.lookup_token && (
                          <option value="twitch">
                            Twitch match: {row.matched_name}
                          </option>
                        )}
                        {catalog.streamers.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.display_name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {row.needs_match && (
                    <div className="lookup-fields">
                      <label>
                        Twitch username
                        <input
                          value={row.login}
                          maxLength="25"
                          onChange={(e) =>
                            update(index, { login: e.target.value })
                          }
                        />
                      </label>
                      <button type="button" onClick={() => match(index)}>
                        Look up Twitch channel
                      </button>
                    </div>
                  )}
                  {row.lookup_token && (
                    <label>
                      Name shown on Fuinoise
                      <input
                        maxLength="80"
                        required
                        value={row.display_name}
                        onChange={(e) =>
                          update(index, { display_name: e.target.value })
                        }
                      />
                    </label>
                  )}
                </fieldset>
              </section>
            ))}
            <label className="check">
              <input
                type="checkbox"
                checked={replace}
                onChange={(e) => {
                  setReplace(e.target.checked);
                  setReviewed(false);
                }}
              />
              Replace the entire working lineup. Leave unchecked to add these
              slots.
            </label>
            <label className="check">
              <input
                type="checkbox"
                required
                checked={reviewed}
                onChange={(e) => setReviewed(e.target.checked)}
              />
              I reviewed the dates, times, lengths, and channel matches.
            </label>
            <button disabled={!reviewed}>Apply reviewed import</button>
          </fieldset>
        </form>
      )}
    </details>
  );
}
