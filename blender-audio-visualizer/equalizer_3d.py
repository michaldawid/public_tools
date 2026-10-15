"""
Circular 3D audio equalizer for Blender (tested on Blender 5.2).

HOW TO USE
  1. Open the "Scripting" tab -> Text -> Open -> pick this file.
  2. Click "Run Script" (or press Alt+P).
     A file browser opens - pick your song (mp3 / wav / flac / ogg / m4a).
     (Or type the path into AUDIO_PATH below - then the browser is skipped.)
  3. Space = preview with sound.
  4. Render -> Render Animation (Ctrl+F12) renders the frames.
  5. Run join_frames.py to turn the frames into an MP4 with sound.

From the command line:
  blender -b -P equalizer_3d.py -- /path/to/song.mp3
"""
import bpy, math, os, sys, colorsys
import numpy as np

# =====================================================================
#  YOUR SONG - leave empty ("") to pick it in a file browser.
#  Example (Windows): r"C:\Users\me\Music\song.mp3"
#  Example (Mac):     r"/Users/me/Music/song.mp3"
AUDIO_PATH = r""
# =====================================================================

# ---------------- LOOK & FEEL (optional) ----------------
FPS          = 30
BANDS        = 36        # frequency bands (the ring shows 2x, mirrored)
RADIUS       = 4.0       # ring radius
MAX_HEIGHT   = 3.2       # max bar height
FREQ_MIN     = 35.0
FREQ_MAX     = 14000.0
HUE_A, HUE_B = 0.52, 0.86   # from teal to magenta
ATTACK       = 0.75      # 0..1, how fast a bar rises
RELEASE      = 0.18      # 0..1, how fast it falls
ENGINE       = "EEVEE"   # "EEVEE" (fast) or "CYCLES" (prettier, slow)
RESOLUTION   = (1920, 1080)
OUTPUT       = "//equalizer_"   # "//" = next to the song (or next to the saved .blend)
OUTPUT_MODE  = "PNG"     # "PNG" = frames, survives crashes (recommended) | "MP4" = video at once
# ---------------------------------------------------------

AUDIO_FILTER = "*.mp3;*.wav;*.flac;*.ogg;*.m4a;*.aac"

if "--" in sys.argv:                       # path given on the command line
    args = sys.argv[sys.argv.index("--") + 1:]
    if args:
        AUDIO_PATH = args[0]


class SetupError(Exception):
    """A problem the user can fix (shown in a popup instead of a traceback)."""


def show_message(title, lines, icon="INFO"):
    """Popup in the Blender window + the same text in the console."""
    print(f"\n{title}\n  " + "\n  ".join(lines))
    if bpy.app.background or not bpy.context.window_manager.windows:
        return

    def draw(self, _ctx):
        for line in lines:
            self.layout.label(text=line)
    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def clean_path(path):
    """Removes quotes from "Copy as path", resolves // and ~."""
    p = path.strip().strip('"').strip("'").strip()
    if not p:
        return ""
    p = bpy.path.abspath(p) if p.startswith("//") else p
    return os.path.normpath(os.path.expanduser(p))


def load_audio(path):
    """Returns (mono float32, sample_rate). Reads mp3/wav/flac/ogg/m4a."""
    import aud
    try:
        snd = aud.Sound(path)
        snd.specs                                 # aud reads the file only here
        data = snd.data()                         # (samples, channels)
    except Exception as e:
        raise SetupError(f"Blender can't read this audio file: {os.path.basename(path)}",
                         "Convert it to WAV (e.g. in Audacity) and pick it again.", f"Details: {e}")
    if data.size == 0:
        raise SetupError(f"The audio file is empty: {os.path.basename(path)}")
    return data.mean(axis=1).astype(np.float32), int(snd.specs[0])


