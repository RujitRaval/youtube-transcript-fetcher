"""Optional dependencies are imported only for explicitly requested audio work."""

import hashlib
import logging
import math
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import requests

from ..output.storage import atomic_write
from ..utils import watch_url

log = logging.getLogger(__name__)
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
SEGMENTATION = "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
EMBEDDING = "nemo_en_titanet_small.onnx"
MODEL_HASHES = {
    SEGMENTATION: "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    EMBEDDING: "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e",
}
ARCHIVE_HASH = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
MODEL_URLS = {
    "segmentation.tar.bz2": RELEASE
    + "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    EMBEDDING: RELEASE + "speaker-recongition-models/" + EMBEDDING,
}


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_options(threshold, num_speakers, sample_seconds, threads):
    if not math.isfinite(threshold) or not 0 < threshold <= 2:
        raise ValueError("Speaker threshold must be greater than 0 and at most 2")
    if num_speakers is not None and not 1 <= num_speakers <= 50:
        raise ValueError("Speaker count must be between 1 and 50")
    if sample_seconds is not None and (not math.isfinite(sample_seconds) or sample_seconds <= 0):
        raise ValueError("Sample duration must be positive and finite")
    if not 1 <= threads <= 32:
        raise ValueError("Threads must be between 1 and 32")


def check_models(directory):
    for name, expected in MODEL_HASHES.items():
        path = directory / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError("Speaker models missing or changed; run 'speakers setup' first")


def setup_models(directory):
    """Fetch pinned public assets; only selected regular archive files are extracted."""
    directory.mkdir(parents=True, exist_ok=True)
    try:
        check_models(directory)
        return
    except RuntimeError:
        pass
    with tempfile.TemporaryDirectory(dir=directory, prefix=".download-") as scratch:
        scratch = Path(scratch)
        for name, url in MODEL_URLS.items():
            log.info("Downloading speaker model: %s", name)
            with requests.get(url, stream=True, timeout=(20, 60)) as response:
                response.raise_for_status()
                with (scratch / name).open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        handle.write(chunk)
            expected = ARCHIVE_HASH if name.endswith("bz2") else MODEL_HASHES[name]
            if digest(scratch / name) != expected:
                raise RuntimeError(
                    "Model download checksum mismatch; existing models were retained"
                )
        prefix = "sherpa-onnx-pyannote-segmentation-3-0/"
        with tarfile.open(scratch / "segmentation.tar.bz2") as archive:
            for name in (SEGMENTATION, prefix + "LICENSE", prefix + "README.md"):
                member = archive.getmember(name)
                if not member.isfile() or member.size > 20_000_000:
                    raise RuntimeError("Unexpected model archive entry")
                target = scratch / name
                target.parent.mkdir(exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
        check_models(scratch)
        for name in (*MODEL_HASHES, prefix + "LICENSE", prefix + "README.md"):
            target = directory / name
            target.parent.mkdir(exist_ok=True)
            (scratch / name).replace(target)
    atomic_write(directory / "SOURCES.txt", "\n".join(MODEL_URLS.values()) + "\n")


def dependencies():
    try:
        import imageio_ffmpeg
        import sherpa_onnx
        import soundfile
    except ImportError as exc:
        raise RuntimeError("Install speaker support with: pip install -e '.[speakers]'") from exc
    return sherpa_onnx, soundfile, imageio_ffmpeg.get_ffmpeg_exe()


def prepare_audio(directory, identifier, *, audio=None, sample_seconds=None, timeout=20):
    """Acquire audio only, convert to 16 kHz mono. No shell or automatic remote code."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    _, _, ffmpeg = dependencies()
    source = audio
    if source is None:
        from ..youtube.ytdlp import QuietLogger

        try:
            with YoutubeDL(
                dict(
                    format="bestaudio",
                    outtmpl=str(directory / "source.%(ext)s"),
                    quiet=True,
                    no_warnings=True,
                    logger=QuietLogger(),
                    noplaylist=True,
                    socket_timeout=timeout,
                    retries=0,
                    fragment_retries=0,
                    extractor_retries=0,
                    cachedir=False,
                    remote_components=set(),
                )
            ) as ydl:
                info = ydl.extract_info(watch_url(identifier), download=True)
                source = Path(ydl.prepare_filename(info))
        except DownloadError as exc:
            raise RuntimeError(
                "Audio download failed; retry later or pass --audio with a local file"
            ) from exc
    source = Path(source).expanduser().resolve()
    target = directory / "audio.wav"
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if sample_seconds is not None:
        command += ["-t", str(sample_seconds)]
    command += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Audio conversion timed out after 30 minutes") from exc
    if result.returncode:
        raise RuntimeError("Audio conversion failed; check that the input contains decodable audio")
    if audio is None:
        source.unlink()
    return target


def infer(audio, models, *, threshold=0.9, num_speakers=None, threads=2):
    sherpa, sf, _ = dependencies()
    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(models / SEGMENTATION),
                window_shift_ratio=0.1,
            ),
            num_threads=threads,
            provider="cpu",
        ),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(
            model=str(models / EMBEDDING),
            num_threads=threads,
            provider="cpu",
        ),
        clustering=sherpa.FastClusteringConfig(
            num_clusters=num_speakers or -1, threshold=threshold
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Invalid speaker model configuration")
    samples, rate = sf.read(audio, dtype="float32")
    engine = sherpa.OfflineSpeakerDiarization(config)
    if samples.ndim != 1 or rate != engine.sample_rate or not len(samples):
        raise ValueError("Speaker processing requires nonempty 16 kHz mono audio")
    started = time.monotonic()

    def progress(done, total):
        if done % 250 == 0 or done == total:
            log.info("Speaker analysis: %s/%s chunks", done, total)
        return 0

    result = engine.process(samples, callback=progress).sort_by_start_time()
    turns = [
        {"start": float(t.start), "end": float(t.end), "cluster": int(t.speaker)} for t in result
    ]
    return turns, {
        "backend": "sherpa-onnx",
        "backend_version": sherpa.__version__,
        "audio_seconds": len(samples) / rate,
        "inference_seconds": round(time.monotonic() - started, 2),
        "threshold": threshold,
        "num_speakers": num_speakers,
        "threads": threads,
        "model_sha256": MODEL_HASHES,
        "model_sources": MODEL_URLS,
    }
