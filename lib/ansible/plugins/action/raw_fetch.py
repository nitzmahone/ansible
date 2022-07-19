from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


from ansible.plugins.action import ActionBase
from ansible.errors import AnsibleActionSkip


class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):
        ''' handler for fetch operations '''
        if task_vars is None:
            task_vars = dict()

        result = super(ActionModule, self).run(tmp, task_vars)
        if self._play_context.check_mode:
            raise AnsibleActionSkip('check mode not (yet) supported for this module')

        source = self._task.args.get('src', None)
        dest = self._task.args.get('dest', None)

        self._connection.fetch_file(source, dest)

        return result
