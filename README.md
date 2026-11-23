# 🎬 ClipForge

Cut long videos (stream VODs, podcasts, gameplay) into **captioned, viral-style shorts** for YouTube Shorts, Reels and TikTok. Free, open source, runs fully on your laptop.

## Features
**Clipping**
- Cut any time range, or **find hype moments** automatically (loud + sudden audio spikes)
- Frame size 9:16 / 4:5 / 1:1 / 16:9, resolution 480-1440, quality low-max, 24/30/60 fps
- Framing: blurred background, crop, black bars, or **stack** (gameplay on top, facecam below, the popular stream-clip look)
- Batch export, command line **and** web UI, one-click presets

**Captions**
- Auto captions (Whisper) in 5 styles, word-by-word highlight, pop-in animation, position, font, language (Hindi works)

**Viral boosters**
- **Hook title** in the first seconds (the first second decides if people swipe)
- **Cut dead air** and long pauses for faster pacing
- **Slow zoom-in** and **speed-up** (up to 1.5x) to hold attention
- **Progress bar** that nudges people to watch to the end
- **Bleep swearing**: masks it in captions and mutes it in the audio (helps monetization)
- **Loudness normalisation** (-14 LUFS, the platform standard)
- **Credit line** ("Clip from @streamer") to respect creators
- **Thumbnail (.jpg)** and a **title + hashtags (.txt)** saved next to every clip

## Setup
1. Install **Python 3.9+** and **FFmpeg** (`winget install ffmpeg` on Windows, `brew install ffmpeg` on Mac, `sudo apt install ffmpeg` on Linux).
2. `pip install -r requirements.txt`

## Use it
**Web UI:** `streamlit run app.py`

**Command line**
```bash
# a viral-style clip with a hook and credit
python cli.py vod.mp4 -c 00:12:30-00:13:00 --preset viral --hook "He actually did it" --credit "Clip from @streamer"

# let it find 5 moments, stream layout (gameplay + facecam in the bottom-right corner)
python cli.py vod.mp4 --auto 5 --length 30 --preset stream-stack --facecam 0.70,0.60,0.30,0.40

# quick, small, no captions
python cli.py vod.mp4 -c 10:00-10:45 --preset fast
```
Run `python cli.py -h` for every option. Clips are saved in `clips/`.

## Presets
| Preset | What it does |
|---|---|
| `shorts` | 9:16, 1080p, 30 fps, captions, normalised audio |
| `viral` | shorts + dead-air cut, slow zoom, progress bar, bleeping, fire captions |
| `stream-stack` | viral-style with gameplay on top and facecam below |
| `fast` | 720p, crop, low quality, no captions: quick drafts |

Any flag overrides the preset, e.g. `--preset viral --no-bleep --zoom 0`.

## Tips for shorts that perform
- Keep clips **20-45 seconds**. Start on the best moment, not the build-up.
- Use a **hook** that creates a question ("Chat couldn't believe this").
- Check the facecam box for `stack`: the `--facecam x,y,w,h` values are fractions of the original frame.
- Always review auto-suggested moments. They are loudness-based, not a guarantee.
- The first caption run downloads the Whisper model once (internet needed), then works offline.
- Linux: if captions look wrong, install the Arial font or pass `--font` with one you have.
- Only clip content you own or have permission to use, and credit the creator. Check Rockstar's and each platform's rules before posting game footage.

## Files
| File | What it does |
|---|---|
| `clipper.py` | core engine (FFmpeg + Whisper) |
| `cli.py` | command line |
| `app.py` | Streamlit web UI |

## License
MIT
