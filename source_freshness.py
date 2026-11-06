"""Persist per-source fetch times independently of snapshot generation time."""


def record_health(state, health, checked_at):
    ledger = state.setdefault("source_freshness", {})
    for row in health:
        source = row["source"]
        previous = ledger.get(source, {})
        if row["state"] == "disabled":
            row.update(last_checked_at=previous.get("last_checked_at"),
                       last_success_at=previous.get("last_success_at"))
            continue
        last_success = checked_at if row["state"] == "ok" else previous.get("last_success_at")
        row.update(last_checked_at=checked_at, last_success_at=last_success)
        ledger[source] = {"last_checked_at": checked_at, "last_success_at": last_success,
                          "state": row["state"]}
    return health
