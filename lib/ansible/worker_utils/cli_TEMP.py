#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
# launcher, hosts 0-N spawned workers, farms out tasks, gathers results
# accepts basic inventory yaml (dict of hosts -> vars)

import argparse
import dataclasses
import json
import os
import os.path
import pathlib
import random
import uuid
import shutil
import time
import traceback
import typing as t
import logging

from multiprocessing import get_context
mp = get_context('spawn')

import argcomplete

from threading import Thread

from ansible.worker_utils.action import AsyncAction
from ansible.worker_utils.become import AsyncBecome
from ansible.worker_utils.connection import AsyncConnection
from ansible.worker_utils.inventory import get_inventory
from ansible.worker_utils.worker_pool import WorkerPool
from ansible.worker_utils.plugin_manager import get_plugin_type, BasePlugin
from ansible.worker_utils.message import (
    ActionRequest, BaseTask, BaseTaskRequest, TaskResult, TaskOptions,
    ContentDescriptorRequest, ExecCommandRequest, PutFileRequest, FetchFileRequest, WorkerRequest
)
from ansible.worker_utils.exec import Exec


PYTHON_COMMAND_STDIN = 'python'

PWSH_COMMAND_STDIN = r"""
[CmdletBinding()]
param (
    [Parameter(ValueFromPipeline)]
    [byte[]]
    $InputObject
)

process {
    Invoke-Expression ([System.Text.Encoding]::UTF8.GetString($InputObject))
}
""".strip()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@dataclasses.dataclass(frozen=True)
class CliArgs:
    inventory: str


def parse_args() -> CliArgs:
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory')

    argcomplete.autocomplete(parser)

    args_ns = parser.parse_args()
    args_dict = {field.name: getattr(args_ns, field.name) for field in dataclasses.fields(CliArgs)}
    args = CliArgs(**args_dict)

    return args


@dataclasses.dataclass
class HostDetails:
    variables: dict[str, str]
    task_options: dict[str, str]


def get_host_plugin_from_type(
        name: str,
        variables: dict[str, str],
        optional: bool = False,
) -> t.Optional[t.Type[BasePlugin]]:
    # FIXME: Find a way to make this more dynamic and metadata driven
    plugin_type_meta: dict[str, t.Type[BasePlugin]] = {
        'become': AsyncBecome,
        'connection': AsyncConnection,
    }

    if not (plugin_type := plugin_type_meta.get(name, None)):
        raise Exception(f"Unknown plugin type {name}")

    ansible_var = plugin_type.ansible_variable_name
    if plugin_name := variables.get(ansible_var, None):
        return get_plugin_type(plugin_name=plugin_name, plugin_type=plugin_type)

    if optional:
        return None
    else:
        raise Exception(f"{name} plugin var {ansible_var} is not defined on host")


def prepare_task_options(
        hostname: str,
        host_vars: dict[str, str],
        plugin: str,
        plugin_type: t.Type[BasePlugin],
) -> TaskOptions:
    used_plugin_type = get_plugin_type(plugin_name=plugin, plugin_type=plugin_type)
    used_plugin_names = set()
    pending_plugin_names: set[str] = set()

    task_options = TaskOptions(
        plugins={
            plugin_type.ansible_plugin_type: plugin,
        },
    )

    task_options.plugin_options[plugin] = make_plugin_options(used_plugin_type, host_vars)

    new_plugin_names = used_plugin_type.uses_plugin_type_names - used_plugin_names
    pending_plugin_names.update(new_plugin_names)

    while pending_plugin_names:
        used_plugin_name = pending_plugin_names.pop()
        used_plugin_names.add(used_plugin_name)
        # FIXME: Really ugly hack to make become plugins optional, need to do this better
        used_plugin_type = get_host_plugin_from_type(used_plugin_name, host_vars, optional=used_plugin_name == 'become')

        if not used_plugin_type:
            continue

        # FIXME: Error if plugin type has already been processed - something has gone very wrong
        task_options.plugins[used_plugin_type.ansible_plugin_type] = used_plugin_type.fqname
        task_options.plugin_options[used_plugin_type.fqname] = make_plugin_options(used_plugin_type, host_vars)

        new_plugin_names = used_plugin_type.uses_plugin_type_names - used_plugin_names
        pending_plugin_names.update(new_plugin_names)

    return task_options


