from __future__ import annotations

import contextlib
import enum
import json
import pathlib
import traceback
import sys

import aiofiles  # https://github.com/Tinche/aiofiles
import os
import typing as t

from .storage_protocol import AsyncResourceReader, AsyncResourceWriter
from .exec import ContentDescriptor


@contextlib.asynccontextmanager
async def dynamic_context(key: str) -> t.AsyncIterator[AsyncResourceWriter]:
    temp_key = f'{key}.tmp'
    temp_content_path = _get_file_path(temp_key, FileType.CONTENT)
    final_content_path = _get_file_path(key, FileType.CONTENT)
    lock_path = _get_file_path(key, FileType.LOCK)

    # open fifo
    async with aiofiles.open(os.open(lock_path, os.O_NONBLOCK | os.O_RDWR), 'w') as lock_file:
        try:
            try:
                # FUTURE: is this a good pattern? Maybe we need to push in the blobstore itself or another wrapper object...
                async with await BLOB_STORE.put(temp_key) as ref:
                    yield ref
            except Exception as ex:
                # best-effort cleanup
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temp_content_path)

                error_content = json.dumps(dict(
                    msg=str(ex),
                    traceback=traceback.extract_tb(sys.exc_info()[2]).format(),
                ))
            else:
                os.rename(temp_content_path, final_content_path)
                error_content = ''
        except Exception as ex:  # FIXME: robust error handling needed here to avoid hanging workers
            error_content = f'FATAL: {ex}'

        if error_content:
            # write error file
            async with aiofiles.open(_get_file_path(key, FileType.ERROR), 'w') as fd:
                await fd.write(error_content)

        # delete the fifo *before* we close it, and only once the content/error is in its final location and fully flushed
        # to avoid races with other consumers
        os.unlink(lock_path)  # FIXME: make async


class FileType(enum.IntEnum):
    CONTENT = enum.auto()
    ERROR = enum.auto()
    LOCK = enum.auto()


def _get_file_path(key: str, file_type: FileType) -> str:
    return os.path.join(pathlib.Path(__file__).parent, 'temp/blobstore', file_type.name.lower(), key)  # FIXME: coordinate on a temp dir


class BlobStore:
    async def get_dynamic(self, content: ContentDescriptor) -> AsyncResourceReader:
        try:
            return await self.get(content.key)
        except FileNotFoundError:
            pass

        lock_path = _get_file_path(content.key, file_type=FileType.LOCK)

        try:
            os.mkfifo(lock_path)  # FIXME: async this
        except FileExistsError:
            pass
        else:
            await content.create()

        try:
            # async with aiofiles.open(os.open(lock_path, os.O_NONBLOCK | os.O_RDONLY), 'r') as lock_file:
            async with aiofiles.open(lock_path, 'r') as lock_file:
                os.set_blocking(lock_file.fileno(), True)
                await lock_file.read()
        except FileNotFoundError as e:
            # expected when we're the writer, since we've already deleted the fifo
            pass

        try:
            return await self.get(content.key)
        except FileNotFoundError:
            pass

        # FIXME: try to read error file and report it, otherwise fall through to last-ditch exception below
        error_path = _get_file_path(content.key, file_type=FileType.ERROR)
        if os.path.exists(error_path):
            async with aiofiles.open(error_path, 'r') as error_file:
                msg = await error_file.read()

        else:
            msg = f'No error file found: {error_path}'

        raise Exception(msg)

    async def XXX_get_dynamic_markerfile(self, content: ContentDescriptor) -> AsyncResourceReader:
        path = _get_file_path(content.key)

        try:
            async with aiofiles.open(path, 'x'):
                await content.create()
        except FileExistsError:
            async with aiofiles.open(path, 'r') as marker_file:
                response = await marker_file.readline()  # FIXME: check success/failure

            if response:
                raise Exception(f'Marker error: {response}')

        return await self.get(content.key)

    async def get_dynamic_context(self, key: str) -> t.AsyncIterator[AsyncResourceWriter]:
        return dynamic_context(key)

    async def get(self, key: str) -> AsyncResourceReader:
        path = _get_file_path(key, FileType.CONTENT)
        return await AsyncFileReader.create(path)

    async def put(self, key: str) -> AsyncResourceWriter:  # FIXME: shouldn't these be context managers?
        path = _get_file_path(key, FileType.CONTENT)
        return await AsyncFileWriter.create(path)


BLOB_STORE = BlobStore()


class AsyncFileReader(AsyncResourceReader):
    @classmethod
    async def create(cls, path: str) -> 'AsyncFileReader':
        return AsyncFileReader(await aiofiles.open(path, 'rb'))

    def __init__(self, fd) -> None:
        self._fd = fd

    async def read(self, n=-1) -> bytes:
        return await self._fd.read(n)

    async def close(self) -> None:
        await self._fd.close()


class AsyncFileWriter(AsyncResourceWriter):
    @classmethod
    async def create(cls, path: str) -> 'AsyncFileWriter':
        return AsyncFileWriter(await aiofiles.open(path, 'wb'))

    def __init__(self, fd) -> None:
        self._fd = fd

    async def write(self, data: bytes) -> None:
        await self._fd.write(data)

    async def close(self) -> None:
        await self._fd.close()

#
#
# def _get_aio_streamreader(path) -> asyncio.StreamReader:
#     loop = asyncio.get_running_loop()
#
#     # FIXME: small line-length limit is problematic with large stdout and zip payloads;
#     #  the latter can be handled with an explicit chunked read + copy to disk until separator or
#     #  firehose directly into a future aiozipstream. For now, it's just all getting pulled into memory.
#     reader = asyncio.StreamReader(limit=2 ** 32, loop=loop)
#     protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
#
#     transport = FileTransport(path, loop)
#     transport.set_protocol(protocol)
#     return reader
#
#
# class FileTransport(asyncio.transports.ReadTransport):
#     def __init__(self, path, loop):
#         super().__init__()
#         self._path = path
#         self._loop = loop
#         self._closing = False
#
#     def is_closing(self):
#         return self._closing
#
#     def close(self):
#         self._closing = True
#
#     def set_protocol(self, protocol):
#         self._protocol = protocol
#         self._loop.create_task(self._do_read())
#
#     def get_protocol(self):
#         return self._protocol
#
#     async def _do_read(self):
#         try:
#             async with aiofiles.open(self._path, 'rb') as f:
#                 self._loop.call_soon(self._protocol.connection_made, self)
#                 async for line in f:
#                     self._loop.call_soon(self._protocol.data_received, line)
#                     if self._closing:
#                         break
#                 self._loop.call_soon(self._protocol.eof_received)
#         except Exception as ex:
#             self._loop.call_soon(self._protocol.connection_lost, ex)
#         else:
#             self._loop.call_soon(self._protocol.connection_lost, None)