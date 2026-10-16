"""Hardware-free Chromium checks of the actual standalone game UI.

Run explicitly with the browser extra; normal unit runs omit this module.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

if os.environ.get("ISPY_BROWSER_TESTS") != "1":
    pytest.skip("Run with ISPY_BROWSER_TESTS=1 and the browser extra", allow_module_level=True)

from playwright.sync_api import expect, sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "reachy_mini_i_spy" / "static"


def game(state="idle", generation=0, **extra):
    return {
        "state": state,
        "generation": generation,
        "language": "en",
        "camera_active": state in {"searching", "selecting", "guessing"},
        "message": "Ready for a round.",
        **extra,
    }


@pytest.fixture
def ui():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        state = {"game": game(), "config": {}, "deployment_profile": "lite_host"}

        def route(request):
            path = request.request.url.split("ispy.test", 1)[1].split("?", 1)[0]
            if path == "/api/status":
                request.fulfill(json=state)
            elif path.startswith("/static/") or path == "/":
                name = "index.html" if path == "/" else path.removeprefix("/static/")
                file = STATIC / name
                request.fulfill(path=file) if file.is_file() else request.fulfill(status=404)
            else:
                request.fulfill(status=500, json={"detail": "Unexpected fixture request"})

        page.route("**/*", route)
        page.goto("http://ispy.test/")
        expect(page.locator("#state")).to_have_text("idle")
        yield page, state
        browser.close()


def refresh(page):
    page.evaluate("refresh()")


def test_state_adapter_is_categorical_and_never_leaks_target(ui):
    page, state = ui
    for value, phase in [
        ("searching", "searching"),
        ("selecting", "thinking"),
        ("guessing", "waiting"),
        ("reveal", "ended"),
        ("stopped", "ended"),
        ("error", "trouble"),
        ("alien", "paused"),
    ]:
        state["game"] = game(value, target={"object_name": "PRIVATE_TARGET"})
        refresh(page)
        expect(page.locator(".hb-scene")).to_have_attribute("data-phase", phase)
        assert "PRIVATE_TARGET" not in page.locator("#game-scene").inner_text()
    assert page.locator(".hb-art-ispy").count() == 1


def test_search_motion_deduplicates_and_pauses(ui):
    page, state = ui
    state["game"] = game("searching", 1)
    refresh(page)
    scene = page.locator(".hb-scene")
    expect(scene).to_have_attribute("data-motion", "on")
    page.wait_for_function("""document.querySelector('.hb-art').getAnimations({subtree:true})
      .some(a => a.playState === 'running')""")
    art = page.locator(".hb-art").element_handle()
    refresh(page)
    assert page.evaluate("art => art === document.querySelector('.hb-art')", art)
    page.emulate_media(reduced_motion="reduce")
    expect(scene).to_have_attribute("data-motion", "off")
    page.wait_for_function("""!document.querySelector('.hb-art').getAnimations({subtree:true})
      .some(a => a.playState === 'running')""")
    page.emulate_media(reduced_motion="no-preference")
    expect(scene).to_have_attribute("data-motion", "on")
    page.evaluate("""Object.defineProperty(document, 'hidden', {configurable:true, value:true});
      document.dispatchEvent(new Event('visibilitychange'))""")
    expect(scene).to_have_attribute("data-motion", "off")
    page.evaluate("""Object.defineProperty(document, 'hidden', {configurable:true, value:false});
      document.dispatchEvent(new Event('visibilitychange'))""")
    expect(scene).to_have_attribute("data-motion", "on")
    page.locator("#settings-form").scroll_into_view_if_needed()
    expect(scene).to_have_attribute("data-motion", "off")


def test_stop_rejects_held_active_status_and_start_reply(ui):
    page, state = ui
    held = []
    payload = {**state, "game": game("searching", 1)}

    def hold(route):
        held.append(route)
        page.evaluate("window.statusHeld = true")

    page.route("**/api/status", hold)
    page.evaluate("void refresh(); null")
    page.wait_for_function("window.statusHeld === true")
    assert held
    page.route("**/api/game/stop", lambda route: route.fulfill(json={"game": game("stopped", 2)}))
    page.locator("#stop").click()
    expect(page.locator("#state")).to_have_text("stopped")
    state["game"] = game("stopped", 2)
    page.unroute("**/api/status", hold)
    for route in list(held):
        with page.expect_response("**/api/status") as response:
            route.fulfill(json=payload)
        response.value.finished()
    page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "ended")
    expect(page.locator("#guess")).to_be_disabled()
    assert not page.locator("#camera-consent").is_checked()


def test_late_start_cannot_resurrect_after_stop(ui):
    page, _ = ui
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("window.startHeld = true")

    page.route("**/api/game/start", hold)
    page.route("**/api/game/stop", lambda route: route.fulfill(json={"game": game("stopped", 2)}))
    page.locator("#camera-consent").check()
    page.locator("#start").click()
    page.wait_for_function("window.startHeld === true")
    assert held
    page.locator("#stop").click()
    expect(page.locator("#state")).to_have_text("stopped")
    with page.expect_response("**/api/game/start") as response:
        held[0].fulfill(json={"game": game("searching", 1)})
    response.value.finished()
    page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "ended")
    expect(page.locator("#guess")).to_be_disabled()


def test_offline_is_unknown_not_camera_off_and_recovers(ui):
    page, state = ui
    state["game"] = game("searching", 1)
    refresh(page)
    page.route("**/api/status", lambda route: route.abort())
    refresh(page)
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "offline")
    expect(page.locator("#camera-badge")).to_have_text("Camera unconfirmed")
    expect(page.locator("#guess")).to_be_disabled()
    page.unroute("**/api/status")
    state["game"] = game("stopped", 2)
    refresh(page)
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "ended")


def test_failed_stop_does_not_claim_camera_off_or_resume_on_active_poll(ui):
    page, state = ui
    state["game"] = game("guessing", 1)
    refresh(page)
    page.route("**/api/game/stop", lambda route: route.fulfill(status=503, json={"detail": "Stop unconfirmed"}))
    page.locator("#stop").click()
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "offline")
    refresh(page)
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "offline")
    expect(page.locator("#start")).to_be_disabled()
    expect(page.locator("#guess")).to_be_disabled()
    expect(page.locator("#stop")).to_be_enabled()
    expect(page.locator("#camera-badge")).to_have_text("Camera unconfirmed")
    state["game"] = game("stopped", 2)
    refresh(page)
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "ended")


def test_dutch_scene_and_safe_text_rendering(ui):
    page, state = ui
    state["game"] = game("selecting", 1, language="nl", message="<img src=x onerror=alert(1)>")
    refresh(page)
    expect(page.locator(".hb-scene-title")).to_have_text("Even nadenken")
    expect(page.locator("#message")).to_have_text("<img src=x onerror=alert(1)>")
    assert page.locator("#message img").count() == 0


def test_speech_result_after_stop_is_discarded(ui):
    page, state = ui
    page.evaluate("""() => {
        window.SpeechRecognition = class {
          start() { window.testRecognition = this; }
          abort() { this.aborted = true; }
        };
    }""")
    state["game"] = game("guessing", 1)
    refresh(page)
    page.locator("#mic").click()
    page.route("**/api/game/stop", lambda route: route.fulfill(json={"game": game("stopped", 2)}))
    page.locator("#stop").click()
    expect(page.locator("#state")).to_have_text("stopped")
    page.evaluate("testRecognition.onresult?.({results:[[{transcript:'private old guess'}]]})")
    expect(page.locator("#guess")).to_have_value("")
    assert page.evaluate("testRecognition.aborted")


@pytest.mark.parametrize("width,height", [(320, 740), (390, 844), (1280, 900)])
def test_layout_controls_and_scene_screenshots(ui, width, height):
    page, state = ui
    page.set_viewport_size({"width": width, "height": height})
    state["game"] = game("guessing", 1)
    refresh(page)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for selector in ["#guess", "#send", "#stop"]:
        element = page.locator(selector)
        element.scroll_into_view_if_needed()
        box = element.bounding_box()
        assert box["height"] >= 48
        assert page.evaluate(
            """selector => {
          const node = document.querySelector(selector), r = node.getBoundingClientRect();
          return node.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
        }""",
            selector,
        )
    page.evaluate("scrollTo(0, 0)")
    target = Path(".artifacts")
    target.mkdir(exist_ok=True)
    page.screenshot(path=str(target / f"ispy-{width}.png"), full_page=True)


def test_scene_destroy_remount_and_page_lifecycle(ui):
    page, _ = ui
    assert page.evaluate("HomebodyScene.liveCount()") == 1
    page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    assert page.evaluate("HomebodyScene.liveCount()") == 0
    page.evaluate("window.dispatchEvent(new Event('pageshow'))")
    expect(page.locator(".hb-scene")).to_have_count(1)
    assert page.evaluate("HomebodyScene.liveCount()") == 1
    page.evaluate("""() => {
      const host = document.createElement('div'); document.body.append(host);
      for (let i=0; i<8; i++) {
        const s=HomebodyScene.mount(host); s.update({scene:'ispy',phase:'searching'}); s.destroy();
      }
      host.remove();
    }""")
    assert page.evaluate("HomebodyScene.liveCount()") == 1


def test_local_setup_can_exceed_thirty_seconds_without_duplicate_install(ui):
    page, state = ui
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("window.setupHeld = true")

    page.clock.install()
    page.route("**/api/local/setup", hold)
    page.locator("#local-setup").click()
    page.wait_for_function("window.setupHeld === true")
    expect(page.locator("#local-setup")).to_be_disabled()
    page.evaluate("document.querySelector('#local-setup').dispatchEvent(new Event('click'))")
    page.clock.fast_forward(31000)
    expect(page.locator("#local-state")).to_have_text("Downloading and verifying local models…")
    assert len(held) == 1
    state["config"] = {"provider": "local", "local_assets": {"ready": True}}
    held[0].fulfill(json={"assets": {"ready": True}})
    expect(page.locator("#local-setup")).to_be_enabled()
    expect(page.locator("#local-state")).to_have_text("Local models installed and verified")


def test_confirmed_refusal_is_not_connection_loss(ui):
    page, _ = ui
    page.route("**/api/game/start", lambda r: r.fulfill(status=403, json={"detail": "Camera opt-in required"}))
    page.locator("#start").click()
    expect(page.locator("#toast")).to_have_text("Camera opt-in required")
    expect(page.locator("#state")).to_have_text("idle")
    expect(page.locator(".hb-scene")).to_have_attribute("data-phase", "preview")


def test_html_error_has_safe_feedback(ui):
    page, _ = ui
    page.route(
        "**/api/game/start", lambda r: r.fulfill(status=502, content_type="text/html", body="<h1>Proxy failure</h1>")
    )
    page.locator("#start").click()
    expect(page.locator("#toast")).to_have_text("The app could not complete the request.")
    expect(page.locator("#camera-badge")).to_have_text("Camera unconfirmed")


@pytest.mark.parametrize("value", ["searching", "selecting", "reveal", "error"])
def test_scene_phase_screenshots(ui, value):
    page, state = ui
    state["game"] = game(value, 1)
    refresh(page)
    page.emulate_media(reduced_motion="reduce")
    target = Path(".artifacts")
    target.mkdir(exist_ok=True)
    page.locator("#game-scene").screenshot(path=str(target / f"scene-{value}.png"))
