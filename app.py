"""ClipForge web UI. Run with:  streamlit run app.py"""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import clipper as C

st.set_page_config(page_title="ClipForge", page_icon="🎬", layout="wide")
st.title("🎬 ClipForge")
st.caption("Cut long videos into captioned, viral-style shorts. Everything runs on your own laptop.")

try:
    C.check_ffmpeg()
except RuntimeError as e:
    st.error(str(e))
    st.stop()

CORNERS = {"Bottom right": (1, 1), "Bottom left": (0, 1), "Top right": (1, 0), "Top left": (0, 0)}

# ------------------------------------------------------------------ settings
with st.sidebar:
    preset = st.selectbox("Preset", ["custom"] + list(C.PRESETS), index=2,
                          help="viral = captions + dead-air cut + zoom + progress bar + bleep. "
                               "stream-stack = gameplay on top, facecam below.")
    P = C.apply_preset(preset) if preset != "custom" else C.ClipSettings()

    def k(name):  # widgets reset to the preset's values when the preset changes
        return f"{preset}-{name}"

    def pick(label, options, value, name, **kw):
        options = list(options)
        return st.selectbox(label, options, index=options.index(value), key=k(name), **kw)

    st.header("Output")
    aspect = pick("Frame size", C.ASPECTS, P.aspect, "aspect", help="9:16 = Shorts / Reels / TikTok")
    resolution = pick("Resolution (short side)", [480, 720, 1080, 1440], P.resolution, "res")
    fit = pick("Framing", C.FIT_MODES, P.fit, "fit",
               help="blur = full frame over blurred background, crop = fill frame, fit = black bars, "
                    "stack = gameplay + facecam")
    quality = st.select_slider("Quality", list(C.QUALITY), value=P.quality, key=k("quality"))
    fps_opts = ["Same as source", 24, 30, 60]
    fps = pick("Frame rate", fps_opts, P.fps or "Same as source", "fps")

    facecam, stack_ratio = P.facecam, P.stack_ratio
    if fit == "stack":
        st.subheader("Facecam box")
        corner = pick("Facecam corner in the original", list(CORNERS), "Bottom right", "corner")
        fw = st.slider("Width (% of frame)", 10, 50, 30, key=k("fw")) / 100
        fh = st.slider("Height (% of frame)", 10, 60, 40, key=k("fh")) / 100
        cx, cy = CORNERS[corner]
        facecam = (cx * (1 - fw), cy * (1 - fh), fw, fh)
        stack_ratio = st.slider("Gameplay share of screen (%)", 40, 80, 60, key=k("ratio")) / 100

    st.header("Captions")
    captions = st.toggle("Auto captions", value=P.captions, key=k("captions"))
    cap_style = pick("Style", C.CAPTION_STYLES, P.caption_style, "cstyle")
    model = pick("Accuracy / speed", C.WHISPER_MODELS, P.model, "model")
    language = st.text_input("Language code (blank = auto)", placeholder="en, hi, ...", key=k("lang"))
    words = st.slider("Words per caption", 1, 6, P.words_per_caption, key=k("words"))
    position = pick("Position", C.CAPTION_POSITIONS, P.caption_position, "pos")
    highlight = st.toggle("Highlight spoken word", value=P.highlight, key=k("hl"))
    uppercase = st.toggle("UPPERCASE", value=P.uppercase, key=k("upper"))
    pop = st.toggle("Pop-in animation", value=P.pop, key=k("pop"))
    font = st.text_input("Font", P.font, key=k("font"))

    st.header("Viral boosters")
    hook = st.text_input("Hook title (first seconds)", placeholder="He actually did it...", key=k("hook"))
    hook_seconds = st.slider("Hook shows for (s)", 1.0, 6.0, P.hook_seconds, key=k("hooks"))
    credit = st.text_input("Credit line", placeholder="Clip from @streamer", key=k("credit"))
    progress_bar = st.toggle("Progress bar", value=P.progress_bar, key=k("bar"))
    zoom = st.slider("Slow zoom-in (%)", 0, 15, int(P.zoom), key=k("zoom"))
    tighten = st.toggle("Cut dead air / long pauses", value=P.tighten_silence, key=k("tight"))
    speed = st.slider("Speed", 0.8, 1.5, P.speed, 0.05, key=k("speed"))
    normalize = st.toggle("Normalize loudness", value=P.normalize, key=k("norm"))
    auto_credit = st.toggle("Auto-credit the channel for links", value=True, key=k("autocred"),
                            help="Adds 'Clip from <channel name>' when you paste a link")
    bleep = st.toggle("Bleep swearing", value=P.bleep, key=k("bleep"),
                      help="Masks swear words in captions and mutes them in the audio")

    st.header("Extras")
    thumbnail = st.toggle("Save thumbnail (.jpg)", value=True, key=k("thumb"))
    post_text = st.toggle("Save title + hashtags (.txt)", value=True, key=k("post"))
    hashtags = st.text_input("Your hashtags", placeholder="#gta6 #gaming", key=k("tags"))

