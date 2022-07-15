import abc
import typing as t

from ..message import ActionRequest, ContentDescriptorRequest
from ..plugin_manager import BasePlugin
from ..tasks import TaskContext

C = t.TypeVar('C')


class ContentDescriptor(metaclass=abc.ABCMeta):
    key: str
    context: TaskContext

    @abc.abstractmethod
    async def create(self) -> None:
        pass

    @classmethod
    async def callback(cls, request: ContentDescriptorRequest) -> None:
        pass


class Exec(BasePlugin):
    ansible_variable_name = 'ansible_exec'
    ansible_plugin_type = 'exec'

    @classmethod
    def supports_action(cls, name: str) -> bool:
        pass

    @classmethod
    def create_request(
            cls,
            *,
            name: str,
            task_options: dict[str, t.Any],
            action_args: dict[str, t.Any],
            **kwargs: t.Any,
    ) -> ActionRequest:
        pass
