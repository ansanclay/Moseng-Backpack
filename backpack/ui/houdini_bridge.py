"""Houdini bridge — send a material to a running Backpack for Houdini session.

Wire protocol: 4-byte big-endian length prefix + UTF-8 JSON body.
The server sends back a single response frame after receiving the payload.
"""

import json
import socket
from pathlib import Path

HOST    = "127.0.0.1"
PORT    = 29700
TIMEOUT = 4.0    # seconds to wait for ACK


# ─────────────────────────────────────────────────────────────────────────────

def is_houdini_available() -> bool:
    """Non-blocking probe — True if Backpack for Houdini is listening."""
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def send_material(
    mat_name: str,
    maps: dict[str, str],
    preview_path: str = "",
) -> dict:
    """Send a material to Backpack for Houdini and return the server's response.

    Parameters
    ----------
    mat_name     Display name, e.g. "Rock_Mossy_01".
    maps         {sub_type: absolute_path_string}
                 Keys: albedo, normal, roughness, metallic, displacement,
                       ao, specular, emissive, opacity, bump, …
    preview_path Optional preview/thumbnail path.

    Returns
    -------
    dict  Server response, e.g. {"status": "received", "node": "Rock_Mossy_01"}

    Raises
    ------
    ConnectionRefusedError  Houdini is not listening.
    TimeoutError            Server did not respond in time.
    OSError                 Any other socket error.
    """
    payload = {
        "version": 1,
        "action" : "import_material",
        "name"   : mat_name,
        "maps"   : maps,
        "preview": preview_path,
    }
    body  = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    frame = len(body).to_bytes(4, "big") + body

    with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as sock:
        sock.sendall(frame)
        # Read back the response frame
        response = _recv_frame(sock)
        return response or {"status": "sent"}


def send_image(
    abs_path: str,
    sub_type: str = "",
    name: str = "",
) -> dict:
    """Send a single texture to the active RS Material Builder in Houdini.

    Houdini will create a redshift::TextureSampler node in whichever
    VOP network is currently open in the Network Editor.

    Returns the server's response dict.
    """
    payload = {
        "version" : 1,
        "action"  : "send_image",
        "path"    : abs_path,
        "sub_type": sub_type,
        "name"    : name or abs_path,
    }
    body  = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    frame = len(body).to_bytes(4, "big") + body

    with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as sock:
        sock.sendall(frame)
        response = _recv_frame(sock)
        return response or {"status": "sent"}


def send_material_from_scan(
    mat_name: str,
    backpack_root: Path,
    maps_by_subtype: dict,      # sub_type -> ScannedAsset | Path | str
    preview_path: str = "",
) -> dict:
    """Convenience wrapper that converts ScannedAsset/Path objects to strings."""
    str_maps: dict[str, str] = {}
    for sub_type, item in maps_by_subtype.items():
        if hasattr(item, "path"):           # ScannedAsset
            str_maps[sub_type] = str(item.path.resolve())
        elif isinstance(item, Path):
            str_maps[sub_type] = str(item.resolve())
        elif isinstance(item, str):
            str_maps[sub_type] = item
    return send_material(mat_name, str_maps, preview_path)


# ── Internal ──────────────────────────────────────────────────────────────────

def _recv_frame(sock: socket.socket) -> dict | None:
    """Read one length-prefixed JSON frame from *sock*. Returns None on error."""
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    length = int.from_bytes(header, "big")
    body   = _recv_exact(sock, length)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf
