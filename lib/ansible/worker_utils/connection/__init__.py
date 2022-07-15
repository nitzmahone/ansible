import asyncio
import io
import typing as t
import abc

from ..storage_protocol import AsyncResourceReader, AsyncResourceWriter
from ..plugin_manager import get_plugin_type, BasePlugin


@t.runtime_checkable
class AsyncProcess(t.Protocol):
    stdin: AsyncResourceWriter
    stdout: AsyncResourceReader
    stderr: AsyncResourceReader
    rc: t.Optional[int]

    async def wait_for_exit(self) -> int:
        pass


class TtyNotSupportedError(NotImplementedError):
    def __init__(self, plugin_name: str):
        self.plugin_name = plugin_name

        super().__init__(f'Connection plugin {plugin_name} does not implement TTY support.')


class AsyncConnection(BasePlugin, metaclass=abc.ABCMeta):
    ansible_variable_name = 'ansible_connection'
    ansible_plugin_type = 'connection'

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def exec_command(
            self,
            cmd: str,
            stdin: t.Optional[AsyncResourceReader] = None,
    ) -> tuple[bytes, bytes, int]:
        stdout_writer = BytesIOWriter()
        stderr_writer = BytesIOWriter()

        proc = await self.streaming_exec_command(cmd)
        tasks = [
            proc.wait_for_exit(),
            pump_stream(proc.stdout, stdout_writer),
            pump_stream(proc.stderr, stderr_writer),
        ]
        if stdin:
            tasks.append(pump_stream(stdin, proc.stdin))

        rc = (await asyncio.gather(*tasks))[0]
        return stdout_writer.buffer.getvalue(), stderr_writer.buffer.getvalue(), rc

    async def streaming_exec_command_with_tty(
            self,
            cmd: str,
    ) -> AsyncProcess:
        raise TtyNotSupportedError(self.name)

    @abc.abstractmethod
    async def streaming_exec_command(
            self,
            cmd: str,
    ) -> AsyncProcess:
        pass

    @abc.abstractmethod
    async def put_file(self, src: AsyncResourceReader, dst: str) -> None:
        pass

    @abc.abstractmethod
    async def fetch_file(self, src: str, dst: AsyncResourceWriter) -> None:
        pass


class BytesIOWriter(AsyncResourceWriter):

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    async def write(self, data: bytes) -> None:
        self.buffer.write(data)


def get_connection_plugin_type(name: str) -> t.Type[AsyncConnection]:
    return get_plugin_type(plugin_name=name, plugin_type=AsyncConnection)


async def pump_stream(
        src: AsyncResourceReader,
        dst: AsyncResourceWriter,
        buffer_size: int = 4096
) -> None:
    if not src or not dst:
        return

    while buf := (await src.read(buffer_size)):
        await dst.write(buf)

    await dst.write_eof()