settings = C.ClipSettings(
    aspect=aspect, resolution=resolution, fit=fit, quality=quality,
    fps=None if fps == "Same as source" else int(fps), facecam=facecam, stack_ratio=stack_ratio,
    captions=captions, model=model, language=language.strip() or None, words_per_caption=words,
    caption_position=position, caption_style=cap_style, highlight=highlight, uppercase=uppercase, pop=pop,
    font=font.strip() or "Arial", hook=hook, hook_seconds=hook_seconds, credit=credit,
    progress_bar=progress_bar, zoom=float(zoom), tighten_silence=tighten, speed=speed, normalize=normalize,
    bleep=bleep, thumbnail=thumbnail, post_text=post_text, hashtags=hashtags,
)

# -------------------------------------------------------------------- source
@st.cache_data(show_spinner="Reading the link...")
def cached_info(url: str) -> dict:
    return C.video_info(url)


SRC_LINK, SRC_PATH, SRC_UPLOAD = "Video link (YouTube, Twitch...)", "Local file path", "Upload"
mode = st.radio("Video source", [SRC_LINK, SRC_PATH, SRC_UPLOAD], horizontal=True)
src, uploader = None, ""

if mode == SRC_LINK:
    url = st.text_input("Paste the video link", placeholder="https://www.youtube.com/watch?v=...").strip()
    dl_res = st.selectbox("Download quality", [480, 720, 1080], index=1,
                          help="Lower = faster download and smaller file. 720 is plenty for shorts.")
    st.caption("Only clip videos you own or have permission to use, and credit the creator.")
    if url:
        downloads = st.session_state.setdefault("downloads", {})
        key = f"{url}|{dl_res}"
        if key in downloads and Path(downloads[key]["path"]).is_file():
            src, uploader = Path(downloads[key]["path"]), downloads[key]["uploader"]
        else:
            try:
                meta = cached_info(url)
            except RuntimeError as e:
                st.error(str(e))
                st.stop()
            st.write(f"**{meta['title']}** by {meta['uploader'] or 'unknown'} ({C.fmt_time(meta['duration'])})")
            if meta["duration"] > 2 * 3600:
                st.warning("This is a long video, so the download will be big and slow. Try 480p.")
            if st.button("⬇️ Download video", type="primary"):
                with st.status("Downloading...", expanded=True) as status:
                    try:
                        path, _ = C.download_video(url, "downloads", dl_res, meta, st.write)
                    except RuntimeError as e:
                        status.update(label="Download failed", state="error")
                        st.error(str(e))
                        st.stop()
                    status.update(label="Downloaded", state="complete")
                downloads[key] = {"path": str(path), "uploader": meta["uploader"]}
                src, uploader = path, meta["uploader"]
elif mode == SRC_PATH:
    raw = st.text_input("Path to video", placeholder=r"C:\Videos\stream_vod.mp4")
    if raw.strip():
        candidate = Path(raw.strip().strip('"'))
        if candidate.is_file():
            src = candidate
        else:
            st.warning("File not found.")
