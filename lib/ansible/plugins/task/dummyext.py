import asyncio
import itertools
import multiprocessing
import os
import queue
import threading

import asyncssh

from dataclasses import dataclass
from multiprocessing import Queue
from multiprocessing.synchronize import Event
from traceback import format_exc
from typing import List
from ...errors import AnsibleConnectionFailure
from ...executor.task_result import TaskResult

from datetime import datetime, timedelta

@dataclass
class AnsibleWorkerConfig:
    collection_path: List[str]
    shutdown_event: Event
    result_queue: Queue

@dataclass
class LWTaskResult:
    host: str
    task: str
    return_data: dict
    task_fields: dict

# @dataclass
# class WorkerTask:
#     task_data: dict
#     worker_task_vars: dict
#     host: str
#     uuid: str


class WorkerTask:
    def __init__(self, task_data=None, worker_task_vars=None, host=None, uuid=None):
        self.task_data = task_data
        self.worker_task_vars = worker_task_vars
        self.host = host
        self.uuid = uuid


def notprint(astr):
    #print(astr)
    pass

class AnsibleAsyncHostWorker(multiprocessing.context.SpawnProcess):
    def __init__(self, work_queue: Queue, worker_config: AnsibleWorkerConfig):
        # FIXME: decide what's boilerplate config object and what's discrete for workers
        self._work_queue = work_queue
        self._worker_config = worker_config
        self._shutdown_event = worker_config.shutdown_event
        self._should_stop = False
        self._result_queue = worker_config.result_queue
        super().__init__(target=self._run)

    def _run(self) -> None:
        print(f'hello from worker PID {os.getpid()}')
        asyncio.run(self._worker_proc())

    def _shutdown_threadproc(self):
        self._shutdown_event.wait()
        notprint("***SHUTDOWN REQUESTED")
        self._should_stop = True

    async def _worker_proc(self):
        # FIXME: track inflight tasks
        #
        # orb = self._work_queue._recv_bytes
        #
        # def rblog():
        #     res = orb()
        #     notprint(f'WORKER WORK QUEUE GOT {len(res)} bytes')
        #     return res
        #
        # self._work_queue._recv_bytes = rblog
        #
        # result_send = self._result_queue._writer._send_bytes
        #
        # def resultsendlog(obj):
        #     notprint(f'WORKER RESULT QUEUE SENDING {len(obj)} bytes')
        #     result_send(obj)
        #
        # self._result_queue._writer._send_bytes = resultsendlog
        # self._result_queue._send_bytes = resultsendlog
        #
        threading.Thread(target=self._shutdown_threadproc).start()

        #while not self._shutdown_event.is_set():
        while not self._should_stop:
            try:
                first_task_time = datetime.now()
                start = datetime.now()
                #task = await asyncio.get_event_loop().run_in_executor(None, self._work_queue.get, False)
                task = self._work_queue.get(block=False)
                notprint(f'got a task for host {task.host}, qlen is {self._work_queue.qsize()}, took {(datetime.now()-start)/timedelta(milliseconds=1)}ms')
            except queue.Empty:
                await asyncio.sleep(0.001)
                continue

            # FIXME: hang onto the task and track it
            asyncio.create_task(self._exec_task(task))
            notprint(f'task created for host {task.host}')

        notprint("*** WORKER PROC EXITED LOOP")

    async def _exec_task(self, task: WorkerTask):
        try:
            start = datetime.now()
            opts = asyncssh.SSHClientConnectionOptions(username=task.worker_task_vars['ansible_user'], password=task.worker_task_vars['ansible_password'], known_hosts=None)
            notprint(f'connecting {task.host}')
            async with asyncssh.connect(task.worker_task_vars['ansible_host'], port=int(task.worker_task_vars.get('ansible_port', 22)), options=opts) as conn:
                notprint(f'CONNECTED {task.host}')
                res: asyncssh.SSHCompletedProcess = await conn.run(task.task_data['args']['_raw_params'])
                notprint(f'WORKER DONE {task.host}')
                return_data = dict(changed=True, stdout=res.stdout, stderr=res.stderr, rc=res.returncode)

                # FIXME: handle full retry, set block False
                # FIXME: smuggle host context and other necessary data over in a proper wrapper

        # FIXME: propagate connection failures properly
        except Exception as e:
            notprint(f'BANG: {e}')
            return_data = dict(failed=True, msg=f"didn't work: {e}", exception=format_exc())

        notprint(f'worker is queuing result for {task.host}; took {(datetime.now()-start).total_seconds()}s')
        tr = TaskResult(task.host, task.uuid, return_data=return_data, task_fields=task.task_data)
        asyncio.get_event_loop().run_in_executor(None, self._result_queue.put, tr, True)
        #self._result_queue.put(tr, block=False)
        #asyncio.get_event_loop().run_in_executor(None, print, f'*** queued for {task.host}')
        notprint(f'*** queued for {task.host}')


class DummyExternalWorker:
    _worker = None
    _workqueues = None
    _ctx = None
    _selector = None
    @classmethod
    def query_worker_task_keys(cls, task):
        return ['ansible_connection', 'ansible_user', 'ansible_password', 'ansible_port', 'ansible_host']

    @classmethod
    def queue_task(cls, final_q, shutdown_event, task, worker_task_vars):
        if not cls._workqueues:
            NUM_WORKERS = 1
            cls._selector = itertools.cycle(range(0, NUM_WORKERS))
            cls._ctx = multiprocessing.get_context('spawn')
            cls._workqueues = []


            workerstarts = []
            #cls._workqueue = cls._ctx.Queue()
            for wq in range(0, NUM_WORKERS):
                newqueue = cls._ctx.Queue()
                # FIXME: figure out safest way to clone running collection config/loader setup into the spawned worker (vs recalculating)
                worker_config = AnsibleWorkerConfig(collection_path=['foo', 'bar'],
                                                    shutdown_event=shutdown_event,
                                                    result_queue=final_q)
                newworker = AnsibleAsyncHostWorker(worker_config=worker_config, work_queue=newqueue)
                workerstarts.append(threading.Thread(target=newworker.start))
                workerstarts[-1].start()
                cls._workqueues.append(newqueue)

            for t in workerstarts:
                t.join()

        cls._workqueues[next(cls._selector)].put(WorkerTask(task_data=dict(args=dict(_raw_params=task.args['_raw_params'])), host=task._host.name, uuid=task._uuid, worker_task_vars=worker_task_vars), block=True)




