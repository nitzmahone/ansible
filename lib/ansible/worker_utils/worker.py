# hosted by multiprocessing spawned worker, accepts an input and output queue, picks tasks off the input queue, runs
# them in an asyncio event loop

from __future__ import annotations

import asyncio
import functools
import importlib
import queue
import sys
import traceback
import typing as t
import uuid
import logging
import secrets

from .action import AsyncAction
from .become import AsyncBecome
from .connection import AsyncConnection, get_connection_plugin_type
from .exec import Exec
from .message import (BaseTaskRequest, BaseTaskResult, ActionRequest, ShutdownWorkerRequest, TaskResult,
                      ShutdownWorkerResponse, ExecCommandRequest, PutFileRequest, FetchFileRequest,
                      ContentDescriptorRequest, WorkerRequest, BaseTask, TaskOptions)
from .plugin_manager import get_plugin
from .storage import (BLOB_STORE)
from .tasks import TaskContext
from ._util import hash_args

from multiprocessing import get_context
mp = get_context('spawn')

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class WorkerTaskContext(TaskContext):
    def __init__(self, *, worker: ActionHostWorker, request: ActionRequest, **kwargs) -> None:
        self._worker = worker
        self._request = request

    @property
    def task_options(self) -> TaskOptions:
        return self._request.task_options

    @property
    def action_args(self) -> dict[str, ...]:
        return self._request.action_args

    @property
    async def connection(self) -> AsyncConnection:
        return await self._worker.get_connection(self._request.task_options)

    @property
    async def exec(self):
        exec_name = self._request.task_options.plugins['exec']

        return get_plugin(plugin_name=exec_name, plugin_type=Exec, task_options=self._request.task_options)

    @property
    async def become(self) -> t.Optional[AsyncBecome]:
        become_name = self._request.task_options.plugins.get('become', None)
        if become_name:
            return get_plugin(plugin_name=become_name, plugin_type=AsyncBecome, task_options=self._request.task_options)

    async def send_message(self, request: BaseTaskRequest) -> TaskResult:
        channel = self._worker.incoming_messages[request.task_id] = asyncio.Queue()
        try:
            self._worker.put_result(request)
            return await channel.get()
        finally:
            del self._worker.incoming_messages[request.task_id]