else:
    up = st.file_uploader("Upload a video", type=["mp4", "mkv", "mov", "webm", "avi"])
    if up:
        folder = Path(tempfile.gettempdir()) / "clipforge_uploads"
        folder.mkdir(exist_ok=True)
        src = folder / up.name
        if not src.exists() or src.stat().st_size != up.size:
            src.write_bytes(up.getbuffer())

if src is None:
    st.stop()

if mode == SRC_LINK and uploader and auto_credit and not settings.credit.strip():
    settings = C.replace(settings, credit=f"Clip from {uploader}")

st.info(f"**{src.name}**: {C.fmt_time(C.probe_duration(src))} long")

# ------------------------------------------------------------------ segments
if "rows" not in st.session_state:
    st.session_state.rows = pd.DataFrame({"Use": [True], "Start": ["00:00:00"], "End": ["00:00:30"]})
    st.session_state.ver = 0


def export_jobs(jobs):
    results = []
    with st.status("Exporting...", expanded=True) as status:
        try:
            for i, (a, b) in enumerate(jobs, 1):
                st.write(f"Clip {i}/{len(jobs)}: {a} → {b}")
                results.append(C.make_clip(src, a, b, out_dir, settings, index=i, log=st.write))
            status.update(label=f"Done! {len(results)} clip(s) saved in the '{out_dir}' folder", state="complete")
        except (RuntimeError, ValueError) as e:
            status.update(label="Failed", state="error")
            st.error(str(e))
    for path in results:
        st.video(str(path))
        d1, d2 = st.columns(2)
        d1.download_button(f"Download {path.name}", path.read_bytes(), file_name=path.name, mime="video/mp4")
        if path.with_suffix(".jpg").exists():
            d2.download_button("Thumbnail", path.with_suffix(".jpg").read_bytes(),
                               file_name=path.with_suffix(".jpg").name, mime="image/jpeg", key=f"t{path.name}")
        if path.with_suffix(".txt").exists():
            with st.expander("Title + hashtags"):
                st.code(path.with_suffix(".txt").read_text(encoding="utf-8"))


st.subheader("Clips")
c1, c2 = st.columns(2)
clip_len = c1.slider("Clip length (s)", 10, 90, 30, help="Shorts work best at 20-45 seconds")
how_many = c2.number_input("How many clips", 1, 20, 5)
out_dir = st.text_input("Save clips to folder", "clips")

b1, b2 = st.columns(2)
auto = b1.button("⚡ Auto-generate clips", type="primary",
                 help="Finds the hype moments and exports them all in one click")
find = b2.button("🔍 Just find hype moments")

auto_jobs = None
if auto or find:
    with st.spinner("Scanning audio..."):
        found = C.find_highlights(src, clip_len, int(how_many))
    if not found:
        st.warning("No moments found. Add clip times in the table below.")
    else:
        st.session_state.rows = pd.DataFrame({
            "Use": [True] * len(found),
            "Start": [C.fmt_time(a) for a, _, _ in found],
            "End": [C.fmt_time(b) for _, b, _ in found],
        })
        st.session_state.ver += 1
        if auto:
            auto_jobs = [(C.fmt_time(a), C.fmt_time(b)) for a, b, _ in found]

st.caption("Suggestions are based on loud, sudden audio spikes, so review them. Edit times "
           "(seconds, MM:SS or HH:MM:SS), untick to skip, or add rows at the bottom.")
table = st.data_editor(st.session_state.rows, num_rows="dynamic", key=f"editor_{st.session_state.ver}")

if auto_jobs:
    export_jobs(auto_jobs)
elif st.button("🎬 Export selected clips"):
    jobs = [(r.Start, r.End) for r in table.itertuples() if r.Use and r.Start and r.End]
    if jobs:
        export_jobs(jobs)
    else:
        st.warning("Tick at least one clip with a start and end time.")
