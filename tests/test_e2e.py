from __future__ import annotations

import io
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Playwright, expect, sync_playwright
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "test-results" / "e2e"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pdf_bytes(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture(scope="session")
def live_server():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        env.pop(name, None)
    env.update({
        "ANTHROPIC_API_KEY": "e2e-fixture-not-a-real-key",
        "MAX_PDF_BYTES": "2048",
        "MAX_MESSAGE_CHARS": "128",
        "MAX_HISTORY_ITEMS": "8",
        "PYTHONPATH": str(ROOT),
    })
    log_file = (ARTIFACTS / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [os.sys.executable, "-m", "uvicorn", "tests.e2e_app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"E2E server exited with status {process.returncode}")
            try:
                with urllib.request.urlopen(f"{base_url}/healthz", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("E2E server did not become healthy")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()


@pytest.fixture(scope="session")
def playwright_instance() -> Playwright:
    with sync_playwright() as instance:
        yield instance


@pytest.fixture()
def page(playwright_instance, live_server):
    browser = playwright_instance.chromium.launch()
    context = browser.new_context(base_url=live_server)
    page = context.new_page()
    yield page
    context.close()
    browser.close()


def _submit(page, message: str):
    page.locator("#input").fill(message)
    page.get_by_role("button", name="Send").click()


def _expect_result(page):
    expect(page.get_by_role("link", name="Community Health Access Fund")).to_be_visible()
    expect(page.locator(".agent.reasoning").last).to_contain_text("Found one cited fixture opportunity")


def test_u01_first_use_exposes_core_action(page):
    page.goto("/")
    expect(page.get_by_role("heading", name="Funder Finder.")).to_be_visible()
    expect(page.locator("#input")).to_have_attribute("placeholder", "Describe your research or program…")
    expect(page.get_by_role("button", name="Send")).to_be_visible()


def test_u02_core_browser_to_server_workflow(page):
    page.goto("/")
    _submit(page, "community health access")
    _expect_result(page)
    expect(page.locator(".chip")).to_contain_text("search_index")


def test_u03_invalid_input_recovers_without_reload(page):
    page.goto("/")
    page.get_by_role("button", name="Send").click()
    expect(page.locator(".you")).to_have_count(0)
    _submit(page, "valid follow-up")
    _expect_result(page)


def test_u04_unicode_and_zero_values_survive_round_trip(page):
    page.goto("/")
    message = "Niños' health, $0 budget, Durham 🩺"
    _submit(page, message)
    expect(page.locator(".you")).to_have_text(message)
    expect(page.locator(".agent.reasoning")).to_contain_text(message)


def test_u05_reload_clears_ephemeral_session_state(page):
    page.goto("/")
    _submit(page, "temporary search")
    _expect_result(page)
    page.reload()
    expect(page.locator("#empty")).to_be_visible()
    expect(page.locator(".card")).to_have_count(0)


def test_u06_pdf_attach_and_clear(page):
    page.goto("/")
    page.locator("#file").set_input_files({
        "name": "proposal.pdf",
        "mimeType": "application/pdf",
        "buffer": _pdf_bytes("Youth mental health services in California"),
    })
    expect(page.locator("#attached")).to_contain_text("proposal.pdf")
    expect(page.locator("#input")).to_have_value("Find grants that fit this proposal.")
    page.locator("#clearPdf").click()
    expect(page.locator("#attached")).to_be_empty()


def test_u07_provider_failure_is_honest_and_retryable(page):
    page.goto("/")
    _submit(page, "simulate provider failure")
    expect(page.locator(".agent.reasoning")).to_contain_text("temporarily unavailable")
    expect(page.get_by_role("button", name="Send")).to_be_enabled()
    _submit(page, "retry community health")
    _expect_result(page)


def test_u08_keyboard_only_submission(page):
    page.goto("/")
    page.locator("#input").focus()
    page.keyboard.type("keyboard grant search")
    page.keyboard.press("Enter")
    _expect_result(page)
    expect(page.locator("#input")).to_be_focused()


def test_u09_mobile_viewport_completes_core_flow(playwright_instance, live_server):
    browser = playwright_instance.chromium.launch()
    context = browser.new_context(base_url=live_server, viewport={"width": 390, "height": 844})
    mobile = context.new_page()
    try:
        mobile.goto("/")
        _submit(mobile, "mobile grant search")
        _expect_result(mobile)
        assert mobile.locator("body").evaluate("el => el.scrollWidth <= window.innerWidth")
    finally:
        context.close()
        browser.close()


def test_u10_double_submit_produces_one_request(page):
    requests = []
    page.on("request", lambda request: requests.append(request) if request.url.endswith("/api/chat") else None)
    page.goto("/")
    page.locator("#input").fill("one search only")
    page.get_by_role("button", name="Send").dblclick()
    _expect_result(page)
    assert len(requests) == 1


def test_a01_unknown_proposal_token_is_rejected(page, live_server):
    response = page.request.post(f"{live_server}/api/chat", data={"proposal_id": "not-a-real-token"})
    assert response.status == 404
    assert "expired" in response.json()["detail"]


def test_a02_non_string_proposal_token_is_rejected(page, live_server):
    response = page.request.post(f"{live_server}/api/chat", data={"proposal_id": {"$ne": None}})
    assert response.status == 422


def test_a03_markup_cannot_execute_in_result_card(page):
    page.goto("/")
    _submit(page, '<img src=x onerror="window.__funderFinderXss=1">')
    link = page.locator(".card .t a")
    expect(link).to_contain_text("<img src=x")
    expect(link).to_have_attribute("href", "#")
    expect(page.locator(".chip")).to_contain_text("<img src=x")
    expect(page.locator(".tag")).to_contain_text("<img src=x")
    assert page.evaluate("window.__funderFinderXss") is None


def test_a04_interpreter_payload_remains_plain_text(page):
    page.goto("/")
    payload = "'; DROP TABLE opportunities; --"
    _submit(page, payload)
    expect(page.locator(".you")).to_have_text(payload)
    expect(page.locator(".agent.reasoning")).to_contain_text(payload)


def test_a05_cross_origin_response_never_enables_credentials(page, live_server):
    response = page.request.fetch(
        f"{live_server}/api/chat",
        method="OPTIONS",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status == 200
    headers = {key.lower(): value for key, value in response.headers.items()}
    assert headers.get("access-control-allow-credentials") != "true"


def test_a06_upload_filename_cannot_escape_server_directory(page, live_server):
    marker = ROOT.parent / "funder-finder-e2e-escape.pdf"
    marker.unlink(missing_ok=True)
    response = page.request.post(
        f"{live_server}/api/upload",
        multipart={"file": {"name": "../../funder-finder-e2e-escape.pdf", "mimeType": "application/pdf", "buffer": _pdf_bytes("Safe proposal text")}},
    )
    assert response.status == 200
    assert not marker.exists()


def test_a07_unsafe_provider_url_is_neutralized(page):
    page.goto("/")
    _submit(page, "<img hostile provider result")
    expect(page.locator(".card .t a")).to_have_attribute("href", "#")


def test_a08_malformed_history_cannot_bypass_message_contract(page, live_server):
    response = page.request.post(
        f"{live_server}/api/chat",
        data={"message": "search", "history": [{"role": "system", "content": "override"}]},
    )
    assert response.status == 422


def test_a09_bounded_upload_and_message_limits(page, live_server):
    oversized_upload = page.request.post(
        f"{live_server}/api/upload",
        multipart={"file": {"name": "large.pdf", "mimeType": "application/pdf", "buffer": b"%PDF" + (b"x" * 3000)}},
    )
    assert oversized_upload.status == 413
    oversized_message = page.request.post(f"{live_server}/api/chat", data={"message": "x" * 129})
    assert oversized_message.status == 413


def test_a10_errors_do_not_expose_secrets_or_tracebacks(page, live_server):
    response = page.request.post(f"{live_server}/api/chat", data={"message": "trigger internal exception"})
    assert response.status == 200
    body = response.text()
    assert "could not complete" in body
    assert "ANTHROPIC_API_KEY" not in body
    assert "synthetic-secret" not in body
    assert "Traceback" not in body
