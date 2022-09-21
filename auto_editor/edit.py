from __future__ import annotations

import os

from auto_editor.ffwrapper import FFmpeg, FileInfo
from auto_editor.objects import (
    Attr,
    EditAudio,
    EditClipSequence,
    EditDefault,
    EditFinalCutPro,
    EditJson,
    EditPremiere,
    EditShotCut,
    EditTimeline,
    Exports,
)
from auto_editor.output import Ensure
from auto_editor.timeline import Timeline, make_timeline
from auto_editor.utils.bar import Bar
from auto_editor.utils.chunks import Chunk, Chunks
from auto_editor.utils.container import Container, container_constructor
from auto_editor.utils.log import Log, Timer
from auto_editor.utils.types import Args


def set_output_name(path: str, original_ext: str, export: Exports) -> str:
    root, path_ext = os.path.splitext(path)

    if isinstance(export, EditPremiere):
        return f"{root}.xml"
    if isinstance(export, EditFinalCutPro):
        return f"{root}.fcpxml"
    if isinstance(export, EditShotCut):
        return f"{root}.mlt"
    if isinstance(export, EditJson):
        return f"{root}.json"
    if isinstance(export, EditAudio):
        return f"{root}_ALTERED.wav"
    if path_ext == "":
        return root + original_ext

    return f"{root}_ALTERED{path_ext}"


codec_error = "'{}' codec is not supported in '{}' container."


def set_video_codec(
    codec: str, src: FileInfo | None, out_ext: str, ctr: Container, log: Log
) -> str:
    if codec == "auto":
        codec = "h264" if (src is None or not src.videos) else src.videos[0].codec
        if ctr.vcodecs is not None:
            if ctr.vstrict and codec not in ctr.vcodecs:
                return ctr.vcodecs[0]

            if codec in ctr.disallow_v:
                return ctr.vcodecs[0]
        return codec

    if codec == "copy":
        if src is None:
            log.error("No input to copy its codec from.")
        if not src.videos:
            log.error("Input file does not have a video stream to copy codec from.")
        codec = src.videos[0].codec

    if ctr.vstrict:
        assert ctr.vcodecs is not None
        if codec not in ctr.vcodecs:
            log.error(codec_error.format(codec, out_ext))

    if codec in ctr.disallow_v:
        log.error(codec_error.format(codec, out_ext))

    return codec


def set_audio_codec(
    codec: str, src: FileInfo | None, out_ext: str, ctr: Container, log: Log
) -> str:
    if codec == "auto":
        codec = "aac" if (src is None or not src.audios) else src.audios[0].codec
        if ctr.acodecs is not None:
            if ctr.astrict and codec not in ctr.acodecs:
                return ctr.acodecs[0]

            if codec in ctr.disallow_a:
                return ctr.acodecs[0]
        return codec

    if codec == "copy":
        if src is None:
            log.error("No input to copy its codec from.")
        if not src.audios:
            log.error("Input file does not have an audio stream to copy codec from.")
        codec = src.audios[0].codec

    if codec != "unset":
        if ctr.astrict:
            assert ctr.acodecs is not None
            if codec not in ctr.acodecs:
                log.error(codec_error.format(codec, out_ext))

        if codec in ctr.disallow_a:
            log.error(codec_error.format(codec, out_ext))

    return codec


def make_sources(
    paths: list[str], ffmpeg: FFmpeg, log: Log
) -> tuple[dict[str | int, FileInfo], list[int]]:

    used_paths: dict[str, int] = {}
    sources: dict[int | str, FileInfo] = {}
    inputs: list[int] = []

    i = 0
    for path in paths:
        if path in used_paths:
            inputs.append(used_paths[path])
        else:
            sources[i] = FileInfo(path, i, ffmpeg, log)
            inputs.append(i)
            used_paths[path] = i
            i += 1
    return sources, inputs


