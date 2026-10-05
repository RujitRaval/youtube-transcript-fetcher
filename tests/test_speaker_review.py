import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

from youtube_transcript_collector.speakers import backend, store
from youtube_transcript_collector.speakers.review import ReviewHandler


@pytest.fixture
def review(tmp_path):
    bundle = tmp_path / "bundle"
    directory = bundle / "speakers/runs" / ("a" * 32)
    directory.mkdir(parents=True)
    (bundle / "transcript.raw.json").write_text("[]")
    base = {
        "schema_version": 1,
        "run_id": "a" * 32,
        "video_id": "abcdefghijk",
        "source_sha256": backend.digest(bundle / "transcript.raw.json"),
        "title": "<script>alert(1)</script>",
        "partial": False,
        "captions": [],
    }
    state = {
        "run_id": base["run_id"],
        "revision": 0,
        "names": {"speaker_1": ""},
        "overrides": {},
        "history": [],
    }
    (directory / "run.json").write_text(json.dumps(base))
    (directory / "edits.json").write_text(json.dumps(state))
    (directory / "audio.wav").write_bytes(bytes(range(100)))
    (bundle / "speakers/current.json").write_text(json.dumps({"run_id": base["run_id"]}))
    return SimpleNamespace(
        bundle=bundle,
        origin="http://127.0.0.1:8766",
        token="test-token",
        config=SimpleNamespace(database=tmp_path / "db"),
    )


def request(server, path, *, method="GET", headers=None, body=None):
    handler = object.__new__(ReviewHandler)
    handler.server, handler.path, handler.command = server, path, method
    handler.requestline, handler.request_version = f"{method} {path} HTTP/1.1", "HTTP/1.1"
    handler.headers = Message()
    content = json.dumps(body).encode() if body is not None else b""
    default = {
        "Host": "127.0.0.1:8766",
        "Origin": server.origin,
        "X-Review-Token": server.token,
        "Content-Length": str(len(content)),
    }
    for key, value in (default | (headers or {})).items():
        handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(content), io.BytesIO()
    getattr(handler, "do_" + method)()
    head, result = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    return head.decode(), result


def test_review_routes_are_allowlisted_and_static_resources_packaged(review):
    for path in ["/", "/review.js", "/review.css", "/api/state"]:
        head, body = request(review, path)
        assert "200 OK" in head and body
        assert "no-store" in head
    for path in ["/../../config.yaml", "/run.json", "/edits.json", "/export/../../config.yaml"]:
        assert "404" in request(review, path)[0]
    state = json.loads(request(review, "/api/state")[1])
    assert state["token"] == review.token
    assert b"<script>alert" not in request(review, "/")[1]


@pytest.mark.parametrize(
    "headers",
    [{"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"X-Review-Token": "invalid"}],
)
def test_cross_origin_edits_rejected_without_modification(review, headers):
    head, _ = request(
        review,
        "/api/edits",
        method="POST",
        headers=headers,
        body={"run_id": "a" * 32, "revision": 0, "names": {"speaker_1": "Alice"}},
    )
    assert "403" in head
    assert store.load_run(review.bundle)[2]["revision"] == 0


def test_save_conflicts_and_exports_use_saved_revision(review):
    payload = {"run_id": "a" * 32, "revision": 0, "names": {"speaker_1": "Alice"}}
    head, body = request(review, "/api/edits", method="POST", body=payload)
    assert "200" in head and json.loads(body)["revision"] == 1
    assert "409" in request(review, "/api/edits", method="POST", body=payload)[0]
    path = "/export/transcript.speakers.json?" + "a" * 32
    exported = json.loads(request(review, path)[1])
    assert exported["edits"]["names"]["speaker_1"] == "Alice"
    assert len(exported["edits"]["history"]) == 1
    assert "409" in request(review, "/export/transcript.speakers.json?" + "b" * 32)[0]


@pytest.mark.parametrize(
    ("value", "status", "content"),
    [
        ("bytes=10-19", "206", bytes(range(10, 20))),
        ("bytes=95-", "206", bytes(range(95, 100))),
        ("bytes=-4", "206", bytes(range(96, 100))),
        ("bytes=110-", "416", b""),
        ("bytes=20-10", "416", b""),
        ("bytes=1-2,4-5", "416", b""),
    ],
)
def test_audio_ranges_support_seeking_and_reject_invalid_ranges(review, value, status, content):
    head, body = request(review, "/audio.wav?" + "a" * 32, headers={"Range": value})
    assert status in head and body == content


def test_audio_stale_run_and_get_host_rejected(review):
    assert "409" in request(review, "/audio.wav?" + "b" * 32)[0]
    assert "403" in request(review, "/api/state", headers={"Host": "evil.example"})[0]
    head, body = request(review, "/audio.wav?" + "a" * 32)
    assert "200" in head and len(body) == 100


def test_bad_or_large_post_does_not_modify_saved_names(review):
    for payload in [
        [],
        {"run_id": "a" * 32},
        {"run_id": "a" * 32, "revision": 0, "unexpected": True},
    ]:
        assert "409" in request(review, "/api/edits", method="POST", body=payload)[0]
    assert (
        "409"
        in request(review, "/api/edits", method="POST", headers={"Content-Length": "200001"})[0]
    )
    assert store.load_run(review.bundle)[2]["revision"] == 0
