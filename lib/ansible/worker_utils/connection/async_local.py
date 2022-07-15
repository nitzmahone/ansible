import asyncio

import aiofiles
import typing as t

from . import AsyncConnection, AsyncProcess
from ..storage_protocol import AsyncResourceReader, AsyncStreamReaderAdapter, AsyncResourceWriter, AsyncStreamWriterAdapter


class LocalProcess(AsyncProcess):
    def __init__(
            self,
            process: asyncio.subprocess.Process,
    ) -> None:
        self._process = process
        self._stdin = AsyncStreamWriterAdapter(process.stdin)
        self._stdout = AsyncStreamReaderAdapter(process.stdout)
        self._stderr = AsyncStreamReaderAdapter(process.stderr)

    @property
    def stdin(self) -> AsyncResourceWriter:
        return self._stdin

    @property
    def stdout(self) -> AsyncResourceReader:
        return self._stdout

    @property
    def stderr(self) -> AsyncResourceReader:
        return self._stderr

    @property
    def rc(self) -> t.Optional[int]:
        return self._process.returncode

    async def wait_for_exit(self) -> int:
        return await self._process.wait()


class LocalConnection(AsyncConnection):
    plugin_options = {}

    async def streaming_exec_command(
            self,
            cmd: str,
    ) -> AsyncProcess:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        return LocalProcess(proc)

    async def exec_command(
        self,
        cmd: str,
        stdin: t.Optional[AsyncResourceReader] = None,
    ) -> tuple[bytes, bytes, int]:
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdin_bytes = await stdin.read() if stdin else None

        stdout_bytes, stderr_bytes = await process.communicate(stdin_bytes)

        return stdout_bytes, stderr_bytes, process.returncode

    async def put_file(self, src: AsyncResourceReader, dst: str) -> None:
        blob = await src.read()

        async with aiofiles.open(dst, 'wb') as dst_file:
            await dst_file.write(blob)

    async def fetch_file(self, src: str, dst: AsyncResourceWriter) -> None:
        async with aiofiles.open(src, 'rb') as src_file:
            blob = await src_file.read()

        await dst.write(blob)