def make_plugin_options(plugin_type, host_vars):
    plugin_options = {}
    for plugin_option_name, host_var_names in plugin_type.plugin_options.items():
        for host_var_name in host_var_names:
            if host_var := host_vars.get(host_var_name):
                plugin_options[plugin_option_name] = host_var
                break

        else:
            raise Exception(f"Missing plugin option value '{plugin_option_name}' for '{plugin_type.fqname}'")

    return plugin_options


def prepare_temp() -> None:
    temp_path = pathlib.Path(os.path.dirname(__file__)) / 'temp'

    shutil.rmtree(temp_path, ignore_errors=True)

    for dirname in ['content', 'error', 'lock']:
        os.makedirs(temp_path / 'blobstore' / dirname)


def main() -> None:
    prepare_temp()

    logging.basicConfig(
        filename='temp/debug.log',
        encoding='utf-8',
        format="%(asctime)s %(filename)s:%(lineno)s %(funcName)s() %(message)s",
    )

    args = parse_args()

    logger.debug('args: %s' % args)

    inventory = get_inventory(args.inventory)

    tm = TaskManager()
    tm.start()

    try:
        # hello_raw(tm, inventory)
        hello_module(tm, inventory, binary=False)
        # hello_module(tm, inventory, binary=True)
        # run_module(tm, inventory, 'targetsudo', {
        #     'become_method': 'sudo',
        #     'requires_tty': True,
        # })
        # file_upload(tm, inventory)
        # file_download(tm, inventory)
        # hello_world(tm, inventory)
        wait_for_results(tm)
    except:
        logger.fatal(f'%s', traceback.format_exc())
        raise

    finally:
        tm.shutdown()


# FIXME: transplant into
class TaskManager:
    def __init__(self):
        self._running_tasks: dict[uuid.UUID, BaseTask] = {}
        self._result_queue = mp.Queue()
        self._pools_by_worktype: dict[str, WorkerPool] = {}
        #self._ahw = ActionHostWorker(self._result_queue)

        # self._workers: t.Dict[?, ActionHostWorker] = {}

        self._relayed_tasks: dict[uuid.UUID, WorkerPool] = {}

    def start(self) -> None:
        pass

    def _get_pool_for_task(self, task: BaseTask) -> WorkerPool:
        # FIXME: figure out how to make this not require an if-parade
        #        perhaps we query the plugin handling the message to get pool details
        if isinstance(task, ContentDescriptorRequest):
            pool_type = 'content'
            pool_options = dict(max_workers=10)  # non-async cpu-bound worker, one worker per concurrent task needed
        elif isinstance(task, BaseTaskRequest):
            pool_type = f"connection-{task.task_options.plugins['connection']}"
            pool_options = dict(max_workers=1, supports_concurrent_tasks=True)  # async worker, only one worker needed per connection type
        else:
            raise NotImplementedError(f"I don't know how to get a pool for task: {task}")

        # FIXME: not async or thread safe
        if not (pool := self._pools_by_worktype.get(pool_type, None)):
            pool = WorkerPool(pool_type, self._result_queue, **pool_options)
            self._pools_by_worktype[pool_type] = pool

        return pool

    def queue(self, task: BaseTask, *, track: bool = True) -> None:
        # FIXME: lazily make a local worker thread
        pool = self._get_pool_for_task(task)
        pool.queue(task)

        if track:
            self._running_tasks[task.task_id] = task

    def get(self) -> t.Optional[BaseTask]:
        if not self._running_tasks:
            return None

        while True:
            result: BaseTask = self._result_queue.get(block=True)
            pool_type, task = result

            if task.task_id in self._running_tasks:
                return task

            if task and (relay_pool := self._relayed_tasks.pop(task.task_id, None)):
                relay_pool.queue(task)
            else:
                self._relayed_tasks[task.task_id] = self._pools_by_worktype[pool_type]
                self.queue(task, track=False)

    def get_original_task(self, task: BaseTask) -> BaseTask:
        return self._running_tasks.get(task.task_id, None)

    def finish(self, task_id: uuid.uuid4) -> None:
        del self._running_tasks[task_id]

    def shutdown(self):
        for worker in self._pools_by_worktype.values():
            # FIXME: async and wait for all workers to shutdown, then kill any workers still hanging out
            worker.stop()


