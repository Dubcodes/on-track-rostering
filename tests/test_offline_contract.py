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
    assert 'localStorage.setItem("ontrack-last-saved-at", new Date().toISOString())' in source


def test_offline_html_is_built_only_from_the_small_roster_feed() -> None:
    source = Path("app/static/service-worker.js").read_text(encoding="utf-8")
    assert "offlineDayPage(workdayId)" in source
    assert 'cache.match("/api/upcoming-work")' in source
    assert "payload.cached_at = new Date().toISOString()" in source
    assert "day.assignments" in source
    assert "offlineRosterPage" in source
    assert "payload.product_name" in source
    assert "${productName} offline" in source
    assert "Reconnect to view or edit the authoritative roster." in source
    assert 'name="csrf_token"' not in source


def test_shell_cache_is_build_scoped_and_retires_only_old_shells() -> None:
    source = Path("app/static/service-worker.js").read_text(encoding="utf-8")
    assert 'new URL(self.location.href).searchParams.get("v")' in source
    assert "ontrack-shell-v3-${BUILD_VERSION}" in source
    assert '"/static/redeputy.css"' in source
    assert 'key.startsWith("ontrack-shell-") && key !== SHELL_CACHE' in source
    activate = source[source.index('self.addEventListener("activate"') : source.index('self.addEventListener("message"')]
    assert "ROSTER_PREFIX" not in activate
    assert "ontrack-shell-v2" not in source
    shell_fetch = source[source.index("if (SHELL_PATHS.includes") : source.index('self.addEventListener("push"')]
    assert 'url.searchParams.get("v") === BUILD_VERSION' in shell_fetch
    assert "caches.open(SHELL_CACHE)" in shell_fetch
    assert "caches.match(event.request)" not in shell_fetch
