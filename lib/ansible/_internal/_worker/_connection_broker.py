from __future__ import annotations

import abc
import os
import threading
import typing as _t

from collections import deque

from multiprocessing.managers import BaseManager

class KeyedConnection(metaclass=abc.ABCMeta):
    #@property
    @abc.abstractmethod
    def key(self) -> str: ...

    #@property
    @abc.abstractmethod
    def connected(self) -> bool: ...

    @classmethod
    @abc.abstractmethod
    def get_key(cls, conn_kwargs: dict[str, object]) -> str: ...

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def exec_command(self, *args, **kwargs) -> tuple[int, bytes, bytes]: ...

    @abc.abstractmethod
    def reset(self) -> None: ...

    @abc.abstractmethod
    def update_connection_options(self, **options: object) -> None: ...


# class BogusConnection(KeyedConnection):
#     def __init__(self, key: str):
#         print(f"new connection created for key {key} in manager PID {os.getpid()}")
#         self._key = key
#
#     def key(self) -> str:
#         return self._key
#
#     @property
#     def connected(self) -> bool:
#         return True
#
#     def do_stuff(self) -> None:
#         print(f"doing stuff for connection {self.key} in PID {os.getpid()}")
#
class ConnectionBroker:
    def __init__(self):
        self._connection_lock = threading.Lock()
        self._idle_connections: dict[str, deque[KeyedConnection]] = {}  # FIXME: better checkout system with attribution, builtin key/pool support
        self._busy_connections: dict[str, KeyedConnection] = {}

    def get_connection(self, conn_type: type[KeyedConnection], conn_kwargs: dict[str, object]) -> KeyedConnection:
        with self._connection_lock:
            key = conn_type.get_key(conn_kwargs)
            if (conns := self._idle_connections.get(key)) is None:
                conns = self._idle_connections[key] = deque()

            if conns:
                conn = None
                while not conn:
                    conn = conns.popleft()
                    if not conn.connected():
                        #print(f'dropping disconnected connection for key {key}')
                        continue
                    #print(f'using existing connection for key {key}')

            else:
                conn = conn_type(**conn_kwargs)
                #print(f'using new connection for key {key}')

            self._busy_connections[key] = conn

        return conn

    def release_connection(self, conn: KeyedConnection) -> None:
        key = conn.key()
        with self._connection_lock:
            try:
                del self._busy_connections[key]
            except KeyError:
                pass
                #print(f'ignoring connection release for key {key}')

            if not conn.connected:
                return

            if (conns := self._idle_connections.get(key)) is None:
                conns = self._idle_connections[key] = deque()

            #print(f'releasing connection for key {key}')
            conns.append(conn)


class ConnectionBrokerManager(BaseManager): ...

ConnectionBrokerManager.register('ConnectionBroker', ConnectionBroker, method_to_typeid=dict(get_connection='KeyedConnection'))
ConnectionBrokerManager.register('KeyedConnection', callable=None, create_method=False)
