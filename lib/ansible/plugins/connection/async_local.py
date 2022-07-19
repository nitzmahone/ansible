# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
# (c) 2015, 2017 Toshio Kuratomi <tkuratomi@ansible.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    name: async_local
    short_description: execute on controller
    description:
        - This connection plugin allows ansible to execute tasks on the Ansible 'controller' instead of on a remote host.
    author: ansible (@core)
    version_added: historical
    extends_documentation_fragment:
        - connection_pipelining
    notes:
        - The remote user is ignored, the user with which the ansible CLI was executed is used instead.
'''

import getpass
import uuid
import typing as t

from ansible.plugins.connection import ConnectionBase
from ansible.utils.display import Display

from ansible.executor.process import controller_queue
from ansible.worker_utils.message import ActionRequest, TaskOptions


display = Display()


class Connection(ConnectionBase):
    ''' Local based connections '''

    transport = 'local'
    has_pipelining = True

    def __init__(self, *args, **kwargs):

        super(Connection, self).__init__(*args, **kwargs)
        self.cwd = None
        self.default_user = getpass.getuser()

    def _connect(self):
        ''' connect to the local host; nothing to do here '''

        # Because we haven't made any remote connection we're running as
        # the local user, rather than as whatever is configured in remote_user.
        self._play_context.remote_user = self.default_user

        if not self._connected:
            display.vvv(u"ESTABLISH LOCAL CONNECTION FOR USER: {0}".format(self._play_context.remote_user), host=self._play_context.remote_addr)
            self._connected = True
        return self

    def exec_command(self, cmd, in_data=None, sudoable=True):
        ''' run a command on the local host '''

        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        request = self._generate_async_request(
            'ansible.worker_utils.action._exec_command_internal',
            cmd=cmd,
            in_data=in_data,
            sudoable=sudoable,
        )
        resp = controller_queue.dispatch(request)
        return resp.result['rc'], resp.result['stdout'], resp.result['stderr']

    def put_file(self, in_path, out_path):
        ''' transfer a file from local to local '''

        super(Connection, self).put_file(in_path, out_path)

        request = self._generate_async_request(
            'ansible.worker_utils.action._put_file_internal',
            in_path=in_path,
            out_path=out_path,
        )
        controller_queue.dispatch(request)

    def fetch_file(self, in_path, out_path):
        ''' fetch a file from local to local -- for compatibility '''

        super(Connection, self).fetch_file(in_path, out_path)

        request = self._generate_async_request(
            'ansible.worker_utils.action._fetch_file_internal',
            in_path=in_path,
            out_path=out_path,
        )
        controller_queue.dispatch(request)

    def close(self):
        ''' terminate the connection; nothing to do here '''
        self._connected = False

    def _generate_async_request(self, action: str, **kwargs: t.Any) -> ActionRequest:
        # FIXME: Somehow generate this dynamically like cli.py does.
        task_options = TaskOptions(
            plugins={
                'connection': 'ansible.worker_utils.connection.async_local',
            },
            plugin_options={
                'ansible.worker_utils.connection.async_local': {},
            },
        )

        return ActionRequest(
            task_id=uuid.uuid4(),
            task_options=task_options,
            action=action,
            action_args=kwargs,
        )
