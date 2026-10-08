# SPDX-License-Identifier: MIT
"""Receive compatibility for muse-cli 0.3.2, loaded only in its child processes."""

from importlib.metadata import PackageNotFoundError, version


def assemble(groups, frame):
    if frame.total_chunks <= 1:
        return frame.payload
    if not frame.chunk_id or not 0 <= frame.chunk_index < frame.total_chunks <= 1024:
        raise ValueError("Invalid muse.ai response fragment metadata")
    count, parts = groups.setdefault(frame.chunk_id, (frame.total_chunks, {}))
    if count != frame.total_chunks or (
        frame.chunk_index in parts and parts[frame.chunk_index] != frame.payload
    ):
        raise ValueError("Conflicting muse.ai response fragments")
    parts[frame.chunk_index] = frame.payload
    if (
        len(groups) > 16
        or sum(len(p) for _count, chunks in groups.values() for p in chunks.values())
        > 64 * 1024 * 1024
    ):
        groups.clear()
        raise ValueError("muse.ai response exceeds the receive limit")
    if len(parts) != count:
        return None
    del groups[frame.chunk_id]
    return b"".join(parts[i] for i in range(count))


def install():
    try:
        if version("muse-cli") != "0.3.2":
            return
        from muse_cli import gateway
    except (ImportError, PackageNotFoundError):
        return

    # 0.3.2 decodes partial protobuf messages; remove when upstream reassembles chunks.
    def read_frame(self):
        if not hasattr(self, "_blindpilot_chunks"):
            self._blindpilot_chunks = {}
        with self._recv_lock:
            while True:
                data, _flags = self.ws.recv()
                frame = gateway.NoiseTransportFrame()
                frame.ParseFromString(bytes(self.noise.decrypt(bytes(data))))
                payload = assemble(self._blindpilot_chunks, frame)
                if payload is not None:
                    response = gateway.ServiceResponse()
                    response.ParseFromString(payload)
                    service_frame = gateway.ServiceFrame()
                    service_frame.ParseFromString(response.payload)
                    return service_frame

    gateway.Gateway._read_frame = read_frame


install()
