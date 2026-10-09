# SPDX-License-Identifier: MIT
"""Receive compatibility for muse-cli 0.3.2, loaded only in its child processes."""

import os
import sys
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

    # Current Muse web client adds Run now; 0.3.2's bundled route table predates it.
    gateway.ROUTES.setdefault(
        "tasks.run", {"method": "tasks.run", "http": "POST", "path": "/tasks/{job_id}/run"}
    )

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

    from muse_cli import cli

    plain_watch = cli.cmd_watch

    # 0.3.2's watch sends capabilities as {} and no session, so it never sees a
    # side chat's live status, and prints nothing until it ends. With
    # BLINDPILOT_MUSEAI_WATCH set to a chat id, subscribe the way the web
    # client does and stream each event as one JSON line while the agent works.
    # The web client's Activity panel is a second subscription on the same
    # connection: `activity.updated` carries each task and every tool call in
    # it (the command run, the file written) as it happens.
    def watch(args):
        session = os.environ.get("BLINDPILOT_MUSEAI_WATCH")
        if not session:
            return plain_watch(args)
        gw = cli.connect(cli.load_config())
        try:
            chat = gw._open(
                "chat.subscribe",
                body={
                    "after_stream_seq": 0,
                    "after_chat_event_seq": 0,
                    "capabilities": ["chat_cancel", "delta_stream"],
                    "session_id": session,
                },
            )
            streams = {chat}
            try:
                streams.add(
                    gw._open("activity.subscribe", body={"capabilities": ["ACTIVITY_FEED_GOALS"]})
                )
            except Exception:
                pass
            bufs = {}
            while True:
                frame = gw._read_frame()
                if frame.stream_id not in streams:
                    continue
                kind = frame.WhichOneof("kind")
                if kind == "reset":
                    if frame.stream_id == chat:
                        return None
                    streams.discard(frame.stream_id)
                    continue
                part = frame.response if kind == "response" else frame.body_chunk
                buf = bufs.get(frame.stream_id, b"")
                buf += bytes(part.body if kind == "response" else part.data)
                *lines, bufs[frame.stream_id] = buf.split(b"\n")
                for line in lines:
                    if line.strip():
                        sys.stdout.write(line.decode("utf-8", "replace") + "\n")
                sys.stdout.flush()
                if part.end_body and frame.stream_id == chat:
                    return None
        finally:
            gw.close()

    cli.cmd_watch = watch


install()
