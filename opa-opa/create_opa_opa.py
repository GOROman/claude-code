"""オパオパ (ファンタジーゾーン) 風の 3D モデルを Blender で生成するスクリプト。

使い方:
    blender --background --python create_opa_opa.py          # .blend / .glb / PNG を出力
    blender --python create_opa_opa.py -- --no-render        # GUI で開いてモデルだけ生成

`pip install bpy` した Python から直接 `python create_opa_opa.py` でも実行できます。
座標系: +X が機首、+Z が上。
"""

import math
import os
import sys

import bpy  # isort: skip  (pip 版では bmesh より先に import する必要がある)
import bmesh
from mathutils import Matrix, Vector

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# 卵型ボディの寸法
BODY_LEN = 1.5  # X 方向の半径
BODY_RAD = 1.0  # Y/Z 方向の半径
TAPER = 0.22  # 機首側をどれだけ細くするか
SPLIT_X = 0.05  # 青(前)と赤(後ろ)の境目 (単位球上の X)


def egg_point(u, scale=1.0):
    """単位球上の方向ベクトル u を卵型ボディ表面の座標へ写す。"""
    t = (1.0 - TAPER * u.x) * BODY_RAD * scale
    return Vector((u.x * BODY_LEN * scale, u.y * t, u.z * t))


def make_material(name, color, roughness=0.3, metallic=0.0, emission=None):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 3.0
    mat.diffuse_color = (*color, 1.0)
    return mat


def mesh_object(name, bm, materials, parent):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    for m in materials:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def ellipsoid(name, loc, scale, mat, parent, rot=None, segments=32):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=segments // 2, radius=1.0)
    m = Matrix.Diagonal((*scale, 1.0))
    if rot is not None:
        m = rot.to_matrix().to_4x4() @ m
    bmesh.ops.transform(bm, matrix=Matrix.Translation(loc) @ m, verts=bm.verts)
    return mesh_object(name, bm, [mat], parent)


def tube(name, points, radius, mat, parent, cyclic=False):
    """点列に沿ったチューブ (黄色の縁取り用)。メッシュに変換して返す。"""
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 6
    curve.use_fill_caps = True
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for p, co in zip(spline.points, points):
        p.co = (*co, 1.0)
    spline.use_cyclic_u = cyclic
    tmp = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(tmp)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(deps))
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(curve)
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def build_body(mats, parent):
    """青(前上)・赤(後ろ上)・緑(下) に塗り分けた卵型ボディ。"""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=40, radius=1.0)
    # 球の極を X 軸 (機首/機尾) に向ける
    for v in bm.verts:
        v.co = Vector((v.co.z, v.co.y, -v.co.x))
    for f in bm.faces:
        c = f.calc_center_median()
        if c.z < 0.0:
            f.material_index = 2  # 緑
        elif c.x > SPLIT_X:
            f.material_index = 0  # 青
        else:
            f.material_index = 1  # 赤
    for v in bm.verts:
        v.co = egg_point(v.co.normalized())
    return mesh_object("Body", bm, [mats["blue"], mats["red"], mats["green"]], parent)


def build_trim(mats, parent):
    n = 96
    # 上下を分ける水平の縁取り
    ring = [
        egg_point(Vector((math.cos(a), math.sin(a), 0.0)), 1.005)
        for a in (2 * math.pi * i / n for i in range(n))
    ]
    tube("TrimEquator", ring, 0.055, mats["yellow"], parent, cyclic=True)
    # 青と赤を分ける縦の縁取り (上半分のアーチ)
    s = math.sqrt(1.0 - SPLIT_X**2)
    arch = [
        egg_point(Vector((SPLIT_X, s * math.cos(a), s * math.sin(a))), 1.005)
        for a in (math.pi * i / (n // 2) for i in range(n // 2 + 1))
    ]
    tube("TrimDivider", arch, 0.045, mats["yellow"], parent)


def build_wing(side, mats, parent):
    """side: +1 = 左舷(+Y), -1 = 右舷(-Y)。羽根を扇状に重ねた翼。"""
    tag = "L" if side > 0 else "R"
    root = Vector((-0.55, side * 0.42, 0.78))
    ellipsoid(f"WingRoot.{tag}", root, (0.36, 0.24, 0.30), mats["orange"], parent)
    # (前傾角[deg], 長さ, 幅): 前の羽根ほど立っていて長い
    feathers = [(-4, 1.35, 0.20), (13, 1.25, 0.19), (31, 1.08, 0.18), (50, 0.86, 0.16), (70, 0.62, 0.14)]
    for i, (deg, length, width) in enumerate(feathers):
        a = math.radians(deg)
        d = Vector((-math.sin(a), side * 0.22, math.cos(a))).normalized()
        rot = d.to_track_quat("X", "Z")
        base = root + Vector((0.0, side * 0.035 * i, 0.05))
        ellipsoid(
            f"Feather{i}.{tag}",
            base + d * (length * 0.5),
            (length * 0.5, 0.06, width),
            mats["white"],
            parent,
            rot=rot,
        )


def build_foot(side, mats, parent):
    tag = "L" if side > 0 else "R"
    y = side * 0.38
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.09, radius2=0.09, depth=0.4)
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.15, y, -1.0)), verts=bm.verts)
    mesh_object(f"Leg.{tag}", bm, [mats["yellow"]], parent)
    ellipsoid(f"Foot.{tag}", Vector((0.28, y, -1.22)), (0.34, 0.2, 0.14), mats["orange"], parent)


