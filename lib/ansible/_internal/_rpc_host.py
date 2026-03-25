from __future__ import annotations

import dataclasses
import signal
import threading
import time
import typing as t

from ..module_utils._internal._concurrent._futures import DaemonThreadPoolExecutor
from multiprocessing.managers import BaseManager, Server
from multiprocessing.process import BaseProcess

if t.TYPE_CHECKING:
    from . import _task
    from ansible.executor.task_queue_manager import TaskQueueManager
    from ansible.plugins.strategy import StrategyBase

_mgr: LocalManager | None = None

tqm_instance_fixme: TaskQueueManager | None = None  # RPFIX-0: bikeshed name
strategy_instance_fixme: StrategyBase | None = None  # RPFIX-0: bikeshed name


class LocalProcess(BaseProcess):
    def __init__(self, *posargs, target, args, **kwargs):
        super().__init__(*posargs, **kwargs)

        self._args = args
        self._kwargs = kwargs
        self._target = target
        self._tpe = DaemonThreadPoolExecutor()

    def start(self):
        original_signal = signal.signal

        try:
            signal.signal = lambda *args, **kwargs: None  # RPFIX-0: restore or come up with another way

            self._tpe.submit(self._target, *self._args, **self._kwargs)

            _server_ready.wait(5)
        finally:
            signal.signal = original_signal


class LocalContext:
    Process = LocalProcess


_server_ready: threading.Event = threading.Event()


class LocalServer(Server):
    def serve_forever(self):
        _server_ready.set()
        return super().serve_forever()


class LocalManager(BaseManager):
    _current: t.Self | None = None

    def __init__(self, address=None, authkey=None, serializer='pickle', ctx=LocalContext, *, shutdown_timeout=1.0):
        type(self)._current = self  # HACK: ew

        super().__init__(address=address, authkey=authkey, serializer=serializer, ctx=ctx, shutdown_timeout=shutdown_timeout)

    @property
    def authkey(self) -> bytes:
        return self._authkey


@dataclasses.dataclass(kw_only=True)
class RpcRequest:
    event: threading.Event = dataclasses.field(default_factory=threading.Event)
    impl: t.Callable
    args: tuple
    kwargs: dict[str, object]

    _response: object = None
    _exception: BaseException | None = None

    @property
    def result(self) -> object:
        if self._exception:
            raise self._exception

        return self._response

    def dispatch(self) -> None:
        try:
            self._response = self.impl(*self.args, **self.kwargs)
        except BaseException as ex:
            self._exception = ex

        self.event.set()


def do_add_host(host_info: _task.AddHost) -> bool:
    changed = tqm_instance_fixme._inventory.add_dynamic_host(host_info)

    if changed and host_info.host_name not in strategy_instance_fixme._hosts_cache_all:
        strategy_instance_fixme._hosts_cache_all.append(host_info.host_name)

    return changed


def do_add_group(host_name: str, group_info: _task.AddGroup) -> bool:
    return tqm_instance_fixme._inventory.add_dynamic_group(host_name, group_info)


class InventoryGooFixme:
    def add_host(self, *args, **kwargs) -> object:
        return self._dispatch(impl=do_add_host, *args, **kwargs)

    def add_group(self, *args, **kwargs) -> object:
        return self._dispatch(impl=do_add_group, *args, **kwargs)

    def _dispatch(self, *args, impl: t.Callable, **kwargs) -> object:
        request = RpcRequest(impl=impl, args=args, kwargs=kwargs)

        strategy_instance_fixme._rpc_queue.put(request)

        request.event.wait()

        return request.result


_inventory: InventoryGooFixme = InventoryGooFixme()  # RPFIX-0: bikeshed name


def init() -> None:
    global _mgr

    if _mgr:
        return

    LocalManager.register("InventoryGooFixme", lambda: _inventory)  # RPFIX-0: bikeshed name

    _mgr = LocalManager()
    _mgr.start()
