from __future__ import annotations

from queue import Queue

import typing as t

from ansible.worker_utils.message import ActionRequest, TaskResult

_send_queue: t.Optional[Queue[ActionRequest]] = None
_recv_queue: t.Optional[Queue[TaskResult]] = None


def dispatch(msg: ActionRequest) -> TaskResult:
    _send_queue.put(msg)
    return _recv_queue.get()
