from __future__ import annotations

import asyncio
import typing as t

from ..plugin_manager import BasePlugin
from ..storage_protocol import AsyncResourceReader, AsyncResourceWriter


class BecomeReader(AsyncResourceReader):

    def __init__(
            self,
            stream_type: str,
            become: AsyncBecome,
            reader: AsyncResourceReader,
            writer: AsyncResourceWriter,
            completed_event: asyncio.Event,
    ) -> None:
        self.stream_type = stream_type
        self.become = become
        self.reader = reader
        self.writer = writer
        self.completed_event = completed_event
        self._process_gen = self.become.process_data(self.stream_type, self.reader, self.writer)

    async def read(self, n: int = -1) -> t.Optional[bytes]:
        while self._process_gen is not None:
            try:
                # FIXME: Do we want to put a lock around this so only 1 stream
                # is processed.
                data = await self._process_gen.__anext__()
            except StopAsyncIteration:
                self._process_gen = None
                self.completed_event.set()

            else:
                if data:
                    return data

        return await self.reader.read(n)


class BecomeWriter(AsyncResourceWriter):

    def __init__(
            self,
            become: AsyncBecome,
            writer: AsyncResourceWriter,
            completed_event: asyncio.Event,
    ) -> None:
        self.become = become
        self.writer = writer
        self.completed_event = completed_event

    async def write(self, data: bytes) -> None:
        await self.completed_event.wait()
        await self.writer.write(data)

    async def write_eof(self) -> None:
        await self.completed_event.wait()
        await self.writer.write_eof()


class AsyncBecome(BasePlugin):
    ansible_variable_name = 'ansible_become_method'
    ansible_plugin_type = 'become'

    @property
    def completed(self) -> bool:
        """Marks whether the become process is complete.

        Needs to be overridden by become plugins and conditionally set once it
        is finished reading data from the remote process. When set to True the
        process_data function is no longer called and the data is returned as is.
        """
        return True

    @property
    def requires_tty(self) -> bool:
        """Become plugin requires TTY to wrap command."""
        return False

    def build_become_command(self, cmd: str) -> str:
        """Wraps the cmd inside the become executable.

        It is expected that become plugins implement this to wrap any commands
        the caller wishes to run under the become context.

        Args:
            cmd: The command to wrap.

        Returns:
            str: The wrapped command that will invoke the become executable.
        """
        return cmd

    async def process_data(
            self,
            stream_type: str,
            reader: AsyncResourceReader,
            writer: AsyncResourceWriter,
    ) -> t.AsyncIterator[bytes]:
        """Calls by the stream pumper to read from the process stdio.

        Reads from a particular process stdio until the become plugin doesn't
        need to process any more data.

        A plugin can return data at any time that needs to be passed back to
        process output reader. The iterator will be called again where it left
        off to continue processing data. This will keep on happening until the
        plugin marks itself as complete, in that case it will no longer process
        the data.

        While a become plugin can override this to have complete control over
        how it reads the data from the remote process it might be better to
        just override the process_std*_data functions instead which take in a
        pre-processed buffer with a simpler API. It's essentially a trade-off
        between finer control and simpler code.

        Args:
            stream_type: The type of the stream, stdout or stderr.
            reader: The async stream to read from.
            writer: The writer for the process stdin.

        Yields:
            bytes: Any data remaining on the stream that is not consumed by
            the become operation and should be returned as normal process out.
        """
        lines: t.List[bytes] = []
        completed_newline = True

        while not self.completed:
            data = await reader.read(4096)
            if not data:
                continue

            while data:
                newline_idx = data.find(b"\n")
                if newline_idx == -1:
                    newline_idx = len(data)

                if completed_newline:
                    lines.append(data[:newline_idx])
                else:
                    lines[-1] += data[:newline_idx]

                completed_newline = len(data) > newline_idx
                data = data[newline_idx + 1:]

            # FIXME: Call this per line - deal with complete in between lines
            # and return remaining.
            remaining_data = await self.process_data_line(stream_type, lines,
                                                          writer)
            if remaining_data:
                # FIXME: Does this need some trickery to remove the
                # remaining data from our buffer to avoid it being processed again?
                yield remaining_data

    async def process_data_line(
        self,
        stream_type: str,
        lines: t.List[bytes],
        writer: AsyncResourceWriter,
    ) -> t.Optional[bytes]:
        """Process the data received on stdout.

        Processes the next block of bytes that was received from the process'
        stdout or stderr stream.

        It is expected that a become plugin implements this method to handle
        data that was received on the stdout stream and potentially write data
        on the process' stdin pipe (like a password) or mark the become plugin
        as complete.

        Args:
            stream_type: The stream the data is from.
            lines: The byte string lines collected so far.
            writer: The remote process' stdin writer that can be used to write
                data into the process.

        Returns:
            t.Optional[bytes]: Any data that needs to be passed back to the
            caller that is reading the process stream. This is essentially data
            that is not to be consumed by the become plugin.
        """
        return None

    async def apply_stdio_filter(
            self,
            stdout: AsyncResourceReader,
            stderr: AsyncResourceReader,
            stdin: AsyncResourceWriter
    ) -> t.Tuple[AsyncResourceReader, AsyncResourceReader, AsyncResourceWriter]:
        completed_event = asyncio.Event()
        # FIXME: May want to wrap stdin so that only 1 of the readers can
        # write to it at the same time
        filtered_stdout = BecomeReader('stdout', self, stdout, stdin, completed_event)
        filtered_stderr = BecomeReader('stderr', self, stderr, stdin, completed_event)
        filtered_stdin = BecomeWriter(self, stdin, completed_event)

        return filtered_stdout, filtered_stderr, filtered_stdin