def hello_raw(tm: TaskManager, inventory: dict[str, dict[str, str]]) -> None:
    for hostname, variables in inventory.items():
        action = 'ansible.worker_utils.action.raw'
        task_options = prepare_task_options(hostname, variables, action, AsyncAction)

        request = ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args={'command': 'echo "hello from raw, I am $(whoami) on tty $(tty 2>&1)"'},
        )

        task_context[request.task_id] = TaskContext(hostname=hostname)

        tm.queue(request)


def run_module(tm: TaskManager, inventory: dict[str, dict[str, str]], name: str, options: dict) -> None:
    for hostname, variables in inventory.items():
        ansible_shell = variables.get('ansible_shell', 'sh')
        action_args = {}

        if ansible_shell == 'sh':
            action = 'ansible.worker_utils.action.module_python'
            action_args.update(module=name, options=options)
        else:
            raise Exception(f'bad shell: {ansible_shell}')

        task_options = prepare_task_options(hostname, variables, action, AsyncAction)

        request = ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args=action_args,
        )

        task_context[request.task_id] = TaskContext(hostname=hostname)

        tm.queue(request)


def hello_module(tm: TaskManager, inventory: dict[str, dict[str, str]], binary: bool) -> None:
    for hostname, variables in inventory.items():
        ansible_shell = variables.get('ansible_shell', 'sh')
        action_args = {}

        if not binary:
            if ansible_shell == 'sh':
                action = 'ansible.worker_utils.action.module_python'
                action_args.update(module='not_a_real_module', options={})
            elif ansible_shell == 'powershell':
                action = 'ansible.worker_utils.action.module_powershell'
                action_args.update(module='not_a_real_module', options={})
            else:
                raise Exception(f'bad shell: {ansible_shell}')
        else:
            if ansible_shell == 'sh':
                action = 'ansible.worker_utils.action.module_posix_binary'
                action_args.update(module='helloworld_linux_x86_64', options={})
            elif ansible_shell == 'powershell':
                action = 'ansible.worker_utils.action.module_windows_binary'
                action_args.update(module='helloworld_win32nt_64-bit.exe', options={})
            else:
                raise Exception(f'bad shell: {ansible_shell}')

        task_options = prepare_task_options(hostname, variables, action, AsyncAction)

        request = ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args=action_args,
        )

        task_context[request.task_id] = TaskContext(hostname=hostname)

        tm.queue(request)


@dataclasses.dataclass(frozen=True)
class TaskContext:
    hostname: str


task_context: dict[uuid.UUID, TaskContext] = {}


def hello_world(tm: TaskManager, inventory: dict[str, dict[str, str]]) -> None:
    for cmd_num in range(5):
        for hostname, variables in inventory.items():
            details = prepare_task_options(hostname, variables)

            ansible_shell = details.variables['ansible_shell']

            if ansible_shell == 'sh':
                # command = f'echo {cmd_num} && echo $PPID'  # && sleep {random.randint(0, 2)}'
                command = PYTHON_COMMAND_STDIN
                stdin_key = 'payload.py'
            elif ansible_shell == 'powershell':
                # Start-Sleep {random.randint(0, 2)}'
                # command = f'echo {cmd_num}; [Runspace]::DefaultRunspace.InstanceId'
                command = PWSH_COMMAND_STDIN
                stdin_key = 'payload.ps1'
            else:
                raise Exception(f'bad shell: {ansible_shell}')

            request = ExecCommandRequest(
                task_id=uuid.uuid4(), task_options=details.task_options, cmd=command,
                stdin_key=stdin_key,
            )

            tm.queue(request)


