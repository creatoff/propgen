"""Хай-поли генератора → сплошное тело → лоу-поли (venv проекта):
.venv/Scripts/python tools/solidify.py hi.glb out.ply --tris 1500 [--res 256] [--slice-fill 1]

Сетка TRELLIS/Pixal3D после RemeshMesh — тонкая двойная оболочка с дырами и внутренними пузырями. Декимация такой
сетки прорастает стенками друг в друга. Поэтому: воксели оболочки → заливка всего, что не достижимо снаружи
(+ заливка горизонтальных срезов, закрывает предметы без дна) → marching cubes → одна замкнутая поверхность →
квадрик-декимация MeshLab с сохранением топологии. Выход — PLY в осях Blender (Z вверх).
"""
import argparse
import os
import tempfile

import numpy as np
import pymeshlab
import trimesh
from scipy import ndimage
from scipy.spatial import cKDTree
from skimage import measure

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("dst")
ap.add_argument("--tris", type=int, default=1500)
ap.add_argument("--res", type=int, default=256, help="вокселей по большей стороне")
ap.add_argument("--slice-fill", type=int, default=1, help="заливать горизонтальные срезы (предметы без дна)")
ap.add_argument("--close", type=int, default=1, help="морфологическое закрытие щелей, вокселей")
ap.add_argument("--dense", default="", help="сохранить и плотное сглаженное тело — источник нормалей и AO для запекания")
ap.add_argument("--offset", type=float, default=0.75, help="t: насколько поверхность снаружи от оболочки, вокселей")
ap.add_argument("--sigma", type=float, default=0.6, help="размытие поля расстояний, вокселей")
ap.add_argument("--dense-smooth", type=int, default=20, help="проходы Taubin для копии-источника нормалей")
a = ap.parse_args()

mesh = trimesh.load(a.src, force="mesh")
lo, hi = mesh.bounds
pitch = (hi - lo).max() / a.res
# точки по поверхности гуще шага сетки → занятые воксели оболочки
n_pts = int(mesh.area / pitch ** 2 * 12)
pts, _ = trimesh.sample.sample_surface(mesh, n_pts)
pts = np.vstack([pts, mesh.vertices])
pad = a.close + 3
idx = np.floor((pts - lo) / pitch).astype(int) + pad
shape = np.ceil((hi - lo) / pitch).astype(int) + 2 * pad + 1
shell = np.zeros(shape, bool)
shell[idx[:, 0], idx[:, 1], idx[:, 2]] = True
shell = ndimage.binary_dilation(shell, iterations=a.close)

solid = ndimage.binary_fill_holes(shell)
if a.slice_fill:
    up = 1  # glTF: Y вверх
    for i in range(solid.shape[up]):
        sl = [slice(None)] * 3
        sl[up] = i
        solid[tuple(sl)] |= ndimage.binary_fill_holes(shell[tuple(sl)])
solid = ndimage.binary_erosion(solid, iterations=a.close)
# крошки — вон, оставляем связные куски от 1% крупнейшего
lab, n = ndimage.label(solid)
if n > 1:
    sizes = ndimage.sum(solid, lab, range(1, n + 1))
    solid = np.isin(lab, 1 + np.nonzero(sizes >= sizes.max() * 0.01)[0])

# Поверхность — по гладкому полю расстояний, а не по бинарной маске: marching cubes по маске даёт террасы, которые на
# пологих к сетке участках растягиваются в широкие кольца («годичные слои») и проступают в normal map.
# Поверхность — уровень «на расстоянии t снаружи от оболочки»: поле = udf − t, где udf — расстояние до точек исходной
# сетки (KD-дерево, точность меньше вокселя). Оно непрерывно, ступеням маски взяться неоткуда. Маска нужна только там,
# где оболочки нет: внутренние полости (udf > t, но внутри) и закрытое дно — там поле отрицательное.
edt = (ndimage.distance_transform_edt(solid) - ndimage.distance_transform_edt(~solid)).astype(np.float32)
field = np.where(solid, -4.0, 4.0).astype(np.float32)
band = np.abs(edt) <= 4
bi = np.argwhere(band)
d, _ = cKDTree(pts).query((bi - pad + 0.5) * pitch + lo, workers=-1)
f = d / pitch - a.offset
f = np.where(solid[band] & (f > 0), -1.0, f)  # дальше t, но внутри тела — полость или закрытое дно
field[band] = f
field = -field  # внутри положительно, как у маски
field = ndimage.gaussian_filter(field, sigma=a.sigma)
verts, faces, _, _ = measure.marching_cubes(field, 0.0)
verts = (verts - pad + 0.5) * pitch + lo
verts = np.c_[verts[:, 0], -verts[:, 2], verts[:, 1]]  # glTF (Y вверх) → Blender (Z вверх)
faces = faces[:, ::-1]  # смена осей с отражением меняет обход — возвращаем нормали наружу

ms = pymeshlab.MeshSet()
ms.add_mesh(pymeshlab.Mesh(verts, faces))
ms.apply_coord_taubin_smoothing(stepsmoothnum=10)  # снять «лесенку» вокселей
dense = ms.current_mesh().face_number()
if a.dense:
    # Сетка генератора несёт шаг его воксельной решётки (полосы) и дрожь ремеша — normal map с неё переносит этот
    # шум на гладкие грани лоу-поли. Это тело гладкое, но держит форму деталей крупнее ~1% размера.
    # Копия, сглаженная сильнее: ступени вокселей иначе проступают в normal map кольцами («годичные слои»).
    m = ms.current_mesh()
    ms.add_mesh(pymeshlab.Mesh(m.vertex_matrix(), m.face_matrix()))
    ms.apply_coord_taubin_smoothing(stepsmoothnum=a.dense_smooth)
    ms.save_current_mesh(a.dense, save_vertex_normal=False, save_vertex_color=False)
    ms.set_current_mesh(0)
for t in (a.tris * 8, a.tris * 2, a.tris):
    ms.meshing_decimation_quadric_edge_collapse(targetfacenum=t, qualitythr=0.5, preserveboundary=True,
                                                preservenormal=True, preservetopology=True, optimalplacement=True,
                                                planarquadric=True, planarweight=0.002, autoclean=True)
if ms.current_mesh().face_number() > a.tris * 1.3:
    # тело уже сплошное, двойных стенок нет — если шумовые туннели держат бюджет, топологию отпускаем
    ms.meshing_decimation_quadric_edge_collapse(targetfacenum=a.tris, qualitythr=0.5, preserveboundary=True,
                                                preservenormal=True, preservetopology=False, optimalplacement=True,
                                                planarquadric=True, planarweight=0.002, autoclean=True)
ms.meshing_repair_non_manifold_edges()
try:
    ms.meshing_re_orient_faces_coherently()
except pymeshlab.PyMeshLabException:
    pass  # нормали всё равно выравнивает Blender
ms.save_current_mesh(a.dst, save_vertex_normal=False, save_vertex_color=False)
t = ms.get_topological_measures()
print(f"SOLID grid={tuple(shape)} dense={dense} lo={ms.current_mesh().face_number()} "
      f"components={t['connected_components_number']} genus={t['genus']}")