def analyse(mono, sr, n_frames):
    win = 4096
    hann = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(FREQ_MIN, FREQ_MAX, BANDS + 1)
    idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(BANDS)]
    idx = [ix if len(ix) else np.array([np.argmin(abs(freqs - edges[i]))]) for i, ix in enumerate(idx)]
    pad = np.concatenate([np.zeros(win), mono, np.zeros(win)])
    out = np.zeros((n_frames, BANDS))
    for f in range(n_frames):
        c = int(f / FPS * sr) + win
        spec = np.abs(np.fft.rfft(pad[c - win // 2:c + win // 2] * hann))
        out[f] = [spec[ix].mean() for ix in idx]
    out *= np.sqrt(np.arange(1, BANDS + 1))[None, :] ** 0.8   # balance: high tones are quieter
    db = 20 * np.log10(out + 1e-9)
    lo, hi = np.percentile(db, 30), np.percentile(db, 99.5)
    norm = np.clip((db - lo) / (hi - lo), 0, 1) ** 1.6
    sm = np.zeros_like(norm)                 # attack/release smoothing
    for f in range(n_frames):
        prev = sm[f - 1] if f else norm[0]
        k = np.where(norm[f] > prev, ATTACK, RELEASE)
        sm[f] = prev + (norm[f] - prev) * k
    return sm


def clear_scene():
    """Clears the current scene without resetting Blender (a reset breaks the GUI context)."""
    sc = bpy.context.scene
    if sc.sequence_editor:
        se = sc.sequence_editor
        strips = se.strips if hasattr(se, "strips") else se.sequences
        for s in list(strips):
            strips.remove(s)
    for coll in list(sc.collection.children):
        sc.collection.children.unlink(coll)
    for data in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                 bpy.data.lights, bpy.data.actions, bpy.data.node_groups, bpy.data.worlds,
                 bpy.data.collections, bpy.data.sounds):
        bpy.data.batch_remove(list(data))
    for img in list(bpy.data.images):
        if img.name.startswith("spectrum"):
            bpy.data.images.remove(img)


def mesh_object(name, build_fn, location=(0, 0, 0), rotation=(0, 0, 0)):
    """Creates an object from a bmesh-built mesh - no operators, no context needed."""
    import bmesh
    bm = bmesh.new()
    build_fn(bm)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.location, ob.rotation_euler = location, rotation
    bpy.context.scene.collection.objects.link(ob)
    return ob


def bar_material(strength):
    """One shared material for all bars - the color comes from the object (ob.color).
    EEVEE compiles 1 shader instead of 72."""
    m = bpy.data.materials.new("bars")
    m.use_nodes = True
    N, L = m.node_tree.nodes, m.node_tree.links
    b = N["Principled BSDF"]
    info = N.new("ShaderNodeObjectInfo")
    L.new(info.outputs["Color"], b.inputs["Base Color"])
    L.new(info.outputs["Color"], b.inputs["Emission Color"])
    b.inputs["Emission Strength"].default_value = strength
    b.inputs["Roughness"].default_value = 0.3
    return m


def emission_material(name, color, strength):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Emission Color"].default_value = (*color, 1)
    bsdf.inputs["Emission Strength"].default_value = strength
    bsdf.inputs["Roughness"].default_value = 0.3
    return m


def bake_curve(ob, path, index, values):
    """Writes a whole property animation at once (instead of thousands of keyframe_insert calls)."""
    n = len(values)
    setattr_vec = getattr(ob, path)
    setattr_vec[index] = float(values[0])
    ob.keyframe_insert(path, index=index, frame=1)
    act = ob.animation_data.action
    if hasattr(act, "fcurve_ensure_for_datablock"):          # Blender 4.4+ / 5.x
        fc = act.fcurve_ensure_for_datablock(ob, path, index=index)
    else:
        fc = act.fcurves.find(path, index=index)
    kp = fc.keyframe_points
    kp.add(n - 1)
    co = np.empty(n * 2, np.float32)
    co[0::2] = np.arange(1, n + 1)
    co[1::2] = values
    kp.foreach_set("co", co)
    kp.foreach_set("interpolation", np.full(n, 2, np.int32))   # LINEAR - no overshoot
    fc.update()


def setup_output(r, audio_path):
    """PNG: every frame is a separate file - after a crash the render resumes where it stopped.
    MP4: a video with sound at once, but a crash = the whole render is lost."""
    base = os.path.dirname(audio_path) if (OUTPUT.startswith("//") and not bpy.data.filepath) else None
    name = OUTPUT[2:] if OUTPUT.startswith("//") else OUTPUT
    r.use_lock_interface = True                  # a long render in the GUI is more stable
    if OUTPUT_MODE == "PNG":
        folder = os.path.join(base, name + "frames") if base else OUTPUT + "frames"
        r.filepath = os.path.join(folder, "frame_####")
        if hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = "PNG"
        r.image_settings.color_mode = "RGB"
        r.use_overwrite = False                  # finished frames are skipped when resuming
        r.use_placeholder = True
        return bpy.path.abspath(folder)
    r.filepath = os.path.join(base, name) if base else OUTPUT
    if hasattr(r.image_settings, "media_type"):
        r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"; r.ffmpeg.codec = "H264"; r.ffmpeg.audio_codec = "AAC"
    return bpy.path.abspath(r.filepath)


def build(audio_path):
    audio_path = clean_path(audio_path)
    if not os.path.isfile(audio_path):
        raise SetupError(f"Song not found: {audio_path}",
                         "Check AUDIO_PATH - it must be the full path to the file,",
                         "or set AUDIO_PATH = r\"\" and pick the song in the file browser.")
    mono, sr = load_audio(audio_path)

    clear_scene()
    sc = bpy.context.scene
    sc.render.fps = FPS
    duration = len(mono) / sr
    n_frames = int(duration * FPS)
    sc.frame_start, sc.frame_end = 1, n_frames
    levels = analyse(mono, sr, n_frames)

    # sound in the sequencer -> preview and export with audio
    sc.sequence_editor_create()
    seqs = sc.sequence_editor.strips if hasattr(sc.sequence_editor, "strips") else sc.sequence_editor.sequences
    seqs.new_sound("music", audio_path, channel=1, frame_start=1)
    sc.sync_mode = "AUDIO_SYNC"

    # --- bars: one shared mesh with the origin at the bottom ---
    mesh = bpy.data.meshes.new("bar")
    w, d = 0.075, 0.11
    v = [(x, y, z) for z in (0, 1) for x, y in ((-w, -d), (w, -d), (w, d), (-w, d))]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(v, [], f)
    mesh.materials.append(bar_material(2.2))
    coll = bpy.data.collections.new("Equalizer")
    sc.collection.children.link(coll)

    n_bars = BANDS * 2
    bars = []
    for i in range(n_bars):
        band = i if i < BANDS else n_bars - 1 - i            # mirrored
        t = band / (BANDS - 1)
        rgb = colorsys.hsv_to_rgb(HUE_A + (HUE_B - HUE_A) * t, 1.0, 1.0)
        ob = bpy.data.objects.new(f"bar_{i:03d}", mesh)
        ob.color = (*rgb, 1.0)
        a = 2 * math.pi * i / n_bars
        ob.location = (RADIUS * math.cos(a), RADIUS * math.sin(a), 0)
        ob.rotation_euler = (0, 0, a)
        coll.objects.link(ob)
        bars.append((ob, band))

    # core pulsing with the bass
    import bmesh
    core = mesh_object("core", lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0),
                       location=(0, 0, 1.3))
    wf = core.modifiers.new("wire", "WIREFRAME"); wf.thickness = 0.035
    core.data.materials.append(emission_material("core", (1.0, 0.35, 0.8), 3.5))
    bass = levels[:, :4].mean(axis=1)

    # keyframes
    for ob, band in bars:
        bake_curve(ob, "scale", 2, 0.05 + MAX_HEIGHT * levels[:, band])
    for axis in range(3):
        bake_curve(core, "scale", axis, 0.6 + 0.55 * bass)

    # floor - dark mirror
    floor = mesh_object("floor", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=30))
    fm = bpy.data.materials.new("floor"); fm.use_nodes = True
    b = fm.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.004, 0.004, 0.008, 1)
    b.inputs["Roughness"].default_value = 0.12
    b.inputs["Metallic"].default_value = 0.6
    floor.data.materials.append(fm)

    # world: almost black
    world = bpy.data.worlds.new("world"); sc.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0, 0.0, 0.003, 1)

    # camera orbiting the center
    pivot = bpy.data.objects.new("cam_pivot", None)
    sc.collection.objects.link(pivot)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 32
    cam_data.dof.use_dof = True
    cam_data.dof.aperture_fstop = 2.2
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    cam.parent = pivot
    cam.location = (0, -11.5, 3.0)
    target = bpy.data.objects.new("cam_target", None)
    target.location = (0, 0, 1.0)
    sc.collection.objects.link(target)
    cam_data.dof.focus_object = target
    tr = cam.constraints.new("TRACK_TO"); tr.target = target
    sc.camera = cam
    pivot.rotation_euler.z = 0
    pivot.keyframe_insert("rotation_euler", index=2, frame=1)
    pivot.rotation_euler.z = math.radians(min(360, duration * 6))   # ~6 deg/s
    pivot.keyframe_insert("rotation_euler", index=2, frame=n_frames)
    try:  # linear orbit
        for fc in pivot.animation_data.action.fcurves:
            for k in fc.keyframe_points: k.interpolation = "LINEAR"
    except Exception:
        pass

    # render
    r = sc.render
    r.resolution_x, r.resolution_y = RESOLUTION
    output = setup_output(r, audio_path)
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Punchy"
    if ENGINE == "CYCLES":
        r.engine = "CYCLES"
        sc.cycles.samples = 96
        sc.cycles.use_denoising = True
    else:
        r.engine = "BLENDER_EEVEE"
        e = sc.eevee
        for attr, val in (("use_raytracing", True), ("taa_render_samples", 32)):
            if hasattr(e, attr): setattr(e, attr, val)
    add_glow(sc)
    sc.frame_set(1)

    # remembered for join_frames.py - it needs the same song and the frames folder
    sc["eq_audio"] = audio_path
    sc["eq_frames"] = output if OUTPUT_MODE == "PNG" else ""

    next_step = ("Then run join_frames.py to make the MP4." if OUTPUT_MODE == "PNG"
                 else "The MP4 with sound is saved there.")
    show_message("Equalizer ready", [
        f"Song: {os.path.basename(audio_path)} ({duration:.0f} s, {n_frames} frames)",
        "Space = preview with sound.",
        "Render -> Render Animation (Ctrl+F12) saves the frames to:",
        output,
        next_step,
    ])