def build_thruster(mats, parent):
    rot = Matrix.Rotation(math.radians(90), 4, "Y")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=32, radius1=0.16, radius2=0.26, depth=0.3)
    bmesh.ops.transform(bm, matrix=Matrix.Translation((-1.55, 0, 0)) @ rot.inverted(), verts=bm.verts)
    mesh_object("Thruster", bm, [mats["metal"]], parent)
    ellipsoid("ThrusterGlow", Vector((-1.55, 0, 0)), (0.1, 0.18, 0.18), mats["glow"], parent)


def build_opa_opa():
    mats = {
        "blue": make_material("CanopyBlue", (0.05, 0.25, 0.85), roughness=0.08),
        "red": make_material("BodyRed", (0.85, 0.06, 0.04), roughness=0.25),
        "green": make_material("BodyGreen", (0.04, 0.55, 0.10), roughness=0.25),
        "yellow": make_material("TrimYellow", (1.0, 0.72, 0.05), roughness=0.3),
        "orange": make_material("Orange", (1.0, 0.33, 0.03), roughness=0.3),
        "white": make_material("FeatherWhite", (0.82, 0.84, 0.9), roughness=0.5),
        "metal": make_material("Metal", (0.35, 0.36, 0.4), roughness=0.3, metallic=1.0),
        "glow": make_material("Glow", (1.0, 0.6, 0.1), emission=(1.0, 0.45, 0.05)),
    }
    root = bpy.data.objects.new("OpaOpa", None)
    bpy.context.collection.objects.link(root)
    build_body(mats, root)
    build_trim(mats, root)
    for side in (1, -1):
        build_wing(side, mats, root)
        build_foot(side, mats, root)
    build_thruster(mats, root)
    return root


def setup_scene(root):
    scene = bpy.context.scene
    root.rotation_euler = (0.0, math.radians(22), 0.0)  # 機首を少し下げる

    world = bpy.data.worlds.new("PastelSky")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.42, 0.40, 0.85, 1.0)
    bg.inputs["Strength"].default_value = 0.6
    scene.world = world

    def light(name, kind, loc, energy, size=None):
        data = bpy.data.lights.new(name, kind)
        data.energy = energy
        if size:
            data.size = size
        obj = bpy.data.objects.new(name, data)
        obj.location = loc
        obj.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
        scene.collection.objects.link(obj)

    light("Key", "AREA", (3.0, 5.0, 6.0), 600, size=5)
    light("Fill", "AREA", (-5.0, 4.0, -1.0), 120, size=6)
    light("Rim", "AREA", (-2.0, -6.0, 4.0), 350, size=4)

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 960
    scene.view_settings.view_transform = "Standard"
    return cam


def render(cam, loc, filename):
    target = Vector((-0.1, 0.0, 0.45))
    cam.location = loc
    cam.rotation_euler = (target - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.render.filepath = os.path.join(OUT_DIR, filename)
    bpy.ops.render.render(write_still=True)


def main():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    bpy.ops.wm.read_factory_settings(use_empty=True)
    root = build_opa_opa()
    cam = setup_scene(root)
    cam.location = (2.2, 8.5, 1.6)
    cam.rotation_euler = (Vector((-0.1, 0, 0.45)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    if "--no-render" in args:
        return
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT_DIR, "opa_opa.blend"))
    bpy.ops.export_scene.gltf(filepath=os.path.join(OUT_DIR, "opa_opa.glb"))
    render(cam, (2.2, 8.5, 1.6), "opa_opa_side.png")  # 参考画像に近い構図
    render(cam, (6.5, -5.5, 3.0), "opa_opa_front.png")  # 斜め前から


if __name__ == "__main__":
    main()
