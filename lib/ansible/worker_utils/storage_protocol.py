from __future__ import annotations

import asyncio
import typing as t

# FIXME: figure out a better name for this


@t.runtime_checkable
class AsyncResourceReader(t.Protocol):
    async def __aenter__(self) -> AsyncResourceReader:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # empty bytes == EOF
    async def read(self, n=-1) -> bytes:
        pass

    async def close(self) -> None:
        pass


class AsyncStreamReaderAdapter(AsyncResourceReader):
    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader

    async def read(self, n=-1) -> bytes:
        return await self._reader.read(n)

    async def close(self) -> None:
        # FIXME: do we need to do something here?
        pass


@t.runtime_checkable
class AsyncResourceWriter(t.Protocol):
    async def __aenter__(self) -> AsyncResourceWriter:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def write(self, data: bytes) -> None:
        pass

    async def write_eof(self) -> None:
        pass

    async def close(self) -> None:
        pass


class AsyncStreamWriterAdapter(AsyncResourceWriter):
    def __init__(self, writer: asyncio.StreamWriter):
        if not writer:
            raise ValueError('writer must be provided')
        self._writer = writer

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def write_eof(self) -> None:
        self._writer.write_eof()
        await self._writer.drain()

    async def close(self) -> None:
        self._writer.close()
        await self._writer.drain()
