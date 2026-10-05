#!/usr/bin/env python3
"""Render native scheduler files with this virtualenv's paths. Does not install jobs."""

import argparse
import plistlib
import shutil
import sys
from pathlib import Path


def systemd_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render(project, config, output, platform, python):
    project, config, output = project.resolve(), config.resolve(), output.resolve()
    if not config.is_file():
        raise ValueError(f"Create and edit the config first: {config}")
    output.mkdir(parents=True, exist_ok=True)
    logs = config.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    values = {
        "@@PROJECT@@": str(project),
        "@@CONFIG@@": str(config),
        "@@LOG@@": str(logs / "collector.log"),
        "@@PYTHON@@": str(python),
    }
    if platform == "launchd":
        data = plistlib.loads(
            (project / "deploy/launchd/com.youtube-transcript-collector.plist").read_bytes()
        )
        data["WorkingDirectory"] = values["@@PROJECT@@"]
        data["ProgramArguments"] = [values.get(value, value) for value in data["ProgramArguments"]]
        path = output / "com.youtube-transcript-collector.plist"
        path.write_bytes(plistlib.dumps(data))
    else:
        template = (project / "deploy/systemd/youtube-transcript-collector.service").read_text()
        for key, value in values.items():
            if any(char in value for char in ("\n", "\r", "$")):
                raise ValueError("systemd installation paths may not contain newlines or '$'")
            template = template.replace(key, systemd_quote(value))
        path = output / "youtube-transcript-collector.service"
        path.write_text(template)
        shutil.copyfile(
            project / "deploy/systemd/youtube-transcript-collector.timer",
            output / "youtube-transcript-collector.timer",
        )
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=["launchd", "systemd"], required=True)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--output", type=Path, default=Path("build/scheduler"))
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    # Do NOT resolve the executable symlink: that would escape the virtualenv.
    print(render(project, args.config, args.output, args.platform, Path(sys.executable).absolute()))


if __name__ == "__main__":
    main()
