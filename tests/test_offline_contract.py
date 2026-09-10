from pathlib import Path


def test_user_switch_deletes_other_roster_caches_before_marker_update() -> None:
    source = Path("app/static/service-worker.js").read_text(encoding="utf-8")
    function = source[source.index("async function setActiveUser") : source.index("async function activeRosterCache")]
    assert "key !== ownRosterCache" in function
    assert function.index("caches.delete(key)") < function.index("cache.put(ACTIVE_USER_KEY")


def test_prefetch_uses_server_selected_days_and_is_sequential() -> None:
    source = Path("app/static/app.js").read_text(encoding="utf-8")
    assert 'fetch("/api/upcoming-work"' in source
    assert "(upcoming.days || []).map((item) => item.id)" in source
    assert ".slice(0, 4)" not in source
    assert "for (const id of ids)" in source
    assert "payload.saved_at" in source


def test_offline_html_is_built_only_from_the_small_roster_feed() -> None:
    source = Path("app/static/service-worker.js").read_text(encoding="utf-8")
    assert "^\\/day\\/[a-f0-9-]+$" in source
    assert 'cache.match("/api/upcoming-work")' in source
    assert "offlineRosterPage" in source
    assert "Reconnect to view or edit the authoritative roster." in source
    assert 'name="csrf_token"' not in source
