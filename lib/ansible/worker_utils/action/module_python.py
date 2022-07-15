import asyncio
import base64
import json
import logging
import pathlib
import shlex
import uuid

from . import AsyncAction
from ..exec import ContentDescriptor
from ..message import ContentDescriptorRequest, TaskOptions
from ..tasks import TaskContext
from ..storage import AsyncFileReader, BlobStore, AsyncResourceReader
from .._util import hash_args

# FIXME: Make generic
from ..connection import pump_stream, BytesIOWriter

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class EncodedReader(AsyncResourceReader):

    def __init__(self, inner: AsyncResourceReader) -> None:
        self._inner = inner
        self._done = False

    async def read(self, n=-1) -> bytes:
        if self._done:
            return b""

        buffer_length = 1024
        raw_length = (buffer_length // 4) * 3

        data = await self._inner.read(raw_length)
        if not data:
            self._done = True

        encoded_chunk = base64.b64encode(data)
        return encoded_chunk + b"\n"


class AsyncPythonModule(AsyncAction):
    uses_plugin_type_names = frozenset({'become', 'connection'})
    plugin_options = {
        'tmp_remove_later': ['ansible_tmp_remove_later']
    }

    async def run(self) -> dict[str, ...]:
        blob_store = BlobStore()  # FIXME: get this properly
        module_name = self.context.action_args['module']
        module_options = self.context.action_args.get('options', {})

        logger.debug(f"%s - get module start", self.context._worker.id)

        # become = await self.context.become
        become = None

        python_payload_bootstrap = pathlib.Path(__file__).parent.parent / 'files' / 'python_payload_bootstrap.py'
        with open(python_payload_bootstrap, mode='rb') as fd:
            bootstrap_content = base64.b64encode(fd.read()).decode()

        bootstrap_template = f'import base64; exec(base64.b64decode("{bootstrap_content}").decode())'
        module_cmd = shlex.join(['python', '-c', bootstrap_template])
        if become:
            module_cmd = become.build_become_command(module_cmd)

        create_payload = CreatePythonModule(
            module_name=module_name,
            module_options=module_options,
            context=self.context)

        module_stdin = EncodedReader(await blob_store.get_dynamic(create_payload))

        logger.debug(f"%s - get module done", self.context._worker.id)

        connection = await self.context.connection

        logger.debug(f'running module code start')
        proc = await connection.streaming_exec_command(module_cmd)
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
            pump_stream(module_stdin, stdin)
        ))[0]
        stdout = stdout_writer.buffer.getvalue()
        stderr = stderr_writer.buffer.getvalue()

        # Old code
        # stdout, stderr, rc = await connection.exec_command(module_cmd, stdin=module_stdin, become=become)

        logger.debug(f'running module code done')
        if rc:
            return {
                'failed': True,
                'msg': 'Unknown module failure',
                'stdout': stdout.decode(),
                'stderr': stderr.decode(),
                'rc': rc,
            }

        return json.loads(stdout)


class CreatePythonModule(ContentDescriptor):
    plugin_options = {}

    def __init__(self, module_name: str, module_options: dict, context: TaskContext) -> None:
        action_fqn = context.task_options.plugins['action']

        self.partial_task_options = TaskOptions(
            plugins={'action': action_fqn, 'module': module_name},
            plugin_options={
                action_fqn: context.task_options.plugin_options[action_fqn],
                module_name: module_options,
            },
        )

        self.key = f'{module_name}_{hash_args(module_options)}_{hash_args(self.partial_task_options.__dict__)}_payload.py'
        self.context = context

    async def create(self) -> None:
        req = ContentDescriptorRequest(
            task_id=uuid.uuid4(),
            plugin=f"{self.__module__}.{self.__class__.__qualname__}",
            key=self.key,
            task_options=self.partial_task_options,
        )
        await self.context.send_message(req)

    @classmethod
    async def callback(cls, request: ContentDescriptorRequest) -> None:
        action_options = request.task_options.plugin_options[request.task_options.plugins['action']]
        tmp_remove_later = action_options.get('tmp_remove_later', '')

        blob_store = BlobStore()

        async with await blob_store.get_dynamic_context(request.key) as writer:
            # content generation happens inside context manager to handle errors
            module_name = request.task_options.plugins['module']
            module_options = request.task_options.plugin_options[module_name]

            if module_name == 'not_a_real_module':
                content = f'''import json
import getpass
import time
time.sleep(0)

data = {{
    'changed': True,
    'key': '{request.key}',
    'msg': 'hi mom from {tmp_remove_later} {uuid.uuid4()}',
    'user': getpass.getuser(),
}}
print(json.dumps(data))'''.encode()

            else:
                module_path = pathlib.Path(__file__).parent.parent.joinpath('files', module_name)
                module_fd = await AsyncFileReader.create(f"{module_path}.py")
                module_content = await module_fd.read()
                content = module_content.replace(b'TEMPLATE_OPTIONS', json.dumps(module_options).encode())

            await writer.write(content)
