import asyncio

from . import AsyncAction
from ..connection import pump_stream, BytesIOWriter


class RawAction(AsyncAction):
    uses_plugin_type_names = frozenset({'become', 'connection'})

    async def run(self) -> dict[str, ...]:
        become = await self.context.become
        connection = await self.context.connection

        command = self.context.action_args['command']

        requires_tty = False
        if become:
            command = become.build_become_command(command)
            requires_tty = become.requires_tty

        if requires_tty:
            proc = await connection.streaming_exec_command_with_tty(command)
        else:
            proc = await connection.streaming_exec_command(command)

        if become:
            stdout, stderr, stdin = await become.apply_stdio_filter(
                proc.stdout,
                proc.stderr,
                proc.stdin)
        else:
            stdout = proc.stdout
            stderr = proc.stderr
            stdin = proc.stdin

        stdout_writer = BytesIOWriter()
        stderr_writer = BytesIOWriter()
        rc = (await asyncio.gather(
            proc.wait_for_exit(),
            pump_stream(stdout, stdout_writer),
            pump_stream(stderr, stderr_writer),
            stdin.write_eof(),
        ))[0]
        stdout = stdout_writer.buffer.getvalue()
        stderr = stderr_writer.buffer.getvalue()

        return {
            'failed': rc != 0,
            'stdout': stdout.decode(),
            'stderr': stderr.decode(),
            'rc': rc,
        }
