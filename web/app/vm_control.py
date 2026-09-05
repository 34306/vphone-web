"""Async client for the Swift VPhoneStreamServer unix socket.

Two usages:
  * one-shot JSON commands (touch / key / type / install / ping)
  * a persistent frame stream (length-prefixed JPEG), used by webrtc.py
"""
import asyncio
import json
import struct

CONNECT_TIMEOUT = 5.0
# Reply lines can be large (e.g. app_list with ~570 apps, file_get base64),
# so raise the StreamReader line-buffer well above the 64 KB default.
READ_LIMIT = 32 * 1024 * 1024


async def send_command(socket_path: str, command: dict, timeout: float = 200.0) -> dict:
    """Open the socket, send one JSON command, read one JSON reply, close."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path, limit=READ_LIMIT), timeout=CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as e:
        return {"ok": False, "error": f"connect failed: {e}"}

    try:
        writer.write((json.dumps(command) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            return {"ok": False, "error": "no response"}
        return json.loads(line.decode())
    except (asyncio.TimeoutError, json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": f"{e}"}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


class CommandChannel:
    """Persistent connection for low-latency input commands (touch/key/type)."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.socket_path, limit=READ_LIMIT), timeout=CONNECT_TIMEOUT
        )

    async def send(self, command: dict, read_reply: bool = True) -> dict | None:
        assert self._writer is not None and self._reader is not None
        async with self._lock:
            self._writer.write((json.dumps(command) + "\n").encode())
            await self._writer.drain()
            if not read_reply:
                return None
            line = await asyncio.wait_for(self._reader.readline(), timeout=30)
            return json.loads(line.decode()) if line else None

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None


class FrameStream:
    """Persistent connection that yields JPEG frames from the VM."""

    def __init__(self, socket_path: str, fps: int, scale: int, quality: float):
        self.socket_path = socket_path
        self.fps = fps
        self.scale = scale
        self.quality = quality
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self.socket_path, limit=READ_LIMIT), timeout=CONNECT_TIMEOUT
        )
        cmd = {"t": "stream", "fps": self.fps, "scale": self.scale, "quality": self.quality}
        self._writer.write((json.dumps(cmd) + "\n").encode())
        await self._writer.drain()

    async def read_frame(self) -> bytes:
        """Read one length-prefixed JPEG frame. Raises on EOF/error."""
        assert self._reader is not None
        header = await self._reader.readexactly(4)
        (length,) = struct.unpack(">I", header)
        return await self._reader.readexactly(length)

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None
