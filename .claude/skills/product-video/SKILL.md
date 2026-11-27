---
name: product-video
description: Makes CoffeePy's product video - the housing's 3D model rendered in Blender with the real kiosk on its display, screen recordings of kiosk, portal and dashboard from a demo copy of the app, captions, CC0 music, and exports for sharing and for YouTube (plus the YouTube title and description). Use whenever the user wants a product/promo/teaser video, a new cut of it, new renders of the housing, or a re-recording after the UI changed.
---

# The product video

About 96 s, 1080p30, English: title card → housing (hero turntable, exploded view, card tap and order on the
display) → pay → inventory at the kiosk → ranking and pillory on the display → portal on a phone → dashboard →
"what a cup really costs" → end card. Everything is scripted in `scripts/`; nothing is edited by hand, so a UI
change or a new housing only means re-running steps.

Work dir: `.render/video/work/` (git-ignored, on disk - several GB; `/tmp` is RAM here, never use it for this).
`VIDEO_WORK` moves it, `VIDEO_MODEL` points at another OBJ. Deliverables go to `.render/video/` - copy them
there from `work/out/` at the end.

## Doing it

```bash
cd .claude/skills/product-video/scripts
./get_blender.sh                      # portable Blender 4.5 LTS into work/ (once, ~350 MB, sha256-checked)
./get_music.sh                        # the CC0 track + MUSIC.txt (once)
./setup_app.sh                        # demo copy on :8765 + headless Chromium on CDP :9333 (fresh: after UI changes)
python3 scene_kiosk.py                # kiosk_wait/order/ranking/pay  (~1 min)
python3 scene_web.py                  # portal (phone) + dashboard    (~40 s)
python3 scene_inventory.py            # kiosk purchase + stock-take, dashboard inventory pages
./setup_app.sh stop
W=../../../../.render/video/work; B=$(ls -d $W/blender/blender-*/blender)
$B -b --factory-startup -P import.py                       # OBJ -> work/3d/model.blend (prints part bboxes)
$B -b $W/3d/model.blend -P build_scene.py                  # materials, studio, screen, card, explode rigs
$B -b $W/3d/scene.blend -P shots.py -- explode stills 1,130,250      # half-size previews in work/render/
./render_all.sh                       # all 5 shots, ~1 frame/s on the RX 6700 XT: ~25 min for 1380 frames
python3 compose.py segments && python3 compose.py final    # out/coffeepy.mp4 (H.264) + coffeepy-av1.mp4
python3 compose.py youtube            # out/coffeepy-youtube.mov - ProRes 1440p, the YouTube upload
```

`render_all.sh` and the renders resume: frames on disk are kept, so an interruption costs nothing - delete
`work/render/<shot>/` to redo a shot, `work/seq/<clip>/` after re-recording a clip the display shows. Run long
steps in the background and say how long they take; a full run from nothing is roughly 40 minutes.

**Look before you deliver.** After every render or cut, build a contact sheet (`ffmpeg ... tile=4x4`, or frames
`hstack`/`vstack`) and Read it - every problem in the first video was found that way, none by the scripts. Judge
gradients/banding only on full-resolution crops: a downscaled sheet shows contour bands the video doesn't have.

## What lives where

| Want to change | Where |
|---|---|
| Storyboard, captions, segment order/lengths (`trim=`), title and end card | `compose.py` `SEGMENTS` |
| What is clicked/typed in a recording, timing | `scene_*.py` (`b.tap`, `typing`, `time.sleep`) |
| Demo data (names, sorts, month orders, reader online) | `setup_app.sh`, on top of user-manual's `demo_data.py` |
| Camera paths, explode offsets, which clip the display shows | `shots.py` (one branch per shot) |
| Materials, lights, screen plane, card, explode groups | `build_scene.py` (rebuild scene.blend afterwards) |
| Music | `get_music.sh` + `MUSIC`, `MUSIC_START` in `compose.py` |

## The housing model

`.render/render_modell/` (from the CAD tool, see its LIESMICH.md): millimetres, **Z up, front is -Y**. Import with
`forward_axis='Y', up_axis='Z'` - the identity; LIESMICH's "forward -Y" turns the model 180° in Blender's importer.
The display glass (`1118`) is tilted 13.34° (normal `(0,-0.23,0.97)`), its glass 158×90 mm; the screen plane is
154.2×86 mm centred at u=95.6, v=68 mm, 27.08 mm out along the normal. If the housing changes, re-run the probe
in `import.py`'s output and check a still - a screen that floats or sinks into the bezel is the first sign.

