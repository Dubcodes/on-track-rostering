from pathlib import Path


def test_user_switch_deletes_other_roster_caches_before_marker_update() -> None:
    source = Path("app/static/service-worker.js").read_text(encoding="utf-8")
    function = source[source.index("async function setActiveUser") : source.index("async function activeRosterCache")]
    assert "key !== ownRosterCache" in function
    assert function.index("caches.delete(key)") < function.index("cache.put(ACTIVE_USER_KEY")


def test_prefetch_is_date_sorted_limited_and_sequential() -> None:
    source = Path("app/static/app.js").read_text(encoding="utf-8")
    assert "a[data-work-date]" in source
    assert ".sort((left, right) => left.date.localeCompare(right.date))" in source
    assert ".slice(0, 3)" in source
    assert "for (const id of ids)" in source
    assert "payload.saved_at" in source