def add_glow(sc):
    """Glow (Glare/Bloom) in the compositor - works in Blender 4.x and 5.x."""
    try:
        if hasattr(sc, "compositing_node_group"):          # Blender 5.x
            ng = bpy.data.node_groups.new("glow", "CompositorNodeTree")
            ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            sc.compositing_node_group = ng
            out = ng.nodes.new("NodeGroupOutput")
        else:                                              # Blender 4.x
            sc.use_nodes = True
            ng = sc.node_tree
            ng.nodes.clear()
            out = ng.nodes.new("CompositorNodeComposite")
        rl = ng.nodes.new("CompositorNodeRLayers")
        gl = ng.nodes.new("CompositorNodeGlare")
        if "Type" in gl.inputs:                            # 5.x: parameters are inputs
            gl.inputs["Type"].default_value = "Bloom"
            for k, v in (("Threshold", 1.0), ("Strength", 0.8), ("Size", 0.75)):
                if k in gl.inputs: gl.inputs[k].default_value = v
        else:
            gl.glare_type = "BLOOM"; gl.threshold = 1.0; gl.size = 7
        ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
        ng.links.new(gl.outputs["Image"], out.inputs[0])
    except Exception as ex:
        print("Glow skipped:", ex)


def run(audio_path):
    try:
        build(audio_path)
    except SetupError as e:
        show_message("Equalizer: fix this first", list(e.args), icon="ERROR")
        if bpy.app.background:
            sys.exit(1)


class EQ_OT_pick_song(bpy.types.Operator):
    """Pick the song for the equalizer"""
    bl_idname = "equalizer.pick_song"
    bl_label = "Use this song"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default=AUDIO_FILTER, options={"HIDDEN"})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _context):
        run(self.filepath)
        return {"FINISHED"}


if AUDIO_PATH.strip() or bpy.app.background:
    run(AUDIO_PATH)
else:                                        # no path -> let the user pick the song
    if hasattr(bpy.types, EQ_OT_pick_song.__name__):
        bpy.utils.unregister_class(getattr(bpy.types, EQ_OT_pick_song.__name__))
    bpy.utils.register_class(EQ_OT_pick_song)
    bpy.ops.equalizer.pick_song("INVOKE_DEFAULT")
