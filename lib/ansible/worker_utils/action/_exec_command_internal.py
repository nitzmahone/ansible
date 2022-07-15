from . import AsyncAction
from ..storage_protocol import AsyncResourceReader


class BytesReader(AsyncResourceReader):

    def __init__(self, data: bytes):
        self.data = bytearray(data)

    async def read(self, n: int = -1) -> bytes:
        if n == -1:
            n = len(self.data)

        d = bytes(self.data[:n])
        self.data = self.data[n:]

        return d


class _ExecCommandAction(AsyncAction):
    uses_plugin_type_names = frozenset({'connection'})

    async def run(self) -> dict[str, ...]:
        # FIXME: remote become config over from worker and handle it inline here...

        connection = await self.context.connection

        command = self.context.action_args['cmd']
        in_data = self.context.action_args["in_data"]
        stdin = BytesReader(in_data) if in_data else None

        stdout, stderr, rc = await connection.exec_command(command, stdin)

        return {
            'stdout': stdout,
            'stderr': stderr,
            'rc': rc,
        }
