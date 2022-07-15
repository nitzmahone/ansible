import json
import logging
import uuid

from . import AsyncAction
from ..exec import ContentDescriptor
from ..message import ContentDescriptorRequest, TaskOptions
from ..tasks import TaskContext
from ..storage import BlobStore
from .._util import hash_args

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AsyncPowerShellModule(AsyncAction):
    uses_plugin_type_names = frozenset({'connection'})
    plugin_options = {
        'tmp_remove_later': ['ansible_tmp_remove_later']
    }

    async def run(self) -> dict[str, ...]:
        blob_store = BlobStore()  # FIXME: get this properly
        module_name = self.context.action_args['module']

        logger.debug(f"%s - get module start", self.context._worker.id)

        module_cmd = '''
            [CmdletBinding()]
            param (
                [Parameter(ValueFromPipeline)]
                [byte[]]
                $InputObject
            )

            process {
                Invoke-Expression ([System.Text.Encoding]::UTF8.GetString($InputObject))
            }'''

        create_payload = CreatePowerShellModule(module_name=module_name, context=self.context)

        module_stdin = await blob_store.get_dynamic(create_payload)
        logger.debug(f"%s - get module done", self.context._worker.id)

        connection = await self.context.connection

        logger.debug(f'running module code start')
        stdout, stderr, rc = await connection.exec_command(module_cmd, stdin=module_stdin)
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


class CreatePowerShellModule(ContentDescriptor):
    plugin_options = {}

    def __init__(self, module_name: str, context: TaskContext) -> None:
        action_fqn = context.task_options.plugins['action']

        self.partial_task_options = TaskOptions(
            plugins={'action': action_fqn},
            plugin_options={
                action_fqn: context.task_options.plugin_options[action_fqn],
            },
        )

        self.key = f'{module_name}_{hash_args(self.partial_task_options.__dict__)}_payload.ps1'
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
            content = ('ConvertTo-Json -InputObject @{changed = $true; key = "%s"; msg = "hi mom from %s %s"}'
                       % (request.key, tmp_remove_later, uuid.uuid4())).encode()
            await writer.write(content)

