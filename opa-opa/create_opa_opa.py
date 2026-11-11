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
BODY_RAD = 1.0  # Y 方向の半径
BODY_FLAT = 0.86  # Z 方向のつぶし具合
TAPER = 0.22  # 機首側をどれだけ細くするか
SPLIT_X = -0.3  # 前部と赤い後部キャップの境目 (単位球上の X)
RIM_H = 0.2  # キャノピー下の黄色い縁の高さ (単位球上の Z)
RIM_BACK = 0.2  # キャノピー後ろの黄色い縁の幅 (単位球上の X)

# メタボールは指定半径より小さく見えるので、見た目の半径から逆算する係数
META_VISIBLE = 0.575


def egg_point(u, scale=1.0):
    """単位球上の方向ベクトル u を卵型ボディ表面の座標へ写す。"""
    t = (1.0 - TAPER * u.x) * BODY_RAD * scale
    return Vector((u.x * BODY_LEN * scale, u.y * t, u.z * t * BODY_FLAT))


def make_material(name, color, roughness=0.3):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = (*color, 1.0)
    return mat


def finish_mesh(name, mesh, materials, parent):
    mesh.name = name
    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    for m in materials:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def evaluated_mesh(data, name):
    """カーブやメタボールを一時オブジェクト経由でメッシュ化する。"""
    tmp = bpy.data.objects.new(name + "Tmp", data)
    bpy.context.collection.objects.link(tmp)
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(tmp.evaluated_get(deps))
    bpy.data.objects.remove(tmp)
    return mesh


def tube(name, points, radius, mat, parent, cyclic=False):
    """点列に沿ったチューブ (黄色の縁取り用)。"""
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
    mesh = evaluated_mesh(curve, name)
    bpy.data.curves.remove(curve)
    return finish_mesh(name, mesh, [mat], parent)


def blob(name, balls, mat, parent, matrix=None):
    """(中心, 見た目の半径) の球をなめらかに融合させた有機的な形状。"""
    mb = bpy.data.metaballs.new(name + "Meta")
    mb.resolution = 0.03
    mb.render_resolution = 0.03
    mb.threshold = 0.6
    for co, radius in balls:
        el = mb.elements.new(type="BALL")
        el.co = co
        el.radius = radius / META_VISIBLE
    mesh = evaluated_mesh(mb, name)
    bpy.data.metaballs.remove(mb)
    if matrix is not None:
        mesh.transform(matrix)
    return finish_mesh(name, mesh, [mat], parent)


def chain(start, end, radius, spacing=0.1, end_radius=None):
    """start から end まで球を並べる。end_radius を指定すると先細り/先太りになる。"""
    start, end = Vector(start), Vector(end)
    n = max(2, round((end - start).length / spacing) + 1)
    end_radius = radius if end_radius is None else end_radius
    return [
        (start.lerp(end, i / (n - 1)), radius + (end_radius - radius) * i / (n - 1))
        for i in range(n)
    ]


def build_body(mats, parent):
    """ドット絵の配色に合わせて塗り分けた卵型ボディ。

    後部は上下とも赤いキャップ。前部は上が青いキャノピー (下と後ろに黄色い縁)、下が緑。
    """
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=48, radius=1.0)
    # 球の極を X 軸 (機首/機尾) に向ける
    for v in bm.verts:
        v.co = Vector((v.co.z, v.co.y, -v.co.x))
    # 色の境目でメッシュを切っておき、境界線をきれいに出す
    for co, no in (
        ((SPLIT_X, 0, 0), (1, 0, 0)),
        ((SPLIT_X + RIM_BACK, 0, 0), (1, 0, 0)),
        ((0, 0, RIM_H), (0, 0, 1)),
    ):
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=co, plane_no=no)
    for f in bm.faces:
        c = f.calc_center_median()
        if c.x < SPLIT_X:
            f.material_index = 1  # 赤
        elif c.z < 0.0:
            f.material_index = 2  # 緑
        elif c.z < RIM_H or c.x < SPLIT_X + RIM_BACK:
            f.material_index = 3  # 黄
        else:
            f.material_index = 0  # 青
    for v in bm.verts:
        v.co = egg_point(v.co.normalized())
    mesh = bpy.data.meshes.new("Body")
    bm.to_mesh(mesh)
    bm.free()
    return finish_mesh("Body", mesh, [mats["blue"], mats["red"], mats["green"], mats["rim"]], parent)


def build_trim(mats, parent):
    n = 96
    # 上下を分ける水平の紺のライン
    ring = [
        egg_point(Vector((math.cos(a), math.sin(a), 0.0)), 1.005)
        for a in (2 * math.pi * i / n for i in range(n))
    ]
    tube("TrimEquator", ring, 0.035, mats["navy"], parent, cyclic=True)
    # 前部と赤い後部キャップを分ける紺のライン
    s = math.sqrt(1.0 - SPLIT_X**2)
    divider = [
        egg_point(Vector((SPLIT_X, s * math.cos(a), s * math.sin(a))), 1.005)
        for a in (2 * math.pi * i / n for i in range(n))
    ]
    tube("TrimDivider", divider, 0.035, mats["navy"], parent, cyclic=True)