**Exploded view, top down as it is assembled** (the user corrected this once): frame (`*_Rand`, logos) → shell
(`*_Schale`, the lid, with the dowels) → display + NFC board → Raspberry Pi (+ RTC, cables) → bottom (`*_Boden`
with its screws). The whole device lifts off the floor (`rig_all`) so the bottom stays above it. A part that is
new in the model and matches no group in `build_scene.py` stays put - check the explode still.

The Raspberry Pi and display meshes are third-party (LIESMICH); their licences have to be checked before a
video is published - say so when delivering.

## Traps that already cost time

- **Magenta screen** = Blender found no image. A cyclic image sequence adds `frame_offset` *after* wrapping, so
  offset + shot length must stay within the clip's frame count - `shots.py` now refuses that before rendering.
- **The kiosk clock**: `rec.py` shifts only a bare `new Date()` to 08:42 (clock.js); `Date.now()` stays true,
  or the countdowns against the server's `data-deadline` break.
- **Demo app warnings on camera**: no NFC bridge ("card reader not available"), no storage helper (red backup
  light), a month ranking empty on the 2nd of the month - `setup_app.sh` covers all three. A new kiosk warning
  will show up the same way; look at the wait screen in the first contact sheet.
- **Recordings end on a frozen last frame** for as long as the clip's container says - long portal/dashboard
  clips grew by ~8 s. `segment_2d(..., trim=)` cuts every 2D segment to its action.
- The pay screen's QR codes are below the fold at 1024×600 (also in the manual's screenshot): the scene scrolls
  the panel to show them.
- **Anything new in camera view after a framing change**: the NFC card is parked off-screen and only visible in
  the order shot; the world shows dark coffee to camera rays, the HDRI only in reflections (a white blob otherwise).
- View transform is **Standard**, not AgX - AgX greys out the screen's own colours.
- EEVEE Next renders headless on the AMD card; Cycles was never needed.
- **Encoders on this Fedora box**: no libx264, no H.264 VAAPI (Mesa), OpenH264 ignores the bitrate (~2 Mbit/s).
  Downloading a static ffmpeg with x264 was refused by the permission check - don't retry it without the user's
  go-ahead. Hence: H.264 via OpenH264 for compatibility, AV1 (SVT, CRF 20) for quality, ProRes 422 HQ for
  YouTube. Tag colours with `setparams` - `prores_ks` ignores `-color_primaries`.
- Dark 8-bit gradients band and the encoder makes blocks of the bands: the 2D backdrop carries a little grain.
- FreePD (CC0 music) has closed. Free Music Archive artists who publish CC0 - HoliznaCC0 - work; check the
  licence on the track page itself. "GEMA-frei" alone does not mean free to use; CC0 does.

## Real data never ends up in the video

The demo copy only holds the manual's demo members (Anna B., Ben K., ...); never record the test Pi or the
checkout's own database. If a scene ever has to come from the device, the user-manual skill's blur rules apply.

## YouTube

Upload `coffeepy-youtube.mov`: ProRes 422 HQ, 2560×1440 (from 1440p YouTube serves VP9/AV1 even to 1080p
viewers), BT.709-tagged, PCM audio at -14 LUFS (YouTube's own target). ~4 GB for 96 s.

Title used: **"CoffeePy – The coffee tally for the office kitchen"**. Description: a two-line pitch, the
feature list, hardware, "runs offline, MIT", the repo link, chapters and the music credit (not required for
CC0, but it answers Content ID claims). **Chapters need ≥ 10 s each and start at 0:00** - work them out from the
segment lengths minus 0.5 s per crossfade and merge short ones; for the first cut they were 0:00 housing,
0:19 order, 0:30 paying + purchases, 0:50 rankings + pillory, 1:02 phone, 1:14 dashboard + costs.

## When done

Copy `work/out/*` (and `work/music/MUSIC.txt`) to `.render/video/`, report the files with what each is for, the
music licence, the third-party model licences, and anything the contact sheets showed that wasn't fixed. Nothing
here is committed unless the user says so.
