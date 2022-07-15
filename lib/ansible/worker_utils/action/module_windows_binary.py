import json
import pathlib
import tempfile
import uuid

from . import AsyncAction
from ..storage import AsyncFileReader


class BinaryAction(AsyncAction):
    plugin_options = {}
    uses_plugin_type_names = frozenset({'connection'})

    async def run(self) -> dict[str, ...]:
        connection = await self.context.connection
        module_name = self.context.action_args['module']
        module_options = {
            'ANSIBLE_MODULE_ARGS': self.context.action_args['options']
        }

        binary = await AsyncFileReader.create(str(pathlib.Path(__file__).parent.parent.joinpath('files', module_name)))

        tmpdir = f"%TMP%\\sworks-{uuid.uuid4()}"
        module_tmpfile = f"{tmpdir}\\{module_name}"
        args_tmpfile = f"{tmpdir}\\args.json"
        mkdir_temp = f"New-Item -Path ([System.Environment]::ExpandEnvironmentVariables('{tmpdir}')) -ItemType Directory -Force"
        rmdir_temp = f"Remove-Item -Path ([System.Environment]::ExpandEnvironmentVariables('{tmpdir}')) -Recurse -Force"
        cmdline = f"&([System.Environment]::ExpandEnvironmentVariables('{module_tmpfile}')) ([System.Environment]::ExpandEnvironmentVariables('{args_tmpfile}'))"

        tmpdir_stdout, tmpdir_stderr, tmpdir_rc = await connection.exec_command(mkdir_temp)
        if tmpdir_rc:
            return {
                "failed": True,
                "msg": f"Failed to create tempdir at '{tmpdir}'",
                "rc": tmpdir_rc,
                "stdout": tmpdir_stdout.decode(),
                "stderr": tmpdir_stderr.decode(),
            }

        await connection.put_file(binary, module_tmpfile)

        with tempfile.NamedTemporaryFile() as temp_fd:
            temp_fd.write(json.dumps(module_options).encode())
            temp_fd.flush()

            temp_reader = await AsyncFileReader.create(temp_fd.name)
            await connection.put_file(temp_reader, args_tmpfile)

            exec_stdout, exec_stderr, exec_rc = await connection.exec_command(cmdline)

            # FIXME: Better error handling here
            await connection.exec_command(rmdir_temp)
            try:
                # FIXME: inspect this for changed/failed/etc?
                return json.loads(exec_stdout)
            except json.decoder.JSONDecodeError as e:
                return {
                    'failed': True,
                    'msg': f'Unknown failure when invoking module: {e}',
                    "rc": exec_rc,
                    "stdout": exec_stdout.decode(),
                    "stderr": exec_stderr.decode(),
                }