class ActionHostWorker(mp.Process):
    _sleep_delay = 0.01

    def __init__(self, result_queue: mp.Queue, workload_type: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._connections: dict[str, AsyncConnection] = {}
        self.incoming_messages: dict[uuid.UUID, asyncio.Queue[TaskResult]] = {}
        self.task_queue: t.Optional[mp.Queue] = None
        self.task_result_queue = result_queue
        self._conn_lock = None
        self._workload_type = workload_type
        self.id = secrets.token_hex(3)  # FIXME: make this more human-readable/meaningful based on worker type?

    def start(self):
        self.task_queue = mp.Queue()
        super().start()

    def run(self) -> None:
        logging.basicConfig(
            filename='temp/debug.log',
            encoding='utf-8',
            format=f"%(asctime)s %(filename)s:%(lineno)s %(funcName)s() [id={self.id}, type={self._workload_type}] %(message)s",
        )

        self._conn_lock = asyncio.Lock()

        logger.info('worker started')

        asyncio.run(self.async_run())

        logger.info('worker completed')

    async def run_tasks(self) -> ShutdownWorkerRequest:
        logger.debug('in worker')
        task: BaseTaskRequest

        try:
            while True:
                try:
                    task = self.task_queue.get(block=False)
                except queue.Empty:
                    await asyncio.sleep(self._sleep_delay)
                    continue

                logger.debug("Worker received task %s", task)

                if isinstance(task, ShutdownWorkerRequest):
                    return task

                if isinstance(task, TaskResult):
                    if correlated_task := self.incoming_messages.get(task.task_id):
                        logger.debug("Waking up %s", task.task_id)
                        # wake up the awaiting task
                        correlated_task.put_nowait(task)
                    else:
                        logger.error(f'worker got unknown task result: %s', task.task_id)
                        pass  # FIXME: warn/log unknown task result
                else:
                    asyncio.create_task(self._run_task(task))

        finally:
            logger.debug('exiting worker loop in run_tasks')

    async def async_run(self) -> None:
        task = await self.run_tasks()

        # we're shutting down
        logger.debug('stuffing shutdown 1')
        self.put_result(ShutdownWorkerResponse(task.task_id, "ack"))

        await asyncio.gather(*[c.close() for c in self._connections.values()])
        self._connections = {}
        logger.debug('stuffing shutdown 2')
        self.put_result(ShutdownWorkerResponse(task.task_id, status="ok"))

    def put_result(self, result: BaseTask) -> None:
        logger.debug("Worker putting result %s", result)
        self.task_result_queue.put((self.id, result))

    async def _run_task(self, task: BaseTaskRequest) -> None:
        # dispatch a task, wait for its result, marshal result to queue
        # FIXME: bookkeeping of running tasks, concurrency controls, blah
        result = await self.dispatch_task_safe(task)
        self.put_result(result)

    async def dispatch_task_safe(self, task: BaseTaskRequest) -> BaseTaskResult:
        try:
            return await self.dispatch_task(task)
        except Exception as ex:
            logger.debug('dispatching exception: %s', traceback.format_exc())
            return create_task_exception(task, ex)

    @functools.singledispatchmethod
    async def dispatch_task(self, task: BaseTaskRequest) -> BaseTaskResult:
        raise NotImplementedError(f'No dispatch method available for task type: {type(task)}')

    @dispatch_task.register
    async def _(self, task: ActionRequest) -> TaskResult:
        context = WorkerTaskContext(worker=self, request=task)

        action = get_plugin(plugin_name=task.action, plugin_type=AsyncAction, task_options=task.task_options,
                            context=context)
        result = await action.run()
        return create_task_result(task, result)

    @dispatch_task.register
    async def _(self, task: WorkerRequest) -> TaskResult:
        return create_task_result(task, {'pong': f'pong from {task.ping} in worker {self.id}'})

    @dispatch_task.register
    async def _(self, task: ExecCommandRequest) -> TaskResult:
        conn = await self.get_connection(task.task_options)
        stdin_reader = await BLOB_STORE.get(task.stdin_key) if task.stdin_key else None
        stdout, stderr, rc = await conn.exec_command(task.cmd, stdin_reader)

        return create_task_result(task, dict(failed=rc != 0, stdout=stdout, stderr=stderr))

    @dispatch_task.register
    async def _(self, task: PutFileRequest) -> TaskResult:
        conn = await self.get_connection(task.task_options)
        await conn.put_file(await task.src.reader, task.dst_path)

        return create_task_result(task, dict(failed=False))

    @dispatch_task.register
    async def _(self, task: FetchFileRequest) -> TaskResult:
        conn = await self.get_connection( task.task_options)
        await conn.fetch_file(task.src_path, await task.dst.writer)

        return create_task_result(task, dict(failed=False))

    @dispatch_task.register
    async def _(self, task: ContentDescriptorRequest) -> TaskResult:
        plugin_module_name, plugin_class_name = task.plugin.rsplit('.', 1)
        plugin_module = importlib.import_module(plugin_module_name)
        plugin_type = getattr(plugin_module, plugin_class_name)

        await plugin_type.callback(task)

        return create_task_result(task, dict(failed=False))

    async def get_connection(self, task_options: TaskOptions) -> AsyncConnection:
        conn_name = task_options.plugins.get('connection', None)
        if not conn_name:
            raise ValueError('No connection plugin set in task options')

        connection_options = task_options.plugin_options[conn_name]
        conn_id = hash_args(connection_options)

        async with self._conn_lock:
            if conn_id not in self._connections:
                connection = get_connection_plugin_type(conn_name)()
                connection.set_options(connection_options)
                await connection.connect()
                self._connections[conn_id] = connection

        return self._connections[conn_id]


def create_task_result(task: BaseTaskRequest, result: dict[str, ...]) -> TaskResult:
    return TaskResult(task.task_id, result)


def create_task_failure(task: BaseTaskRequest, msg: str, **kwargs) -> TaskResult:
    return create_task_result(task, dict(
        msg=msg,
        failed=True,
        **kwargs,
    ))


def create_task_exception(task: BaseTaskRequest, ex: Exception) -> TaskResult:
    return create_task_failure(task, msg=str(ex), traceback=traceback.extract_tb(sys.exc_info()[2]).format())
