from __future__ import annotations

import asyncio
import typing as t

from . import AsyncConnection, AsyncProcess
from ..storage import AsyncResourceReader, AsyncResourceWriter

import asyncssh


class SSHProcess(AsyncProcess):

    def __init__(
            self,
            done_event: asyncio.Event,
            transport: asyncssh.SSHSubprocessTransport,
            proto: SSHStdio,
    ) -> None:
        self._done_event = done_event
        self._transport = transport
        self._proto = proto

    @property
    def stdin(self) -> AsyncResourceWriter:
        return StdioWriter(self._transport.get_pipe_transport(0))

    @property
    def stdout(self) -> AsyncResourceReader:
        return self._proto.stdout

    @property
    def stderr(self) -> AsyncResourceReader:
        return self._proto.stderr

    @property
    def rc(self) -> t.Optional[int]:
        return self._transport.get_returncode()

    async def wait_for_exit(self) -> int:
        await self._done_event.wait()
        rc = self.rc
        if rc is None:
            rc = -1

        return rc


class SSHConnection(AsyncConnection):
    plugin_options = {
        'inventory_hostname': ['inventory_hostname'],
        'host': ['ansible_hostname'],
        'remote_user': ['ansible_user'],
        'password': ['ansible_password'],
    }

    BUFFER_SIZE = 4096

    def __init__(self) -> None:
        super().__init__()
        self._connection: t.Optional[asyncssh.SSHClientConnection] = None

    async def connect(self) -> None:
        if not self._connection:
            self._connection = await asyncssh.connect(
                host=self.get_option('host'),
                username=self.get_option('remote_user', None),
                password=self.get_option('password', None),
            )

    async def close(self) -> None:
        if self._connection:
            self._connection.close()
            await self._connection.wait_closed()
            self._connection = None

    async def streaming_exec_command(
            self,
            cmd: str,
    ) -> AsyncProcess:
        return await self._streaming_exec_command(cmd, require_tty=False)

    async def streaming_exec_command_with_tty(
            self,
            cmd: str,
    ) -> AsyncProcess:
        return await self._streaming_exec_command(cmd, require_tty=True)

    async def _streaming_exec_command(
            self,
            cmd: str,
            *,
            require_tty: bool,
    ) -> AsyncProcess:
        process_done = asyncio.Event()

        subprocess_kwargs = {}
        if require_tty:
            subprocess_kwargs['term_type'] = 'dumb'
            subprocess_kwargs['request_pty'] = 'force'

        transport: asyncssh.SSHSubprocessTransport
        proto: SSHStdio
        transport, proto = await self._connection.create_subprocess(
            lambda: SSHStdio(process_done),
            cmd,
            encoding=None,
            **subprocess_kwargs,
        )

        return SSHProcess(process_done, transport, proto)

    async def put_file(self, src: AsyncResourceReader, dst: str) -> None:
        async with self._connection.start_sftp_client() as sftp:
            # FIXME: set remote file attrs, etc
            async with sftp.open(dst, pflags_or_mode='wb') as remote_file:
                while buf := await src.read(self.BUFFER_SIZE):
                    await remote_file.write(buf)

    async def fetch_file(self, src: str, dst: AsyncResourceWriter) -> None:
        async with self._connection.start_sftp_client() as sftp:
            # FIXME: set local file attrs, etc
            async with sftp.open(src, pflags_or_mode='rb') as remote_file:
                while buf := await remote_file.read(self.BUFFER_SIZE):
                    await dst.write(buf)


class SSHStdio(asyncssh.SSHSubprocessProtocol):

    def __init__(
            self,
            exited_event: asyncio.Event,
    ) -> None:
        self.stdout = StdioReader()
        self.stderr = StdioReader()
        self.exited_event = exited_event

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        stream = self.stdout if fd == 1 else self.stderr
        stream.data_queue.put_nowait(data)

    def pipe_connection_lost(self, fd: int, exc: t.Optional[Exception]) -> None:
        # FIXME: Handle exc not being None
        stream = self.stdout if fd == 1 else self.stderr
        stream.done = True
        stream.data_queue.put_nowait(None)

    def process_exited(self) -> None:
        self.exited_event.set()


class StdioReader(AsyncResourceReader):

    def __init__(self) -> None:
        self.data_queue = asyncio.Queue()
        self.done = False

    async def read(self, n=-1) -> bytes:
        if self.done:
            return None
        else:
            return await self.data_queue.get()


class StdioWriter(AsyncResourceWriter):

    def __init__(
            self,
            pipe: asyncio.StreamWriter,
    ) -> None:
        self.pipe = pipe

    async def write(self, data: bytes) -> None:
        self.pipe.write(data)
        # FIXME: asyncssh doesn't seem to implement await drain()

    async def write_eof(self) -> None:
        self.pipe.write_eof()