def build_wing(side, mats, parent):
    """side: +1 = 左舷(+Y), -1 = 右舷(-Y)。

    翼のローカル座標 (XZ 平面、根元が原点、羽先が -X) で、ふくらんだ根元から
    丸い羽先 4 枚が重なって伸びる形を作り、薄くつぶしてボディ側面へ取り付ける。
    """
    tag = "L" if side > 0 else "R"
    balls = [(Vector((-0.12, 0, 0.02)), 0.30), (Vector((0.1, 0, 0.0)), 0.22)]
    # (上向き角度[deg], 長さ, 根元の太さ, 羽先の太さ): 上の羽ほど長い
    feathers = [(20, 1.45, 0.15, 0.17), (6, 1.28, 0.14, 0.16), (-8, 1.05, 0.13, 0.15), (-22, 0.78, 0.12, 0.14)]
    for i, (deg, length, r0, r1) in enumerate(feathers):
        a = math.radians(deg)
        start = Vector((-0.1, 0, 0.14 - 0.09 * i))
        end = start + Vector((-math.cos(a), 0, math.sin(a))) * length
        balls += chain(start, end, r0, end_radius=r1)
    root = egg_point(Vector((-0.62, side * 0.78, 0.06)).normalized(), 0.97)
    matrix = (
        Matrix.Translation(root)
        @ Matrix.Rotation(math.radians(-side * 24), 4, "Z")  # 羽先を外へ開く
        @ Matrix.Rotation(math.radians(side * 12), 4, "X")  # 少し上へ反らせる
        @ Matrix.Diagonal((0.72, 0.36, 0.72, 1.0))  # 小ぶりにして薄くつぶす
    )
    blob(f"Wing.{tag}", balls, mats["white"], parent, matrix)


def build_leg(side, mats, parent):
    """脚と足がひと続きになった丸いブーツ。"""
    tag = "L" if side > 0 else "R"
    x, y = -0.15, side * 0.36
    balls = chain((x, y, -0.75), (x, y, -1.72), 0.15)  # 脚
    balls += chain((x - 0.02, y, -1.78), (x + 0.5, y, -1.80), 0.20, end_radius=0.21)  # 足
    return blob(f"Leg.{tag}", balls, mats["yellow"], parent)


def build_opa_opa():
    mats = {
        "blue": make_material("CanopyBlue", (0.12, 0.45, 0.85), roughness=0.1),
        "red": make_material("BodyRed", (0.80, 0.03, 0.03), roughness=0.3),
        "green": make_material("BodyGreen", (0.15, 0.62, 0.05), roughness=0.3),
        "rim": make_material("RimYellow", (0.95, 0.80, 0.05), roughness=0.3),
        "navy": make_material("LineNavy", (0.01, 0.01, 0.25), roughness=0.3),
        "yellow": make_material("LegYellow", (0.80, 0.68, 0.10), roughness=0.3),
        "white": make_material("FeatherWhite", (0.85, 0.85, 0.83), roughness=0.4),
    }
    root = bpy.data.objects.new("OpaOpa", None)
    bpy.context.collection.objects.link(root)
    build_body(mats, root)
    build_trim(mats, root)
    legs = []
    for side in (1, -1):
        build_wing(side, mats, root)
        legs.append(build_leg(side, mats, root))
    # 足の裏が Z=0 の床に着くように全体を持ち上げる
    root.location.z = -min(v.co.z for leg in legs for v in leg.data.vertices)
    return root


def look_at(obj, loc, target):
    obj.location = loc
    obj.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def setup_scene():
    scene = bpy.context.scene

    world = bpy.data.worlds.new("Studio")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    scene.world = world

    floor_mesh = bpy.data.meshes.new("Floor")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1000.0)
    bm.to_mesh(floor_mesh)
    bm.free()
    floor_mesh.materials.append(make_material("Floor", (0.22, 0.24, 0.30), roughness=0.55))
    scene.collection.objects.link(bpy.data.objects.new("Floor", floor_mesh))

    def light(name, loc, energy, size):
        # エリアライトだと裏側の床に影の境界線が出るので、全方向に光るポイントライトを使う
        data = bpy.data.lights.new(name, "POINT")
        data.energy = energy
        data.shadow_soft_size = size
        obj = bpy.data.objects.new(name, data)
        obj.location = loc
        scene.collection.objects.link(obj)

    light("Key", (5.0, 4.0, 7.0), 2600, 2.0)
    light("Fill", (6.0, -5.0, 3.0), 700, 2.5)
    light("Rim", (-6.0, 3.0, 5.0), 1300, 1.5)

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 96
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 960
    scene.view_settings.view_transform = "Standard"
    return cam


CAM_TARGET = (-0.4, 0.0, 1.7)
CAM_FRONT = (3.6, 8.8, 2.3)  # ドット絵に近い、機首が左の横寄りの構図
CAM_REAR = (-7.0, 5.5, 3.6)  # 斜め後ろから (翼の形が分かる)


def render(cam, loc, filename):
    look_at(cam, loc, CAM_TARGET)
    bpy.context.scene.render.filepath = os.path.join(OUT_DIR, filename)
    bpy.ops.render.render(write_still=True)


def main():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build_opa_opa()
    cam = setup_scene()
    look_at(cam, CAM_FRONT, CAM_TARGET)
    if "--no-render" in args:
        return
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT_DIR, "opa_opa.blend"))
    bpy.ops.export_scene.gltf(filepath=os.path.join(OUT_DIR, "opa_opa.glb"))
    render(cam, CAM_FRONT, "opa_opa_front.png")
    render(cam, CAM_REAR, "opa_opa_rear.png")


if __name__ == "__main__":
    main()
