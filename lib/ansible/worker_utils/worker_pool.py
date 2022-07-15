import threading
import uuid
import traceback
import typing as t
import logging

from multiprocessing import get_context
mp = get_context('spawn')

from queue import Queue, LifoQueue
from threading import Thread

from .worker import ActionHostWorker
from .message import BaseTask, ShutdownWorkerRequest

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# HACK: debugging
threading.excepthook = lambda args: logger.error("%s: %s", args.exc_value, traceback.format_tb(args.exc_traceback))


class WorkerPool:
    def __init__(self, workload_type: str, result_queue: Queue[BaseTask], max_workers: int = 2, supports_concurrent_tasks: bool = False):
        self.workload_type = workload_type
        self._immediate_stop_requested = False
        self._max_workers = max_workers
        self._supports_concurrent_tasks = supports_concurrent_tasks
        # work awaiting dispatch to a worker
        self._task_queue: Queue[t.Optional[BaseTask]] = Queue()
        self._task_thread = Thread(target=self._task_queue_thread_proc, daemon=True)
        self._task_response_thread = Thread(target=self._task_response_thread_proc, daemon=True)
        self._idle_workers: LifoQueue[t.Optional[ActionHostWorker]] = LifoQueue()

        # pre-populate the queue with empty worker placeholders
        for _idx in range(self._max_workers):
            self._idle_workers.put(None)

        self._worker_by_worker_id: dict[str, ActionHostWorker] = {}
        self._worker_by_task_id: dict[uuid.UUID, ActionHostWorker] = {}
        self._pending_result_queue = mp.Queue()
        self._main_result_queue = result_queue

        self._task_thread.start()
        self._task_response_thread.start()

        self._requested_tasks: dict[uuid.UUID, Queue] = {}
        self._relayed_tasks: dict[uuid.UUID, Queue] = {}

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def queue(self, task: BaseTask) -> None:
        logger.debug('queueing task for workerpool: %s', type(task))
        self._task_queue.put(task)

    def stop(self, drain=False, timeout_sec: t.Optional[float] = None):
        self._immediate_stop_requested = drain

        for worker in self._worker_by_worker_id.values():
            worker.task_queue.put(ShutdownWorkerRequest(task_id=uuid.uuid4(), task_options={}))

        self._task_queue.put(None)
        self._pending_result_queue.put(None)

        # FIXME: Deal with timeout

        self._task_thread.join(timeout_sec)
        self._task_response_thread.join(timeout_sec)

        logger.debug('waiting on workers (join/close)')

        for worker in self._worker_by_worker_id.values():
            worker.join()
            worker.close()  # FIXME: what's safe and reliable here? parallel shutdown?

        logger.debug('workers closed')

    def _task_queue_thread_proc(self):
        idle_workers = self._idle_workers
        worker_by_worker_id = self._worker_by_worker_id
        worker_by_task_id = self._worker_by_task_id
        get = self._task_queue.get
        result_queue = self._pending_result_queue

        # FIXME: exception handling
        while True:
            if not (task := get()) or self._immediate_stop_requested:
                break

            logger.debug("worker task_queue_thread_proc %s", type(task))

            if msg_queue := self._relayed_tasks.pop(task.task_id, None):
                msg_queue.put(task)

            else:
                # FIXME: logical deadlock possible in fan-out situations with single-threaded workers
                worker = idle_workers.get()

                if not worker:
                    # create a new worker, put it to work
                    worker = ActionHostWorker(result_queue, workload_type=self.workload_type)
                    worker.start()
                    worker_by_worker_id[worker.id] = worker

                if self._supports_concurrent_tasks:
                    idle_workers.put(worker)

                logger.debug('worker pool assigning task %s (type %s) to worker (and de-idling worker %s)', task.task_id, type(task), worker.id)

                worker_by_task_id[task.task_id] = worker
                worker.task_queue.put(task)

                self._requested_tasks[task.task_id] = worker.task_queue

    def _task_response_thread_proc(self):
        get = self._pending_result_queue.get
        put = self._main_result_queue.put
        worker_by_worker_id = self._worker_by_worker_id
        worker_by_task_id = self._worker_by_task_id
        idle_workers = self._idle_workers

        while True:
            if not (result := get()) or self._immediate_stop_requested:
                break

            worker_id, result_task = result

            logger.debug("worker task_response_thread_proc %s", type(result_task))
            # worker = self._worker_by_task_id[result_task.task_id]
            # worker = worker_by_worker_id.get(worker_id)

            if relayed_queue := self._relayed_tasks.get(result_task.task_id, None):
                logger.debug('worker relaying task result %s to queue %s', result_task.task_id, id(relayed_queue))
                relayed_queue.put(result_task)
                continue

            if self._requested_tasks.pop(result_task.task_id, None):
                worker = self._worker_by_task_id[result_task.task_id]
                logger.debug('worker completing task %s locally (and idling worker %s)', result_task.task_id, worker.id)
                # worker sent final message, it's now free to do more work
                del worker_by_task_id[result_task.task_id]

                if not self._supports_concurrent_tasks:
                    idle_workers.put(worker)

            else:
                logger.debug('worker requested new task %s (from worker %s)', result_task, worker_id)
                worker = worker_by_worker_id[worker_id]
                self._relayed_tasks[result_task.task_id] = worker.task_queue

            put((self.workload_type, result_task))
