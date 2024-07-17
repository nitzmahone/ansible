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

from ansible.errors import AnsibleValueOmittedError, AnsibleError
from ansible.module_utils.common.validation import check_type_str_no_conversion
from ansible.plugins.action import ActionBase
from ansible.template.templar import TemplateOptions, TemplateMode, TemplateTrustCheckFailedError
from ansible.template.utils import Omit
from ansible.template.undefined_behaviors import ReplaceUndefined, FAIL_ON_UNDEFINED
from ansible.utils.display import Display


display = Display()


class ActionModule(ActionBase):
    ''' Print statements during execution '''

    TRANSFERS_FILES = False
    _requires_connection = False
    DOES_OWN_TEMPLATING = True

    def run(self, tmp=None, task_vars=None):
        # DTFIX-U: we need more consistent error handling, either all failures should be ignored or none of them
        replace_undefined = ReplaceUndefined()

        argument_spec = {
            'msg': {'type': 'raw', 'default': 'Hello world!'},
            'var': {'type': check_type_str_no_conversion},
            'verbosity': {'type': 'int', 'default': 0},
        }

        undefined_behaviors = dict(msg=replace_undefined, var=replace_undefined)

        # special omit handling; we're popping omitted items, so need to iterate a static copy
        for arg_name, arg in list(self._task.args.items()):
            undefined_behavior = undefined_behaviors.get(arg_name, FAIL_ON_UNDEFINED)

            try:
                self._task.args[arg_name] = self._templar.template(arg, options=TemplateOptions(undefined_behavior=undefined_behavior))
            except AnsibleValueOmittedError:
                self._task.args.pop(arg_name)
                continue
            except Exception as ex:
                raise AnsibleError(f'Error while templating arg {arg_name!r}.', obj=arg) from ex

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

                try:
                    results = self._templar.template(raw_var_arg, options=TemplateOptions(undefined_behavior=replace_undefined), mode=TemplateMode.EXPRESSION)
                except TemplateTrustCheckFailedError as ex:
                    results = raw_var_arg
                    display.error_as_warning("The `var` expression must be trusted.", exception=ex)
                except AnsibleValueOmittedError as ex:
                    results = repr(Omit)
                    display.warning("The result of the `var` expression could not be omitted; a placeholder was used instead.", obj=ex.obj)
                except Exception as ex:
                    raise AnsibleError('Error while templating variable expression.', obj=raw_var_arg) from ex

                # DTFIX-U: how should debug handle the case of var being a template?
                #        if the template results in an undefined value, the ReplaceUndefined behavior makes the result even more confusing
                #        it seems like at a minimum, a warning about not using templates for `var` would be appropriate

                result[raw_var_arg] = results

            else:
                result['msg'] = new_module_args['msg']

            # force flag to make debug output module always verbose
            result['_ansible_verbose_always'] = True

            # propagate any undefined warnings in the task result unless we're skipping the task
            replace_undefined.emit_warnings()

        else:
            result['skipped_reason'] = "Verbosity threshold not met."
            result['skipped'] = True

        result['failed'] = False

        return result
