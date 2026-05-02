"""3D Material Viewer — QOpenGLWidget (proper Qt widget, no native-window overlay).

Renders a UV-sphere with Phong shading + albedo / normal map support.
Mouse-drag to orbit, scroll to zoom.
"""

import ctypes
import math
import numpy as np

from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLShaderProgram, QOpenGLShader,
    QOpenGLTexture, QOpenGLBuffer, QOpenGLVertexArrayObject,
)
from PySide6.QtCore  import Qt, QSize, QPoint
from PySide6.QtGui   import QColor, QMatrix4x4, QVector3D, QSurfaceFormat
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy


# ── Shaders ───────────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;
layout(location = 2) in vec2 aUV;
layout(location = 3) in vec3 aTangent;

uniform mat4 uModel;
uniform mat4 uView;
uniform mat4 uProj;

out vec3  vFragPos;
out vec3  vNormal;
out vec2  vUV;
out mat3  vTBN;

void main() {
    vec4 world = uModel * vec4(aPos, 1.0);
    vFragPos   = world.xyz;
    mat3 normalMat = transpose(inverse(mat3(uModel)));
    vNormal    = normalize(normalMat * aNormal);
    vUV        = aUV;

    vec3 T = normalize(normalMat * aTangent);
    vec3 N = vNormal;
    T = normalize(T - dot(T, N) * N);
    vec3 B = cross(N, T);
    vTBN = mat3(T, B, N);

    gl_Position = uProj * uView * world;
}
"""

_FRAG = """
#version 330 core
in vec3  vFragPos;
in vec3  vNormal;
in vec2  vUV;
in mat3  vTBN;

uniform sampler2D uAlbedo;
uniform sampler2D uNormal;
uniform bool      uHasAlbedo;
uniform bool      uHasNormal;
uniform vec3      uLightDir;   // world space, normalised
uniform vec3      uFillDir;
uniform vec3      uViewPos;

out vec4 fragColor;

