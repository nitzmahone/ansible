# Copyright 2012, Dag Wieers <dag@wieers.com>
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

import typing as t

from ansible.errors import AnsibleValueOmittedError, AnsibleError
from ansible.module_utils.common.validation import check_type_list_that_does_not_suck_FIXME
from ansible.plugins.action import ActionBase
from ansible.template.templar import TemplateMode


class ActionModule(ActionBase):
    """Assert that one or more conditional expressions evaluate to true."""

    _requires_connection = False
    FIXME_DOES_OWN_TEMPLATING = True

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super(ActionModule, self).run(tmp, task_vars)
        del tmp  # tmp no longer has any effect

        # since we need raw args, template everything normally except `that`
        for arg_name, arg in list(self._task.args.items()):
            if arg_name == 'that':
                if not isinstance(arg, str):
                    # if `that` is not a string, we don't need to attempt to resolve it as a template before validation (which will also listify it)
                    continue
                # if `that` is a string that might be a template, we only want to resolve to the container and avoid templating the container contents
                mode = TemplateMode.STOP_ON_CONTAINER
            else:
                mode = TemplateMode.DEFAULT

            try:
                templated_arg = self._templar.template(arg, mode=mode)
            except AnsibleValueOmittedError:
                self._task.args.pop(arg_name)
                continue
            except Exception as ex:
                raise AnsibleError(f'Error while templating arg {arg_name!r}.', obj=arg) from ex

            # if `that` is a non-list, restore the original input (template or container)
            if arg_name == "that" and not isinstance(templated_arg, list):
                # DTFIX-U: ensure that we get a warning below because of template instead of expression
                templated_arg = arg

            self._task.args[arg_name] = templated_arg

        validation_result, new_module_args = self.validate_argument_spec(
            argument_spec=dict(
                fail_msg=dict(type=str_or_list_of_str, aliases=['msg'], default='Assertion failed'),
                success_msg=dict(type=str_or_list_of_str, default='All assertions passed'),
                quiet=dict(type='bool', default=False),
                # explicitly not validating types `elements` here to let type rules for conditionals apply
                that=dict(type=check_type_list_that_does_not_suck_FIXME, required=True),
            ),
        )

        fail_msg = new_module_args['fail_msg']
        success_msg = new_module_args['success_msg']
        quiet = new_module_args['quiet']
        thats = new_module_args['that']

        if not quiet:
            result['_ansible_verbose_always'] = True

        for that in thats:
            test_result = self._templar.evaluate_conditional(conditional=that)
            if not test_result:
                result['failed'] = True
                result['evaluated_to'] = test_result
                result['assertion'] = that

                result['msg'] = fail_msg

                return result

        result['changed'] = False
        result['msg'] = success_msg
        return result


def str_or_list_of_str(value: t.Any) -> str | list[str]:
    if isinstance(value, str):
        return value

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError("a string or list of strings is required")

    return value
