from __future__ import annotations

import abc
import dataclasses
import typing as t
import uuid
import copy

from .storage_protocol import AsyncResourceReader, AsyncResourceWriter


@dataclasses.dataclass(frozen=True)
class TaskOptions:
    plugins: dict[str, str]
    plugin_options: dict[str, dict[str, t.Any]] = dataclasses.field(default_factory=dict)

    def copy(self) -> TaskOptions:
        return TaskOptions(
            plugins=self.plugins.copy(),
            plugin_options=copy.deepcopy(self.plugin_options),
        )


@dataclasses.dataclass(frozen=True)
class BaseTask:
    task_id: uuid.UUID


@dataclasses.dataclass(frozen=True)
class BaseTaskRequest(BaseTask):
    task_options: TaskOptions


@dataclasses.dataclass(frozen=True)
class BaseTaskResult(BaseTask):
    pass


@dataclasses.dataclass(frozen=True)
class ActionRequest(BaseTaskRequest):
    action: str
    action_args: dict[str, ...]


@dataclasses.dataclass(frozen=True)
class WorkerRequest(BaseTaskRequest):
    ping: str


@dataclasses.dataclass(frozen=True)
class BaseResource(metaclass=abc.ABCMeta):
    @property
    @abc.abstractmethod
    async def reader(self) -> AsyncResourceReader:
        pass

    @property
    @abc.abstractmethod
    async def writer(self) -> AsyncResourceWriter:
        pass


@dataclasses.dataclass(frozen=True)
class PutFileRequest(BaseTaskRequest):
    src: BaseResource
    dst_path: str


@dataclasses.dataclass(frozen=True)
class FetchFileRequest(BaseTaskRequest):
    src_path: str
    dst: BaseResource


@dataclasses.dataclass(frozen=True)
class ExecCommandRequest(BaseTaskRequest):
    cmd: str
    stdin_key: t.Optional[str] = None
    #
    # @property
    # async def stdin(self) -> t.Optional[AsyncResourceReader]:
    #     return await BLOB_STORE.get(self.stdin_key) if self.stdin_key else None


@dataclasses.dataclass(frozen=True)
class TaskResult(BaseTaskResult):
    task_id: uuid.UUID
    result: dict[str, ...]


@dataclasses.dataclass(frozen=True)
class ShutdownWorkerRequest(BaseTaskRequest):
    pass


@dataclasses.dataclass(frozen=True)
class ShutdownWorkerResponse(BaseTaskResult):
    status: t.Literal["ack", "ok", "need_more_time"]


@dataclasses.dataclass(frozen=True)
class TaskFailedResult(BaseTaskResult):
    message: str


@dataclasses.dataclass(frozen=True)
class ContentDescriptorRequest(BaseTaskRequest):
    plugin: str
    key: str