void main() {
    // Normal
    vec3 N = vNormal;
    if (uHasNormal) {
        vec3 n = texture(uNormal, vUV).rgb * 2.0 - 1.0;
        N = normalize(vTBN * n);
    }

    // Albedo
    vec3 albedo = uHasAlbedo ? pow(texture(uAlbedo, vUV).rgb, vec3(2.2))
                             : vec3(0.72);

    // Key light (warm)
    float diffK  = max(dot(N, uLightDir), 0.0);
    vec3  keyCol = vec3(1.0, 0.97, 0.88) * 1.6;

    // Fill light (cool)
    float diffF  = max(dot(N, uFillDir), 0.0);
    vec3  fillCol= vec3(0.50, 0.62, 0.90) * 0.5;

    // Specular (Blinn-Phong)
    vec3  V    = normalize(uViewPos - vFragPos);
    vec3  H    = normalize(uLightDir + V);
    float spec = pow(max(dot(N, H), 0.0), 48.0) * 0.4;

    // Hemisphere ambient
    float hemi = 0.5 + 0.5 * dot(N, vec3(0.0, 1.0, 0.0));
    vec3  amb  = mix(vec3(0.04, 0.05, 0.08), vec3(0.18, 0.20, 0.22), hemi)
                 * albedo;

    vec3 color = amb
               + albedo * (diffK * keyCol + diffF * fillCol)
               + vec3(spec);

    // Gamma
    color = pow(clamp(color, 0.0, 1.0), vec3(1.0 / 2.2));
    fragColor = vec4(color, 1.0);
}
"""


# ── Sphere geometry ───────────────────────────────────────────────────────────

def _sphere_buffers(radius: float = 0.55, rings: int = 64, slices: int = 64):
    """Return (vertices float32, indices uint32) for a UV sphere.

    Vertex layout: pos(3) normal(3) uv(2) tangent(3)  →  11 floats / vertex
    """
    verts = []
    for r in range(rings + 1):
        phi = math.pi * r / rings
        sin_phi, cos_phi = math.sin(phi), math.cos(phi)
        for s in range(slices + 1):
            theta = 2.0 * math.pi * s / slices
            sin_t, cos_t = math.sin(theta), math.cos(theta)

            nx = sin_phi * cos_t
            ny = cos_phi
            nz = sin_phi * sin_t

            tx = -sin_t   # ∂pos/∂theta, normalised
            ty = 0.0
            tz =  cos_t

            verts += [
                nx * radius, ny * radius, nz * radius,  # pos
                nx, ny, nz,                              # normal
                s / slices, r / rings,                   # uv
                tx, ty, tz,                              # tangent
            ]

    idx = []
    w = slices + 1
    for r in range(rings):
        for s in range(slices):
            a, b = r * w + s, r * w + s + 1
            c, d = a + w,     b + w
            idx += [a, b, c, b, d, c]

    return (np.array(verts, dtype=np.float32),
            np.array(idx,   dtype=np.uint32))


# ── Viewer widget ─────────────────────────────────────────────────────────────

class MaterialViewer3D(QWidget):
    """Drop-in replacement for the old Qt3D viewer.

    Embeds a QOpenGLWidget — fully respects Qt layout constraints.
    """

    _ROLE_MAP = {
        "albedo": "albedo", "diffuse": "albedo", "diff": "albedo",
        "basecolor": "albedo", "base_color": "albedo", "col": "albedo",
        "normal": "normal", "nrm": "normal", "norm": "normal", "bump": "normal",
        "roughness": "roughness", "rough": "roughness", "rgh": "roughness",
        "metallic": "specular", "metal": "specular", "specular": "specular",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._gl = _SphereGL(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._gl)

        self._mat_obj = None
        self._loaded: list[str] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def load_material(self, mat) -> None:
        self._mat_obj = mat
        self._loaded  = []

        found: dict[str, str] = {}
        for a in mat.maps:
            role = self._ROLE_MAP.get(a.sub_type)
            if role and role not in found and a.path.exists():
                found[role] = str(a.path)
                self._loaded.append(role)

        self._gl.set_textures(
            albedo_path  = found.get("albedo"),
            normal_path  = found.get("normal"),
        )

    def loaded_roles(self) -> list[str]:
        return list(self._loaded)


# ── OpenGL widget ─────────────────────────────────────────────────────────────

class _SphereGL(QOpenGLWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(QSize(50, 50))
        self.setFocusPolicy(Qt.StrongFocus)

        self._prog:    QOpenGLShaderProgram | None = None
        self._vao:     QOpenGLVertexArrayObject | None = None
        self._vbo:     QOpenGLBuffer | None = None
        self._ibo:     QOpenGLBuffer | None = None
        self._n_idx:   int = 0

        self._tex_albedo: QOpenGLTexture | None = None
        self._tex_normal: QOpenGLTexture | None = None
        self._pending:    dict | None = None   # set_textures before initGL

        # Camera state
        self._yaw:   float = 30.0
        self._pitch: float = 20.0
        self._dist:  float = 2.2
        self._last_pos: QPoint | None = None

    # ── Texture loading ───────────────────────────────────────────────────────

    def set_textures(self, albedo_path: str | None, normal_path: str | None):
        if not self.isValid():
            # OpenGL not ready yet — defer
            self._pending = {"albedo": albedo_path, "normal": normal_path}
            return
        self.makeCurrent()
        self._load_texture_slot("albedo", albedo_path)
        self._load_texture_slot("normal", normal_path)
        self.doneCurrent()
        self.update()

    def _load_texture_slot(self, slot: str, path: str | None):
        attr = f"_tex_{slot}"
        old = getattr(self, attr, None)
        if old is not None:
            old.destroy()
        if path:
            tex = QOpenGLTexture(QOpenGLTexture.Target2D)
            tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
            tex.setMagnificationFilter(QOpenGLTexture.Linear)
            tex.setWrapMode(QOpenGLTexture.Repeat)
            tex.setAutoMipMapGenerationEnabled(True)
            from PySide6.QtGui import QImage
            img = QImage(path)
            if not img.isNull():
                tex.setData(img)
                setattr(self, attr, tex)
                return
        setattr(self, attr, None)

    # ── GL lifecycle ──────────────────────────────────────────────────────────

    def initializeGL(self):
        from PySide6.QtGui import QOpenGLContext
        gl = QOpenGLContext.currentContext().functions()
        gl.glEnable(0x0B71)   # GL_DEPTH_TEST
        gl.glEnable(0x809D)   # GL_MULTISAMPLE
        gl.glClearColor(0.07, 0.075, 0.094, 1.0)

        # Shader
        self._prog = QOpenGLShaderProgram(self)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex,   _VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, _FRAG)
        self._prog.link()

        # Geometry
        verts, idx = _sphere_buffers()
        self._n_idx = len(idx)

        self._vao = QOpenGLVertexArrayObject()
        self._vao.create()
        self._vao.bind()

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(verts.tobytes(), verts.nbytes)

        self._ibo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
        self._ibo.create()
        self._ibo.bind()
        self._ibo.allocate(idx.tobytes(), idx.nbytes)

        stride = 11 * 4   # 11 floats × 4 bytes
        self._prog.bind()
        # Use Qt's setAttributeBuffer — avoids PySide6's glVertexAttribPointer ptr=0 rejection
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, 0x1406, 0,  3, stride)  # pos
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, 0x1406, 12, 3, stride)  # normal
        self._prog.enableAttributeArray(2)
        self._prog.setAttributeBuffer(2, 0x1406, 24, 2, stride)  # uv
        self._prog.enableAttributeArray(3)
        self._prog.setAttributeBuffer(3, 0x1406, 32, 3, stride)  # tangent

        self._vao.release()
        self._prog.release()

        # Apply any pending texture request
        if self._pending is not None:
            p = self._pending
            self._pending = None
            self._load_texture_slot("albedo", p.get("albedo"))
            self._load_texture_slot("normal", p.get("normal"))

    def resizeGL(self, w: int, h: int):
        from PySide6.QtGui import QOpenGLContext
        gl = QOpenGLContext.currentContext().functions()
        gl.glViewport(0, 0, w, max(h, 1))

    def paintGL(self):
        from PySide6.QtGui import QOpenGLContext
        gl = QOpenGLContext.currentContext().functions()
        gl.glClear(0x4100)   # GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT

        if self._prog is None or self._vao is None:
            return

        # Matrices
        proj = QMatrix4x4()
        proj.perspective(45.0, max(self.width(), 1) / max(self.height(), 1), 0.05, 50.0)

        yaw_r   = math.radians(self._yaw)
        pitch_r = math.radians(self._pitch)
        eye = QVector3D(
            self._dist * math.cos(pitch_r) * math.sin(yaw_r),
            self._dist * math.sin(pitch_r),
            self._dist * math.cos(pitch_r) * math.cos(yaw_r),
        )
        view = QMatrix4x4()
        view.lookAt(eye, QVector3D(0, 0, 0), QVector3D(0, 1, 0))

        model = QMatrix4x4()   # identity

        # Light directions
        kl = QVector3D(1.2, 2.0, 1.5).normalized()
        fl = QVector3D(-2.5, -0.3, -1.0).normalized()

        # Bind shader + uniforms
        self._prog.bind()
        self._prog.setUniformValue("uModel",    model)
        self._prog.setUniformValue("uView",     view)
        self._prog.setUniformValue("uProj",     proj)
        self._prog.setUniformValue("uLightDir", kl)
        self._prog.setUniformValue("uFillDir",  fl)
        self._prog.setUniformValue("uViewPos",  eye)

        has_albedo = self._tex_albedo is not None
        has_normal = self._tex_normal is not None
        self._prog.setUniformValue("uHasAlbedo", has_albedo)
        self._prog.setUniformValue("uHasNormal", has_normal)

        if has_albedo:
            self._tex_albedo.bind(0)
            self._prog.setUniformValue("uAlbedo", 0)
        if has_normal:
            self._tex_normal.bind(1)
            self._prog.setUniformValue("uNormal", 1)

        # Draw
        self._vao.bind()
        gl.glDrawElements(0x0004, self._n_idx, 0x1405, ctypes.c_void_p(0))  # GL_TRIANGLES, GL_UNSIGNED_INT
        self._vao.release()

        if has_albedo:
            self._tex_albedo.release()
        if has_normal:
            self._tex_normal.release()

        self._prog.release()

    # ── Mouse / wheel ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._last_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._last_pos is not None:
            delta = event.position().toPoint() - self._last_pos
            self._yaw   += delta.x() * 0.5
            self._pitch  = max(-89.0, min(89.0, self._pitch - delta.y() * 0.5))
            self._last_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_pos = None

    def wheelEvent(self, event):
        self._dist = max(1.0, min(6.0, self._dist - event.angleDelta().y() * 0.003))
        self.update()
