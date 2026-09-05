"""WebRTC bridge: stream VM JPEG frames to the browser as a video track."""
import asyncio
import io
import logging

import av
import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from PIL import Image

from . import config
from .vm_control import FrameStream

log = logging.getLogger("vphone.webrtc")

# Track all active peer connections so we can close them (e.g. on VM stop).
_PCS: set[RTCPeerConnection] = set()


class VMVideoTrack(VideoStreamTrack):
    """Reads JPEG frames from a VM stream socket and serves them to aiortc."""

    def __init__(self, socket_path: str):
        super().__init__()
        self._stream = FrameStream(
            socket_path, fps=config.STREAM_FPS, scale=config.STREAM_SCALE, quality=config.STREAM_QUALITY
        )
        self._latest: np.ndarray | None = None
        self._closed = False
        self._reader_task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        await self._stream.open()
        self._reader_task = asyncio.ensure_future(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                jpeg = await self._stream.read_frame()
                img = Image.open(io.BytesIO(jpeg)).convert("RGB")
                arr = np.asarray(img)
                # H.264 (libx264) and VP8 require even width/height; the phone
                # panel downscales to an odd width (e.g. 645), so crop to even.
                h, w = arr.shape[:2]
                arr = arr[: h - (h % 2), : w - (w % 2)]
                self._latest = np.ascontiguousarray(arr)
                self._ready.set()
        except (asyncio.IncompleteReadError, OSError, asyncio.CancelledError):
            pass
        except Exception:  # decode errors etc.
            log.exception("frame reader stopped")

    async def recv(self) -> av.VideoFrame:
        # Pace output via the base-class clock (~30fps) and serve the latest frame.
        pts, time_base = await self.next_timestamp()
        if self._latest is None:
            await self._ready.wait()
        frame = av.VideoFrame.from_ndarray(self._latest, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame

    async def close(self) -> None:
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
        await self._stream.close()


async def create_offer_answer(socket_path: str, offer_sdp: str, offer_type: str) -> dict:
    """Negotiate a peer connection that streams the given VM socket."""
    pc = RTCPeerConnection()
    _PCS.add(pc)
    track = VMVideoTrack(socket_path)

    @pc.on("connectionstatechange")
    async def on_state_change():
        log.info("pc state: %s", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await _cleanup(pc, track)

    try:
        await track.start()
    except (OSError, asyncio.TimeoutError) as e:
        await _cleanup(pc, track)
        raise RuntimeError(f"failed to open VM stream: {e}")

    pc.addTrack(track)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def _cleanup(pc: RTCPeerConnection, track: VMVideoTrack) -> None:
    try:
        await track.close()
    except Exception:
        pass
    if pc in _PCS:
        _PCS.discard(pc)
        await pc.close()


async def close_all() -> None:
    for pc in list(_PCS):
        await pc.close()
    _PCS.clear()
