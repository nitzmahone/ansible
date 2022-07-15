import abc
import typing as t

from ..become import AsyncBecome
from ..connection import AsyncConnection
from ..message import BaseTaskRequest, TaskResult, TaskOptions


class TaskContext(metaclass=abc.ABCMeta):
    @property
    def task_options(self) -> TaskOptions:
        pass

    @property
    def action_args(self) -> dict[str, ...]:
        pass

    @property
    async def become(self) -> t.Optional[AsyncBecome]:
        pass

    @property
    async def connection(self) -> AsyncConnection:
        pass

    @property
    async def exec(self):
        pass

    async def send_message(self, request: BaseTaskRequest) -> TaskResult:
        pass
