"""ClipForge core: turn long videos into captioned, viral-style short-form clips.

Requires FFmpeg on PATH. Captions use faster-whisper (only loaded when needed).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple, Union

ASPECTS = {"9:16": (9, 16), "4:5": (4, 5), "1:1": (1, 1), "16:9": (16, 9)}
# blur = full frame over blurred copy | crop = fill frame | fit = black bars | stack = gameplay + facecam
FIT_MODES = ("blur", "crop", "fit", "stack")
QUALITY = {
    "low": {"crf": 28, "preset": "veryfast", "abr": "96k"},
    "medium": {"crf": 23, "preset": "veryfast", "abr": "128k"},
    "high": {"crf": 20, "preset": "medium", "abr": "160k"},
    "max": {"crf": 17, "preset": "slow", "abr": "192k"},
}
CAPTION_POSITIONS = ("bottom", "middle", "top")
# name: (base colour, spoken-word colour, boxed?)  colours are ASS &HBBGGRR
CAPTION_STYLES = {
    "classic": ("&H00FFFFFF", "&H0000FFFF", False),  # white + yellow
    "fire": ("&H00FFFFFF", "&H00008CFF", False),     # white + orange
    "neon": ("&H00FFFFFF", "&H0000FF00", False),     # white + green
    "ice": ("&H00FFFFFF", "&H00FFFF00", False),      # white + cyan
    "boxed": ("&H00FFFFFF", "&H0000FFFF", True),     # white on black box
}
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
PROFANITY = ("fuck", "shit", "bitch", "asshole", "bastard")  # matched as word prefixes

Time = Union[str, int, float]


@dataclass
class ClipSettings:
    # --- output
    aspect: str = "9:16"
    resolution: int = 1080  # length of the SHORT side in px (720 -> 720x1280 for 9:16)
    fit: str = "blur"
    quality: str = "high"
    fps: Optional[int] = None  # None keeps the source frame rate
    # --- stack layout (streams): gameplay on top, facecam below
    facecam: Tuple[float, float, float, float] = (0.70, 0.60, 0.30, 0.40)  # x, y, w, h as fractions of source
    stack_ratio: float = 0.60  # share of the height used by gameplay
    # --- captions
    captions: bool = True
    model: str = "base"
    language: Optional[str] = None  # None = auto-detect, e.g. "en", "hi"
    words_per_caption: int = 3
    caption_position: str = "bottom"
    caption_style: str = "classic"
    highlight: bool = True  # colour each word as it is spoken
    uppercase: bool = True
    pop: bool = True  # small pop-in animation on every caption
    font: str = "Arial"
    # --- viral boosters
    hook: str = ""  # big title text shown in the first seconds
    hook_seconds: float = 3.0
    credit: str = ""  # e.g. "Clip from @streamer"
    progress_bar: bool = False
    zoom: float = 0.0  # slow push-in, percent over the clip (e.g. 6)
    tighten_silence: bool = False  # cut dead air / long pauses
    silence_db: int = -35
    min_silence: float = 0.6
    speed: float = 1.0  # 1.0 - 1.5 feels snappier
    normalize: bool = False  # loudness to -14 LUFS (platform standard)
    bleep: bool = False  # mask swear words in captions and mute them in the audio
    extra_censor: Tuple[str, ...] = ()
    # --- extras
    thumbnail: bool = True
    post_text: bool = True  # write a .txt with title + hashtags next to the clip
    hashtags: str = ""  # e.g. "#gta6 #gaming"


PRESETS = {
    "shorts": dict(aspect="9:16", resolution=1080, fps=30, quality="high", normalize=True),
    "viral": dict(aspect="9:16", resolution=1080, fps=30, quality="high", normalize=True, tighten_silence=True,
                  zoom=6.0, progress_bar=True, pop=True, bleep=True, caption_style="fire"),
    "stream-stack": dict(aspect="9:16", resolution=1080, fps=30, quality="high", normalize=True, fit="stack",
                         caption_position="middle", tighten_silence=True, progress_bar=True, bleep=True,
                         caption_style="neon"),
    "fast": dict(resolution=720, quality="low", fit="crop", captions=False),
}


def apply_preset(name: str, base: Optional[ClipSettings] = None) -> ClipSettings:
    return replace(base or ClipSettings(), **PRESETS[name])


# ----------------------------------------------------------------- helpers
def check_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} not found. Install FFmpeg and make sure it is on your PATH.")


def parse_time(value: Time) -> float:
    """'90', '1:30' or '01:02:03.5' -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Bad time '{value}'. Use seconds, MM:SS or HH:MM:SS.")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def probe_duration(path: Union[str, Path]) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def probe_has_audio(path: Union[str, Path]) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True,
    ).stdout
    return "audio" in out