def file_upload(tm: TaskManager, inventory: dict[str, dict[str, str]]) -> None:
    for hostname, variables in inventory.items():
        action = 'ansible.worker_utils.action.upload'
        task_options = prepare_task_options(hostname, variables, action, AsyncAction)

        ansible_shell = variables['ansible_shell']

        src_path = pathlib.Path(__file__).parent / 'worker_utils/files/payload.py'

        dst_path: str
        if ansible_shell == 'sh':
            dst_path = f'/tmp/{hostname}-payload.py'
        elif ansible_shell == 'powershell':
            dst_path = fr'C:\TEMP\{hostname}-payload.py'
        else:
            raise Exception(f"Unknown shell type {ansible_shell}")

        request = ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args={
                'src': str(src_path),
                'dst': dst_path,
            },
        )

        tm.queue(request)


def file_download(tm: TaskManager, inventory: dict[str, dict[str, str]]) -> None:
    for hostname, variables in inventory.items():
        action = 'ansible.worker_utils.action.download'
        task_options = prepare_task_options(hostname, variables, action, AsyncAction)

        ansible_shell = variables['ansible_shell']

        if ansible_shell == 'sh':
            src_path = f'/tmp/{hostname}-payload.py'
            dst_path = f'/tmp/download-{hostname}-payload.py'
        elif ansible_shell == 'powershell':
            src_path = fr'C:\TEMP\{hostname}-payload.py'
            dst_path = f'/tmp/download-{hostname}-payload.py'
        else:
            raise Exception(f'bad shell: {ansible_shell}')

        request = ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args=dict(
                src=src_path,
                dst=dst_path,
            ),
        )

        tm.queue(request)


def wait_for_results(tm: TaskManager) -> None:
    while task := tm.get():
        original_task = tm.get_original_task(task)
        finish_task = True

        logger.debug('received task %s in response to task %s', {type(task)}, {type(original_task)})

        if isinstance(original_task, ExecCommandRequest):
            if len(original_task.cmd) > 20:
                label = original_task.cmd[:20] + '...'
            else:
                label = original_task.cmd

            label = label.replace('\n', r'\n')
        elif isinstance(original_task, PutFileRequest):
            label = f'"{original_task.src}" -> "{original_task.dst_path}"'
        elif isinstance(original_task, FetchFileRequest):
            label = f'"{original_task.src_path}" -> "{original_task.dst}"'
        elif isinstance(task, WorkerRequest):
            logger.debug('relaying worker task: %s', task.task_id)

            def queue_result(result: WorkerRequest) -> None:
                try:
                    duration = random.randint(4, 7)
                    time.sleep(duration)
                    logger.debug("%s sleep(%d) free: %s", result.task_id, duration, result.ping)
                    tm.queue(TaskResult(
                        task_id=result.task_id,
                        result={'ping': result.ping},
                    ), track=False)
                except Exception as e:
                    logger.error("%s failure - %s", result.task_id, e)

            Thread(target=queue_result, args=(task,), daemon=True).start()
            continue

        else:
            label = type(original_task)

        inventory_hostname = task_context[original_task.task_id].hostname
        result = getattr(task, "result", {})

        logger.debug('result [%s@%s] (%s) %s',
                     task.task_id,
                     inventory_hostname,
                     label,
                     result)

        print(f"[{inventory_hostname}] {json.dumps(result)}")

        if finish_task:
            tm.finish(task.task_id)


if __name__ == '__main__':
    main()