def parse_export(export: str, log: Log) -> Exports:
    from auto_editor.objects import parse_dataclass, timeline_builder

    exploded = export.split(":", maxsplit=1)
    if len(exploded) == 1:
        name, attrs = exploded[0], ""
    else:
        name, attrs = exploded

    parsing: dict[str, tuple[type[Exports], list[Attr]]] = {
        "default": (EditDefault, []),
        "premiere": (EditPremiere, []),
        "final-cut-pro": (EditFinalCutPro, []),
        "shotcut": (EditShotCut, []),
        "json": (EditJson, timeline_builder),
        "timeline": (EditTimeline, timeline_builder),
        "audio": (EditAudio, []),
        "clip-sequence": (EditClipSequence, []),
    }

    if name in parsing:
        return parse_dataclass(attrs, parsing[name], log)

    log.error(f"'{name}': Export must be [{', '.join([s for s in parsing.keys()])}]")


def edit_media(
    paths: list[str], ffmpeg: FFmpeg, args: Args, temp: str, log: Log
) -> None:

    timer = Timer(args.quiet)
    bar = Bar(args.progress)
    timeline = None

    if paths:
        path_ext = os.path.splitext(paths[0])[1]
        if path_ext == ".xml":
            from auto_editor.formats.premiere import premiere_read_xml

            timeline = premiere_read_xml(paths[0], ffmpeg, log)
            src: FileInfo | None = next(iter(timeline.sources.items()))[1]
            sources = timeline.sources

        elif path_ext == ".json":
            from auto_editor.formats.json import read_json

            timeline = read_json(paths[0], ffmpeg, log)
            inputs = [0]
            sources = timeline.sources
            src = None if not inputs else sources[inputs[0]]
        else:
            sources, inputs = make_sources(paths, ffmpeg, log)
            src = None if not inputs else sources[inputs[0]]

    del paths

    if args.output_file is None or os.path.splitext(args.output_file)[1] != ".json":
        export = parse_export(args.export, log)
    else:
        export = EditJson(api="1")

    if src is not None:
        if args.output_file is None:
            output = set_output_name(src.path, src.ext, export)
        else:
            output = args.output_file
            if os.path.splitext(output)[1] == "":
                output = set_output_name(output, src.ext, export)
    else:
        output = "out.mp4" if args.output_file is None else args.output_file

    if not args.preview and output is not None:
        log.conwrite("Starting")

        if os.path.isdir(output):
            log.error("Output path already has an existing directory!")

        if os.path.isfile(output) and src is not None and src.path != output:
            log.debug(f"Removing already existing file: {output}")
            os.remove(output)

    if args.sample_rate is None:
        if timeline is None:
            samplerate = 48000 if src is None else src.get_samplerate()
        else:
            samplerate = timeline.samplerate
    else:
        samplerate = args.sample_rate

    ensure = Ensure(ffmpeg, samplerate, temp, log)

    if timeline is None:
        # Extract subtitles in their native format.
        if src is not None and len(src.subtitles) > 0:
            cmd = ["-i", src.path, "-hide_banner"]
            for s, sub in enumerate(src.subtitles):
                cmd.extend(["-map", f"0:s:{s}"])
            for s, sub in enumerate(src.subtitles):
                cmd.extend([os.path.join(temp, f"{s}s.{sub.ext}")])
            ffmpeg.run(cmd)

        timeline = make_timeline(
            sources, inputs, ffmpeg, ensure, args, samplerate, bar, temp, log
        )

    if isinstance(export, EditTimeline):
        from auto_editor.formats.json import make_json_timeline

        make_json_timeline(export, 0, timeline, log)
        return

    if args.preview:
        from auto_editor.preview import preview

        preview(ensure, timeline, temp, log)
        return

    if isinstance(export, EditJson):
        from auto_editor.formats.json import make_json_timeline

        make_json_timeline(export, output, timeline, log)
        return

    if isinstance(export, EditPremiere):
        from auto_editor.formats.premiere import premiere_write_xml

        premiere_write_xml(ensure, output, timeline)
        return

    if isinstance(export, EditFinalCutPro):
        from auto_editor.formats.final_cut_pro import fcp_xml

        fcp_xml(output, timeline)
        return

    if isinstance(export, EditShotCut):
        from auto_editor.formats.shotcut import shotcut_xml

        shotcut_xml(output, timeline)
        return

    out_ext = os.path.splitext(output)[1].replace(".", "")

    # Check if export options make sense.
    ctr = container_constructor(out_ext)

    if ctr.samplerate is not None and args.sample_rate not in ctr.samplerate:
        log.error(f"'{out_ext}' container only supports samplerates: {ctr.samplerate}")

    args.video_codec = set_video_codec(args.video_codec, src, out_ext, ctr, log)
    args.audio_codec = set_audio_codec(args.audio_codec, src, out_ext, ctr, log)

    if args.keep_tracks_separate and ctr.max_audios == 1:
        log.warning(f"'{out_ext}' container doesn't support multiple audio tracks.")

    def make_media(timeline: Timeline, output: str) -> None:
        from auto_editor.output import mux_quality_media
        from auto_editor.render.video import render_av

        assert src is not None

        visual_output = []
        audio_output = []
        sub_output = []
        apply_later = False

        if ctr.allow_subtitle:
            from auto_editor.render.subtitle import make_new_subtitles

            sub_output = make_new_subtitles(timeline, ffmpeg, temp, log)

        if ctr.allow_audio:
            from auto_editor.render.audio import make_new_audio

            audio_output = make_new_audio(timeline, ensure, ffmpeg, bar, temp, log)

        if ctr.allow_video:
            if len(timeline.v) > 0:
                out_path, apply_later = render_av(
                    ffmpeg, timeline, args, bar, ctr, temp, log
                )
                visual_output.append((True, out_path))

            for v, vid in enumerate(src.videos, start=1):
                if ctr.allow_image and vid.codec in ("png", "mjpeg", "webp"):
                    out_path = os.path.join(temp, f"{v}.{vid.codec}")
                    # fmt: off
                    ffmpeg.run(["-i", src.path, "-map", "0:v", "-map", "-0:V",
                        "-c", "copy", out_path])
                    # fmt: on
                    visual_output.append((False, out_path))

        log.conwrite("Writing output file")
        mux_quality_media(
            ffmpeg,
            visual_output,
            audio_output,
            sub_output,
            apply_later,
            ctr,
            output,
            timeline.timebase,
            args,
            src,
            temp,
            log,
        )

    if isinstance(export, EditClipSequence):
        chunks = timeline.chunks
        if chunks is None:
            log.error("Timeline to complex to use clip-sequence export")

        from auto_editor.make_layers import clipify, make_av
        from auto_editor.utils.func import append_filename

        def pad_chunk(chunk: Chunk, total: int) -> Chunks:
            start = [] if chunk[0] == 0 else [(0, chunk[0], 99999.0)]
            end = [] if chunk[1] == total else [(chunk[1], total, 99999.0)]
            return start + [chunk] + end

        total_frames = chunks[-1][1] - 1
        clip_num = 0
        for chunk in chunks:
            if chunk[2] == 99999:
                continue

            _c = pad_chunk(chunk, total_frames)

            vspace, aspace = make_av([clipify(_c, 0)], timeline.sources, [0])
            my_timeline = Timeline(
                timeline.sources,
                timeline.timebase,
                timeline.samplerate,
                timeline.res,
                "#000",
                vspace,
                aspace,
                _c,
            )

            make_media(my_timeline, append_filename(output, f"-{clip_num}"))
            clip_num += 1
    else:
        make_media(timeline, output)

    if output is not None:
        timer.stop()

    if not args.no_open and output is not None:
        if args.player is None:
            from auto_editor.utils.func import open_with_system_default

            open_with_system_default(output, log)
        else:
            import subprocess
            from shlex import split

            subprocess.run(split(args.player) + [output])