def output_size(aspect: str, resolution: int) -> Tuple[int, int]:
    aw, ah = ASPECTS[aspect]
    if aw <= ah:
        w, h = resolution, resolution * ah / aw
    else:
        h, w = resolution, resolution * aw / ah
    return int(round(w / 2) * 2), int(round(h / 2) * 2)  # H.264 needs even sizes


def _run(cmd: Sequence[str], cwd: Path) -> None:
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{result.stderr[-1500:]}")


# ---------------------------------------------------------- auto highlights
def find_highlights(video: Union[str, Path], clip_len: float = 30.0, count: int = 5) -> List[Tuple[float, float, float]]:
    """Suggest moments from the audio: loud AND suddenly louder than the stretch before (reactions, hype).

    Returns [(start, end, score)] in chronological order. It is a rough guide, so always review.
    """
    import numpy as np

    check_ffmpeg()
    sr = 4000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    seconds = len(audio) // sr
    if seconds == 0:
        return []
    rms = np.sqrt((audio[: seconds * sr].reshape(seconds, sr) ** 2).mean(axis=1))
    smooth = np.convolve(rms, np.ones(3) / 3, mode="same")
    jump = smooth - np.convolve(rms, np.ones(15) / 15, mode="same")  # spike vs the last ~15 s

    def z(x):
        return (x - x.mean()) / (x.std() or 1.0)

    score = z(smooth) + 0.8 * z(jump)
    picks: List[Tuple[float, float, float]] = []
    for idx in np.argsort(score)[::-1]:
        if len(picks) >= count:
            break
        end = float(min(seconds, max(0.0, idx - clip_len * 0.6) + clip_len))  # 60% lead-up, 40% aftermath
        start = float(max(0.0, end - clip_len))
        if any(start < pe and end > ps for ps, pe, _ in picks):
            continue
        picks.append((start, end, float(score[idx])))
    return sorted(picks)


# ------------------------------------------------------------ silence cuts
def detect_silences(wav, noise_db: int, min_len: float, total: float) -> List[Tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_len}",
                        "-f", "null", "-"], capture_output=True, text=True)
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    if len(starts) > len(ends):
        ends.append(total)
    return list(zip(starts, ends))


def plan_cuts(silences, total: float, pad: float = 0.15):
    """Keep a short breath (pad) of each pause. Returns (keep_segments, removed_segments)."""
    removed = [(a + pad, b - pad) for a, b in silences if (b - pad) - (a + pad) >= 0.25]
    keep, cur = [], 0.0
    for a, b in removed:
        if a - cur > 0.05:
            keep.append((cur, a))
        cur = b
    if total - cur > 0.05:
        keep.append((cur, total))
    return keep, removed


def _remap(t: float, removed) -> float:
    shift = 0.0
    for a, b in removed:
        if t >= b:
            shift += b - a
        elif t > a:
            shift += t - a
    return t - shift


# ------------------------------------------------------------------ swearing
def _is_profane(word: str, extra=()) -> bool:
    w = re.sub(r"[^a-z]", "", word.lower())
    return bool(w) and any(w.startswith(p) for p in PROFANITY + tuple(e.lower() for e in extra))


def _mask(word: str) -> str:
    m = re.match(r"^(\W*)(\w)(\w*)(\W*)$", word)
    return word if not m else f"{m.group(1)}{m.group(2)}{'*' * len(m.group(3))}{m.group(4)}"


