"""ClipForge command line.

Examples:
  python cli.py vod.mp4 -c 00:12:30-00:13:00 --preset viral --hook "He actually did it"
  python cli.py vod.mp4 --auto 5 --length 30 --preset stream-stack --credit "Clip from @streamer"
  python cli.py vod.mp4 -c 10:00-10:45 --no-captions --fit crop --res 720
"""
import argparse
import sys

import clipper as C


def parse_range(text: str):
    if "-" not in text:
        raise argparse.ArgumentTypeError("use START-END, e.g. 00:12:30-00:13:00")
    a, b = text.split("-", 1)
    return a.strip(), b.strip()


def parse_facecam(text: str):
    try:
        x, y, w, h = (float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("use x,y,w,h as fractions of the frame, e.g. 0.70,0.60,0.30,0.40")
    return x, y, w, h


def main() -> int:
    p = argparse.ArgumentParser(description="Cut long videos into captioned, viral-style short clips.")
    p.add_argument("video")
    p.add_argument("-c", "--clip", action="append", type=parse_range, default=[], metavar="START-END",
                   help="clip range (repeatable). Times: seconds, MM:SS or HH:MM:SS")
    p.add_argument("--auto", type=int, default=0, metavar="N", help="auto-suggest N loudest moments")
    p.add_argument("--length", type=float, default=30, help="clip length for --auto (seconds, default 30)")
    p.add_argument("--preset", choices=list(C.PRESETS), help="starting point; other flags override it")
    p.add_argument("-o", "--out", default="clips", help="output folder (default: clips)")

    g = p.add_argument_group("output")
    g.add_argument("--aspect", choices=list(C.ASPECTS))
    g.add_argument("--res", type=int, choices=[480, 720, 1080, 1440], help="short-side pixels")
    g.add_argument("--fit", choices=C.FIT_MODES, help="blur / crop / fit (black bars) / stack (gameplay + facecam)")
    g.add_argument("--quality", choices=list(C.QUALITY))
    g.add_argument("--fps", type=int, help="e.g. 30 or 60 (default: keep source)")
    g.add_argument("--facecam", type=parse_facecam, metavar="x,y,w,h", help="facecam box for --fit stack")
    g.add_argument("--stack-ratio", type=float, help="gameplay share of the height for stack (default 0.6)")

    g = p.add_argument_group("captions")
    g.add_argument("--no-captions", action="store_const", const=True)
    g.add_argument("--model", choices=C.WHISPER_MODELS, help="bigger = more accurate, slower")
    g.add_argument("--language", help="e.g. en, hi (default: auto-detect)")
    g.add_argument("--words", type=int, help="words per caption line")
    g.add_argument("--position", choices=C.CAPTION_POSITIONS)
    g.add_argument("--caption-style", choices=list(C.CAPTION_STYLES))
    g.add_argument("--no-highlight", action="store_const", const=True)
    g.add_argument("--no-uppercase", action="store_const", const=True)
    g.add_argument("--no-pop", action="store_const", const=True)
    g.add_argument("--font")

    g = p.add_argument_group("viral boosters")
    g.add_argument("--hook", help="big title shown in the first seconds")
    g.add_argument("--hook-seconds", type=float)
    g.add_argument("--credit", help='small credit line, e.g. "Clip from @streamer"')
    g.add_argument("--progress-bar", action=argparse.BooleanOptionalAction, default=None)
    g.add_argument("--zoom", type=float, help="slow push-in percent over the clip (e.g. 6)")
    g.add_argument("--tighten-silence", action=argparse.BooleanOptionalAction, default=None, help="cut dead air")
    g.add_argument("--speed", type=float, help="1.0-1.5 feels snappier")
    g.add_argument("--normalize", action=argparse.BooleanOptionalAction, default=None, help="loudness to -14 LUFS")
    g.add_argument("--bleep", action=argparse.BooleanOptionalAction, default=None, help="mask swearing in captions + mute it")
    g.add_argument("--no-thumbnail", action="store_const", const=True)
    g.add_argument("--no-post-text", action="store_const", const=True)
    g.add_argument("--hashtags", help='e.g. "#gta6 #gaming"')
    a = p.parse_args()

    segments = list(a.clip)
    if a.auto:
        print(f"Scanning audio for {a.auto} moments...")
        for start, end, score in C.find_highlights(a.video, a.length, a.auto):
            print(f"  suggested {C.fmt_time(start)} -> {C.fmt_time(end)} (score {score:.1f})")
            segments.append((start, end))
    if not segments:
        p.error("give at least one --clip START-END or use --auto N")

    over = {
        "aspect": a.aspect, "resolution": a.res, "fit": a.fit, "quality": a.quality, "fps": a.fps,
        "facecam": a.facecam, "stack_ratio": a.stack_ratio, "captions": False if a.no_captions else None,
        "model": a.model, "language": a.language, "words_per_caption": a.words, "caption_position": a.position,
        "caption_style": a.caption_style, "highlight": False if a.no_highlight else None,
        "uppercase": False if a.no_uppercase else None, "pop": False if a.no_pop else None, "font": a.font,
        "hook": a.hook, "hook_seconds": a.hook_seconds, "credit": a.credit, "progress_bar": a.progress_bar,
        "zoom": a.zoom, "tighten_silence": a.tighten_silence, "speed": a.speed, "normalize": a.normalize,
        "bleep": a.bleep, "thumbnail": False if a.no_thumbnail else None,
        "post_text": False if a.no_post_text else None, "hashtags": a.hashtags,
    }
    base = C.apply_preset(a.preset) if a.preset else C.ClipSettings()
    settings = C.replace(base, **{k: v for k, v in over.items() if v is not None})
    try:
        outputs = C.make_clips(a.video, segments, a.out, settings)
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("\nDone:")
    for o in outputs:
        print(f"  {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
