# Copyright 2012, Dag Wieers <dag@wieers.com>
# Copyright 2016, Toshio Kuratomi <tkuratomi@ansible.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import annotations

from ansible.errors import AnsibleValueOmittedError
from ansible.module_utils.datatag import AnsibleTaggedObject
from ansible.module_utils.datatag.access import SensitiveDataMask
from ansible.module_utils.common.text.converters import to_text
from ansible.plugins.action import ActionBase
from ansible.template import Omit


class ActionModule(ActionBase):
    ''' Print statements during execution '''

    TRANSFERS_FILES = False
    _requires_connection = False

    def run(self, tmp=None, task_vars=None):
        with SensitiveDataMask():
            if task_vars is None:
                task_vars = dict()

            best_effort = self._templar.BestEffort()

            argument_spec = {
                'msg': {'type': 'raw', 'default': 'Hello world!'},
                'var': {'type': 'str_no_conversion'},
                'verbosity': {'type': 'int', 'default': 0},
            }

            custom_undefined_handlers = dict(msg=best_effort, var=best_effort)

            # special omit handling
            for arg_name in argument_spec:
                if (arg := self._task.args.get(arg_name, Omit)) is Omit:
                    continue

                undefined_handler = custom_undefined_handlers.get(arg_name, None)

                try:
                    result = self._templar.template_with_result(arg, undefined_behavior=undefined_handler)
                except AnsibleValueOmittedError:
                    self._task.args.pop(arg_name)
                    continue

                self._task.args[arg_name] = result.result

            validation_result, new_module_args = self.validate_argument_spec(
                argument_spec=argument_spec,
                mutually_exclusive=(
                    ('msg', 'var'),
                ),
            )

            result = super(ActionModule, self).run(tmp, task_vars)
            del tmp  # tmp no longer has any effect

            # get task verbosity
            verbosity = new_module_args['verbosity']

            if verbosity <= self._display.verbosity:
                if raw_var_arg := new_module_args['var']:
                    # If var name is same as result, try to template it
                    # FIXME: preserve AnsibleSourcePosition, SensitiveData, others?
                    template_wrapped_arg = AnsibleTaggedObject.tag_copy(raw_var_arg, "{{" + raw_var_arg + "}}")

                    try:
                        template_result = self._templar.template_with_result(template_wrapped_arg, undefined_behavior=best_effort)
                    except AnsibleValueOmittedError:
                        results = repr(Omit)
                        result.setdefault('warnings', []).append(f"The result of expression {raw_var_arg!r} could not be omitted; a placeholder was used instead.")
                    else:
                        results = template_result.result

                    # handle the corner case where the input was untrusted- if so, return the raw input, not the
                    # generated template
                    if results == template_wrapped_arg:
                        results = raw_var_arg

                    result[raw_var_arg] = results

                else:
                    result['msg'] = new_module_args['msg']

                # force flag to make debug output module always verbose
                result['_ansible_verbose_always'] = True

                # propagate any undefined warnings in the task result unless we're skipping the task
                if best_effort.has_warnings:
                    result.setdefault('warnings', []).extend(best_effort.warnings())
            else:
                result['skipped_reason'] = "Verbosity threshold not met."
                result['skipped'] = True

            result['failed'] = False

            return result