# ----------------------------------------------------------------- captions
_MODELS: dict = {}


def transcribe(audio_path: Union[str, Path], model_size: str = "base", language: Optional[str] = None):
    """Returns [(start, end, word)] with times relative to the audio file."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("Captions and bleeping need faster-whisper. Run: pip install -r requirements.txt")

    model = _MODELS.get(model_size)
    if model is None:
        model = _MODELS[model_size] = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), language=language, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append((w.start, w.end, text))
    return words


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(words, width: int, height: int, s: ClipSettings, total: float, seam_y: Optional[int] = None) -> str:
    base = min(width, height)
    fontsize = int(base * 0.075)
    mh = int(width * 0.06)
    base_col, spoken_col, boxed = CAPTION_STYLES.get(s.caption_style, CAPTION_STYLES["classic"])
    primary = spoken_col if s.highlight else base_col
    align = {"bottom": 2, "middle": 5, "top": 8}[s.caption_position]
    margin_v = 0 if s.caption_position == "middle" else int(height * 0.18)  # keeps clear of app UI
    if seam_y is not None and s.caption_position == "middle":  # stack layout: sit on the seam
        align, margin_v = 2, max(0, int(height - seam_y - fontsize * 0.55))
    border, outline, shadow = (3, int(fontsize * 0.18), 0) if boxed else (1, max(2, int(fontsize * 0.08)), 2)

    def style(name, size, prim, sec, bstyle, out, shad, al, mv):
        return (f"Style: {name},{s.font},{size},{prim},{sec},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,"
                f"{bstyle},{out},{shad},{al},{mh},{mh},{mv},1")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        style("Default", fontsize, primary, base_col, border, outline, shadow, align, margin_v),
        style("Title", int(base * 0.085), "&H00FFFFFF", "&H00FFFFFF", 1, max(3, int(base * 0.008)), 3, 8,
              int(height * 0.10)),
        style("Credit", int(base * 0.04), "&H00FFFFFF", "&H00FFFFFF", 1, 2, 1, 7, int(height * 0.045)),
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if s.hook.strip():
        wrapped = "\\N".join(textwrap.wrap(_esc(s.hook.strip()).upper(), 16))
        end = min(s.hook_seconds * s.speed, total)
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Title,,0,0,0,,{{\\fad(120,200)}}{wrapped}")
    if s.credit.strip():
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(total)},Credit,,0,0,0,,{_esc(s.credit.strip())}")

    if not s.captions:
        words = []
    chunks, cur = [], []
    for i, (st, en, tx) in enumerate(words):
        cur.append((st, en, tx))
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (len(cur) >= s.words_per_caption or tx.endswith((".", "?", "!"))
                or nxt is None or nxt[0] - en > 0.8):
            chunks.append(cur)
            cur = []

    pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if s.pop else ""
    for ci, ch in enumerate(chunks):
        start, end = ch[0][0], ch[-1][1]
        if ci + 1 < len(chunks) and chunks[ci + 1][0][0] - end < 0.4:
            end = chunks[ci + 1][0][0]  # avoid flicker between captions
        parts = []
        for wi, (st, _en, tx) in enumerate(ch):
            txt = _esc(tx.upper() if s.uppercase else tx)
            if s.highlight:
                nxt_st = ch[wi + 1][0] if wi + 1 < len(ch) else end
                parts.append(f"{{\\k{max(1, int(round((nxt_st - st) * 100)))}}}{txt}")
            else:
                parts.append(txt)
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{pop}{' '.join(parts)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- post text / tags
_STOP = set("""the and but for you your not just like what when where which who how why all any can will would could
should about into out over under again more most some such only own same than too very its that this these those
with from have has had was were are been being dont don't gonna wanna yeah okay right going know think got get
there their they them then here really""".split())


def suggest_post_text(words, s: ClipSettings, credit_line: str = "") -> str:
    text = " ".join(w for _, _, w in words).strip()
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if len(x.split()) >= 4]
    title = s.hook.strip() or (sentences[0] if sentences else text) or "New clip"
    title = (title[:77] + "...") if len(title) > 80 else title
    tokens = [re.sub(r"[^a-z0-9]", "", w.lower()) for _, _, w in words]
    top = [w for w, _ in Counter(t for t in tokens if len(t) >= 4 and t not in _STOP).most_common(3)]
    tags: List[str] = []
    for t in ["#shorts", "#fyp"] + [f"#{x}" for x in top] + s.hashtags.split():
        t = t if t.startswith("#") else f"#{t}"
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    lines = [f"TITLE: {title}", "", f"HASHTAGS: {' '.join(tags)}"]
    if credit_line:
        lines += ["", f"CREDIT: {credit_line}"]
    lines += ["", "TIPS: post at peak hours, reply to early comments fast, and keep the first second punchy."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- the clipper
def _build_graph(w, h, s: ClipSettings, keep, has_audio, with_ass, mutes, kept_dur):
    parts: List[str] = []
    n = len(keep) if keep else 0
    if n:
        if n > 1:
            parts.append(f"[0:v]split={n}" + "".join(f"[sv{i}]" for i in range(n)))
            if has_audio:
                parts.append(f"[0:a]asplit={n}" + "".join(f"[sa{i}]" for i in range(n)))
        for i, (a, b) in enumerate(keep):
            vin = f"[sv{i}]" if n > 1 else "[0:v]"
            parts.append(f"{vin}trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
            if has_audio:
                ain = f"[sa{i}]" if n > 1 else "[0:a]"
                parts.append(f"{ain}atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        ins = "".join(f"[v{i}]" + (f"[a{i}]" if has_audio else "") for i in range(n))
        parts.append(f"{ins}concat=n={n}:v=1:a={1 if has_audio else 0}" + ("[vc][ac]" if has_audio else "[vc]"))
    else:
        parts.append("[0:v]null[vc]")
        if has_audio:
            parts.append("[0:a]anull[ac]")

    if s.fit == "crop":
        parts.append(f"[vc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[lay]")
    elif s.fit == "fit":
        parts.append(f"[vc]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[lay]")
    elif s.fit == "stack":
        fx, fy, fw, fh = s.facecam
        hg = int(h * s.stack_ratio / 2) * 2
        hf = h - hg
        parts.append(
            f"[vc]split=2[g][f];[g]scale={w}:{hg}:force_original_aspect_ratio=increase,crop={w}:{hg}[gv];"
            f"[f]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={w}:{hf}:force_original_aspect_ratio=increase,"
            f"crop={w}:{hf}[fv];[gv][fv]vstack=inputs=2,setsar=1[lay]")
    else:  # blur
        parts.append(
            f"[vc]split=2[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=20:5[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[lay]")

    post: List[str] = []
    if s.zoom > 0:
        z = s.zoom / 100
        post.append(f"scale=w='trunc(iw*(1+{z}*t/{kept_dur:.3f})/2)*2':h='trunc(ih*(1+{z}*t/{kept_dur:.3f})/2)*2'"
                    f":eval=frame,crop={w}:{h}")
    if s.fps:
        post.append(f"fps={s.fps}")
    if with_ass:
        post.append("subtitles=captions.ass")
    if s.speed != 1:
        post.append(f"setpts=PTS/{s.speed}")
    final = kept_dur / s.speed
    parts.append("[lay]" + ",".join(post or ["null"]) + ("[pv]" if s.progress_bar else "[out]"))
    if s.progress_bar:
        bh = max(6, int(h * 0.008))
        parts.append(f"color=c=0xFF2D55:s={w}x{bh}:r=30:d={final + 1:.3f}[bar]")
        parts.append(f"[pv][bar]overlay=x='-w+w*t/{final:.3f}':y=H-h:shortest=1[out]")

    if has_audio:
        af = [f"volume=enable='between(t,{a:.3f},{b:.3f})':volume=0" for a, b in mutes]
        if s.normalize:
            af.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        if s.speed != 1:
            af.append(f"atempo={s.speed}")
        parts.append("[ac]" + ",".join(af or ["anull"]) + "[aout]")
    return ";".join(parts), final


def make_clip(source, start: Time, end: Time, out_dir, settings: Optional[ClipSettings] = None,
              index: int = 1, log: Callable[[str], None] = print) -> Path:
    s = settings or ClipSettings()
    if s.aspect not in ASPECTS or s.fit not in FIT_MODES or s.quality not in QUALITY:
        raise ValueError("Invalid aspect, fit or quality setting.")
    s = replace(s, speed=min(2.0, max(0.5, s.speed)))
    check_ffmpeg()
    source = Path(source).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()

    start = max(0.0, parse_time(start))
    end = min(parse_time(end), probe_duration(source))
    if end - start < 1:
        raise ValueError("Clip must be at least 1 second long (check start/end times).")
    dur = end - start
    width, height = output_size(s.aspect, s.resolution)
    q = QUALITY[s.quality]
    out = out_dir / f"{source.stem}_clip{index:02d}_{fmt_time(start).replace(':', '-')}.mp4"
    has_audio = probe_has_audio(source)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        words, keep, removed, kept_dur = [], None, [], dur
        need_wav = has_audio and (s.captions or s.bleep or s.tighten_silence)
        if need_wav:
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
                  "-vn", "-ac", "1", "-ar", "16000", "audio.wav"], tmp)
        if has_audio and (s.captions or s.bleep):
            log("  transcribing...")
            words = transcribe(tmp / "audio.wav", s.model, s.language)
            if not words:
                log("  no speech found")
        if need_wav and s.tighten_silence:
            sil = detect_silences(tmp / "audio.wav", s.silence_db, s.min_silence, dur)
            k, removed = plan_cuts(sil, dur)
            if removed and k:
                keep, kept_dur = k, sum(b - a for a, b in k)
                log(f"  cutting {dur - kept_dur:.1f}s of dead air")
            else:
                removed = []
        if removed:
            words = [(_remap(a, removed), _remap(b, removed), t) for a, b, t in words]

        mutes = [(a - 0.03, b + 0.03) for a, b, t in words if s.bleep and _is_profane(t, s.extra_censor)]
        shown = [(a, b, _mask(t) if s.bleep and _is_profane(t, s.extra_censor) else t) for a, b, t in words]

        with_ass = bool(s.hook.strip() or s.credit.strip() or (s.captions and shown))
        if with_ass:
            seam = int(height * s.stack_ratio / 2) * 2 if s.fit == "stack" else None
            (tmp / "captions.ass").write_text(build_ass(shown, width, height, s, kept_dur, seam), encoding="utf-8")

        graph, final = _build_graph(width, height, s, keep, has_audio, with_ass, mutes, kept_dur)
        log(f"  encoding {width}x{height} ({s.quality})...")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source),
               "-filter_complex", graph, "-map", "[out]"]
        if has_audio:
            cmd += ["-map", "[aout]"]
        cmd += ["-c:v", "libx264", "-preset", q["preset"], "-crf", str(q["crf"]), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", q["abr"], "-ar", "48000", "-movflags", "+faststart", str(out)]
        _run(cmd, tmp)

        if s.thumbnail:
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.0, final / 2):.2f}", "-i", str(out),
                  "-frames:v", "1", "-q:v", "2", str(out.with_suffix('.jpg'))], tmp)
    if s.post_text:
        out.with_suffix(".txt").write_text(suggest_post_text(shown, s, s.credit.strip()), encoding="utf-8")
    return out


def make_clips(source, segments: Sequence[Tuple[Time, Time]], out_dir,
               settings: Optional[ClipSettings] = None, log: Callable[[str], None] = print) -> List[Path]:
    outputs = []
    for i, (a, b) in enumerate(segments, 1):
        log(f"[{i}/{len(segments)}] {a} -> {b}")
        outputs.append(make_clip(source, a, b, out_dir, settings, index=i, log=log))
    return outputs
