import uuid
import typing as t

from multiprocessing import get_context
mp = get_context('spawn')

from ansible.worker_utils.message import ActionRequest, TaskOptions
from ansible.worker_utils.exec import Exec
from ..storage import AsyncResourceReader, BLOB_STORE


class BinaryExec(Exec):
    plugin_options = {}

    @classmethod
    def supports_action(cls, name: str) -> bool:
        return name.startswith('helloworld_')  # FIXME: make determination by name or content (retrieved by name)

    @classmethod
    def create_request(
            cls,
            *,
            name: str,
            task_options: TaskOptions,
            action_args: dict[str, t.Any],
            **kwargs: t.Any,
    ) -> ActionRequest:
        task_options = task_options.copy()  # FIXME: do we even need to copy and modify the task options?
        task_options.plugins['exec'] = 'ansible.worker_utils.exec.binary'  # FIXME: use plugin loader

        return ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action='ansible.worker_utils.action.binary_module',
            action_args={
                'name': name,
                'options': action_args,
            },
        )

    # NB: this probably won't be on the base class- the actions and exec subsystems are tightly coupled
    async def get_module_payload(self, name: str) -> AsyncResourceReader:
        return await get_module_content(name)


async def get_module_content(name: str) -> AsyncResourceReader:
    return await BLOB_STORE.get(name)
