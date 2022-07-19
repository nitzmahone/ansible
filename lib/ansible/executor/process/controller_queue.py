from __future__ import annotations

import dataclasses
from queue import Queue

import typing as t

from ansible.worker_utils.message import ActionRequest, TaskResult

_work_proc_id: int = 0
_send_queue: t.Optional[Queue[ForkedWorkerRequest]] = None
_recv_queue: t.Optional[Queue[t.Union[Exception, TaskResult]]] = None


@dataclasses.dataclass(frozen=True)
class ForkedWorkerRequest:
    worker_id: int
    request: ActionRequest


def dispatch(msg: ActionRequest) -> TaskResult:
    # NB: this has no request correlation and assumes that the first response is only for the request we sent
    _send_queue.put(ForkedWorkerRequest(worker_id=_work_proc_id, request=msg))
    resp = _recv_queue.get()
    if isinstance(resp, Exception):
        raise resp

    return resp
