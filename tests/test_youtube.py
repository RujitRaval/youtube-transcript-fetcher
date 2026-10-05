import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from youtube_transcript_api import TranscriptsDisabled

from youtube_transcript_collector.retry import RetrievalError
from youtube_transcript_collector.youtube import discovery, transcripts
from youtube_transcript_collector.youtube.transcripts import language_rank, parse_json3, parse_vtt


def track(language, generated):
    return SimpleNamespace(
        language_code=language,
        is_generated=generated,
        fetch=Mock(
            return_value=SimpleNamespace(
                to_raw_data=lambda: [{"start": 1.2, "duration": 3.4, "text": "test"}]
            )
        ),
    )


def test_api_selects_manual_english_over_generated_and_other_languages(config, monkeypatch):
    tracks = [track("fr", False), track("en", True), track("en-GB", False)]
    factory = Mock(return_value=SimpleNamespace(list=Mock(return_value=tracks)))
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", factory)
    result = transcripts.fetch_api("abcdefghijk", config)
    assert result.language == "en-GB" and result.source == "youtube-manual"
    tracks[2].fetch.assert_called_once_with(preserve_formatting=True)
    tracks[0].fetch.assert_not_called()


def test_language_ranking():
    assert language_rank("en-orig", True, ["fr"]) < language_rank("fr", False, ["fr"])
    assert language_rank("pt-BR", False, ["pt"]) is not None
    assert language_rank("de", False, ["fr"]) is None


def test_fallback_on_missing(config, transcript, monkeypatch):
    monkeypatch.setattr(
        transcripts, "fetch_api", Mock(side_effect=TranscriptsDisabled("abcdefghijk"))
    )
    fallback = Mock(return_value=transcript)
    monkeypatch.setattr(transcripts, "fetch_ytdlp", fallback)
    assert transcripts.retrieve("abcdefghijk", config) == transcript
    fallback.assert_called_once()


def test_rate_limit_never_uses_fallback(config, monkeypatch):
    monkeypatch.setattr(
        transcripts, "fetch_api", Mock(side_effect=RetrievalError("rate_limited", "429"))
    )
    fallback = Mock()
    monkeypatch.setattr(transcripts, "fetch_ytdlp", fallback)
    with pytest.raises(RetrievalError):
        transcripts.retrieve("abcdefghijk", config)
    fallback.assert_not_called()


def fake_ydl(monkeypatch, module, info, payload=None):
    instance = Mock()
    instance.extract_info.return_value = info
    if payload:
        instance.urlopen.return_value = io.BytesIO(payload.encode())
    context = Mock()
    context.__enter__ = Mock(return_value=instance)
    context.__exit__ = Mock(return_value=False)
    factory = Mock(return_value=context)
    monkeypatch.setattr(module, "YoutubeDL", factory)
    return factory, instance


def test_ytdlp_only_fetches_selected_native_caption(config, monkeypatch):
    payload = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 1038100,
                    "dDurationMs": 6200,
                    "segs": [{"utf8": "hello "}, {"utf8": "world"}],
                }
            ]
        }
    )
    info = {
        "subtitles": {"en": [{"ext": "vtt", "url": "https://youtube.com/captions?tlang=en"}]},
        "automatic_captions": {
            "en-orig": [{"ext": "json3", "url": "https://youtube.com/captions"}]
        },
    }
    factory, instance = fake_ydl(monkeypatch, transcripts, info, payload)
    result = transcripts.fetch_ytdlp("abcdefghijk", config)
    assert result.source == "youtube-auto" and result.language == "en-orig"
    assert result.original == payload
    assert result.segments == [{"start": 1038.1, "duration": 6.2, "text": "hello world"}]
    assert factory.call_args.args[0]["skip_download"] is True
    assert instance.extract_info.call_args.kwargs["download"] is False
    instance.urlopen.assert_called_once_with("https://youtube.com/captions")
    instance.download.assert_not_called()


def test_discovery_is_flat_and_bounded(config, monkeypatch):
    entries = [
        {"id": "abcdefghijk", "title": "test"},
        {"id": "12345678901", "title": "live", "live_status": "is_live"},
    ]
    factory, instance = fake_ydl(monkeypatch, discovery, {"entries": entries})
    result = discovery.discover(config.channels[0], config)
    opts = factory.call_args.args[0]
    assert opts["extract_flat"] and opts["playlistend"] == 10 and opts["skip_download"]
    assert len(result) == 1 and result[0].published_at is None
    instance.extract_info.assert_called_once_with(
        "https://www.youtube.com/@test/videos", download=False
    )


def test_vtt_parsing():
    payload = (
        "WEBVTT\r\n\r\nNOTE ignore\r\n\r\n1\r\n"
        "00:17:18.100 --> 00:17:24.300 align:start\r\n"
        "<c>Hello</c> &amp; <00:17:19.000>world\r\n\r\n"
    )
    result = parse_vtt(payload)
    assert result[0]["text"] == "Hello & world"
    assert result[0]["start"] == 1038.1
    assert result[0]["duration"] == pytest.approx(6.2)


def test_json3_empty_and_bad_timings():
    with pytest.raises(RetrievalError, match="no timed text"):
        parse_json3('{"events": []}')
    with pytest.raises(RetrievalError, match="Invalid caption"):
        parse_json3('{"events":[{"tStartMs":-1,"segs":[{"utf8":"hi"}]}]}')


def test_ytdlp_manual_vtt_preferred_over_auto_json(config, monkeypatch):
    payload = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n\n"
    info = {
        "subtitles": {"en-GB": [{"ext": "vtt", "url": "https://youtube.com/manual"}]},
        "automatic_captions": {"en": [{"ext": "json3", "url": "https://youtube.com/auto"}]},
    }
    _, instance = fake_ydl(monkeypatch, transcripts, info, payload)
    result = transcripts.fetch_ytdlp("abcdefghijk", config)
    assert result.source == "youtube-manual" and result.original == payload
    assert result.segments == [{"start": 1.0, "duration": 2.0, "text": "Hello"}]
    instance.urlopen.assert_called_once_with("https://youtube.com/manual")


def test_fallback_does_not_hide_network_error(config, monkeypatch):
    monkeypatch.setattr(transcripts, "fetch_api", Mock(side_effect=ConnectionError("offline")))
    monkeypatch.setattr(
        transcripts,
        "fetch_ytdlp",
        Mock(side_effect=RetrievalError("transcript_unavailable", "none")),
    )
    with pytest.raises(RetrievalError) as caught:
        transcripts.retrieve("abcdefghijk", config)
    assert caught.value.kind == "network"
