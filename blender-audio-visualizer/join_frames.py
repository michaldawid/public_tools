"""
Joins the PNG frames from the equalizer render into an MP4 video with sound.

HOW TO USE (in the same Blender file where you ran equalizer_3d.py)
  1. Scripting -> Text -> Open -> pick this file.
  2. Click "Run Script". Nothing to type: the script takes the song and the frames
     folder remembered by equalizer_3d.py. (In a new Blender file it asks for the
     frames folder - the song must then be set in AUDIO_PATH below.)
  3. Blender builds the video - usually a few minutes, the window may not respond
     in the meantime. The MP4 appears next to the frames folder.

The script creates a separate scene "join" - it doesn't touch the equalizer scene.
"""
import bpy, os, re

# =====================================================================
#  Leave empty ("") - the script uses the paths remembered by equalizer_3d.py.
#  Fill in only when the frames come from another Blender file.
FRAMES_DIR = r""      # e.g. r"C:\Users\me\Music\equalizer_frames"
AUDIO_PATH = r""      # the same song as in the equalizer
# =====================================================================
FPS    = 30
OUTPUT = ""           # empty = next to the frames folder, e.g. ...\equalizer_frames.mp4


class SetupError(Exception):
    """A problem the user can fix (shown in a popup instead of a traceback)."""


def show_message(title, lines, icon="INFO"):
    print(f"\n{title}\n  " + "\n  ".join(lines))
    if bpy.app.background or not bpy.context.window_manager.windows:
        return

    def draw(self, _ctx):
        for line in lines:
            self.layout.label(text=line)
    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def clean(p):
    p = (p or "").strip().strip('"').strip("'").strip()
    return os.path.normpath(os.path.expanduser(bpy.path.abspath(p))) if p else ""


def remembered(key):
    """Path saved by equalizer_3d.py in any scene of this file."""
    for sc in bpy.data.scenes:
        if sc.get(key):
            return sc[key]
    return ""


def join(frames_dir, audio):
    frames_dir, audio = clean(frames_dir), clean(audio)
    if not os.path.isdir(frames_dir):
        raise SetupError(f"Frames folder not found: {frames_dir}",
                         "Render the equalizer first: Render -> Render Animation (Ctrl+F12).")
    files = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith(".png"))
    if not files:
        raise SetupError(f"No PNG frames in: {frames_dir}",
                         "Render the equalizer first: Render -> Render Animation (Ctrl+F12).")
    # empty placeholder files (left when a render stopped in the middle of a frame)
    empty = [f for f in files if os.path.getsize(os.path.join(frames_dir, f)) == 0]
    if empty:
        raise SetupError(f"{len(empty)} frames are empty (e.g. {empty[0]}) - the render isn't finished.",
                         "Go back to the equalizer scene, press Ctrl+F12 to finish it,",
                         "then run this script again.")
    warnings = []
    # gaps in numbering (e.g. after a crash that wasn't resumed)
    nums = [int(m.group(1)) for f in files if (m := re.search(r"(\d+)\.png$", f, re.I))]
    missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums)) if nums else []
    if missing:
        warnings.append(f"Warning: {len(missing)} frames are missing (e.g. {missing[:3]}) - the video will skip.")
    if not os.path.isfile(audio):
        warnings.append("Warning: song not found - the video will have no sound.")
        audio = ""

    old = bpy.data.scenes.get("join")
    if old:
        bpy.data.scenes.remove(old)
    sc = bpy.data.scenes.new("join")
    sc.render.fps = FPS
    se = sc.sequence_editor_create()
    strips = se.strips if hasattr(se, "strips") else se.sequences

    img = strips.new_image("frames", os.path.join(frames_dir, files[0]), channel=1, frame_start=1)
    for f in files[1:]:
        img.elements.append(f)
    if audio:
        strips.new_sound("music", audio, channel=2, frame_start=1)

    first = bpy.data.images.load(os.path.join(frames_dir, files[0]))
    w, h = first.size
    bpy.data.images.remove(first)

    r = sc.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = w, h, 100
    sc.frame_start, sc.frame_end = 1, len(files)
    out = clean(OUTPUT) if OUTPUT else frames_dir.rstrip("\\/") + ".mp4"
    r.filepath = out
    r.use_file_extension = False
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.ffmpeg.audio_codec = "AAC"
    r.ffmpeg.audio_bitrate = 256
    sc.view_settings.view_transform = "Standard"   # frames already have final colors - no second AgX
    if hasattr(sc.render, "use_sequencer"):
        sc.render.use_sequencer = True

    print(f"Joining {len(files)} frames ({w}x{h}) -> {out}")
    if bpy.context.window:
        bpy.context.window.scene = sc
    bpy.ops.render.render(animation=True, scene=sc.name)
    show_message("Video ready", [out, f"{len(files)} frames, {len(files) / FPS:.0f} s"] + warnings,
                 icon="ERROR" if warnings else "INFO")


def run(frames_dir):
    try:
        join(frames_dir, AUDIO_PATH or remembered("eq_audio"))
    except SetupError as e:
        show_message("Join frames: fix this first", list(e.args), icon="ERROR")


class EQ_OT_pick_frames(bpy.types.Operator):
    """Pick the folder with the rendered frames"""
    bl_idname = "equalizer.pick_frames"
    bl_label = "Use this folder"

    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _context):
        run(self.directory)
        return {"FINISHED"}


frames = FRAMES_DIR or remembered("eq_frames")
if frames or bpy.app.background:
    run(frames)
else:                                        # nothing remembered -> let the user pick the folder
    if hasattr(bpy.types, EQ_OT_pick_frames.__name__):
        bpy.utils.unregister_class(getattr(bpy.types, EQ_OT_pick_frames.__name__))
    bpy.utils.register_class(EQ_OT_pick_frames)
    bpy.ops.equalizer.pick_frames("INVOKE_DEFAULT")
