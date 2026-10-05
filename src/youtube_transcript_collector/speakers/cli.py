import logging
from pathlib import Path

from ..database import writer_lock
from ..utils import video_id
from . import backend, store


def register(commands):
    diarize = commands.add_parser(
        "diarize", help="Explicitly analyze audio and label collected captions"
    )
    diarize.add_argument("video_url")
    diarize.add_argument(
        "--audio",
        type=Path,
        help="Local audio starting at video time zero; otherwise download audio only",
    )
    diarize.add_argument("--models-dir", type=Path)
    diarize.add_argument(
        "--sample-seconds",
        type=float,
        help="Analyze only the beginning; source download may be full length",
    )
    diarize.add_argument(
        "--num-speakers", type=int, help="Known voice count; otherwise detect automatically"
    )
    diarize.add_argument(
        "--threshold",
        type=float,
        default=0.9,
        help="Clustering threshold (default .9); higher merges more voices",
    )
    diarize.add_argument("--threads", type=int, default=2)
    speakers = commands.add_parser(
        "speakers", help="Set up models, review, rename, or export speakers"
    )
    sub = speakers.add_subparsers(dest="speaker_command", required=True)
    setup = sub.add_parser("setup", help="Download checksum-verified public speaker models")
    setup.add_argument("--models-dir", type=Path)
    review = sub.add_parser("review", help="Open a localhost review page with durable saved edits")
    review.add_argument("video_url")
    review.add_argument("--port", type=int, default=8766)
    review.add_argument("--open", action="store_true", help="Also open the default browser")
    rename = sub.add_parser("rename", help="Rename a numbered voice across the current run")
    rename.add_argument("video_url")
    rename.add_argument("speaker", type=int, help="Speaker number, e.g. 1")
    rename.add_argument("name", help="Display name; an empty string clears it")
    export = sub.add_parser("export", help="Write Markdown, VTT, and audit JSON using saved edits")
    export.add_argument("video_url")


def execute(args, config):
    if args.command == "diarize" or args.speaker_command == "setup":
        models = (
            (args.models_dir or config.database.parent / "speaker-models").expanduser().resolve()
        )
        if args.command == "speakers":
            with writer_lock(config.database):
                backend.setup_models(models)
            print(f"Speaker models ready: {models}")
            return 0
        identifier = video_id(args.video_url)
        directory, base, state = store.create_run(
            config,
            identifier,
            models=models,
            audio=args.audio,
            sample_seconds=args.sample_seconds,
            threshold=args.threshold,
            num_speakers=args.num_speakers,
            threads=args.threads,
        )
        print(f"Saved {len(state['names'])} voice clusters: {directory}")
        print(
            f"Model processing: {base['analysis']['inference_seconds']} seconds; "
            f"partial audio: {base['partial']}"
        )
        print(
            f"Review with: youtube-transcript-collector --config {args.config} "
            f"speakers review {identifier}"
        )
        logging.info("Speaker run %s saved for %s", base["run_id"], identifier)
        return 0
    bundle = store.bundle_for(config, video_id(args.video_url))
    if args.speaker_command == "review":
        from .review import serve

        return serve(config, bundle, args.port, args.open)
    with writer_lock(config.database):
        if args.speaker_command == "rename":
            _, base, state = store.load_run(bundle)
            state = store.apply_edit(
                bundle,
                run_id=base["run_id"],
                revision=state["revision"],
                names={f"speaker_{args.speaker}": args.name},
            )
            print(f"Saved speaker name at revision {state['revision']}")
        print(f"Speaker exports: {store.export_run(bundle)}")
    return 0
