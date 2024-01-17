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

import traceback

from ansible.errors import AnsibleError, AnsibleValueOmittedError
from ansible.module_utils.datatag import AnsibleTaggedObject, NotATemplate
from ansible.plugins.action import ActionBase
from ansible.template import Omit, BestEffort


class ActionModule(ActionBase):
    ''' Print statements during execution '''

    TRANSFERS_FILES = False
    _requires_connection = False

    def run(self, tmp=None, task_vars=None):
        best_effort = BestEffort()

        raw_task_args = self._task.untemplated_args

        # template splatted `args` only until we get a dictionary
        if vp := raw_task_args.pop('_variable_params', None):
            raw_task_args = self._templar.template(vp, stop_on_container_result=True, value_for_omit={})

            if not isinstance(raw_task_args, dict):
                # FIXME: needs AnsibleTaggedObject.get_native_type() to avoid displaying internal type names
                raise AnsibleError(NotATemplate().tag(f"variable args {vp!r} resolved to a {type(raw_task_args)!r} instead of a dict"))

            # merge any explicitly-defined args on top of splatted args, then put them back on untemplated_args
            # FIXME: or not put them back?
            raw_task_args.update(self._task.untemplated_args)

        argument_spec = {
            'msg': {'type': 'raw', 'default': 'Hello world!'},
            'var': {'type': 'str_no_conversion'},
            'verbosity': {'type': 'int', 'default': 0},
        }

        custom_undefined_handlers = dict(msg=best_effort, var=best_effort)

        # special omit handling; we're popping omitted items, so need to iterate a static copy
        for arg_name, arg in list(raw_task_args.items()):
            undefined_handler = custom_undefined_handlers.get(arg_name, None)

            try:
                result = self._templar.template(arg, undefined_behavior=undefined_handler)
            except AnsibleValueOmittedError:
                raw_task_args.pop(arg_name)
                continue
            except Exception as ex:
                return dict(
                    # FIXME: better error message and location?
                    msg=NotATemplate().tag(f'Error while templating arg {arg!r}: {ex}'),
                    exception=NotATemplate().tag(str(traceback.format_exc())),
                    failed=True,
                )

            raw_task_args[arg_name] = result

        self._task.args = raw_task_args

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
                # FIXME: preserve AnsibleSourcePosition, others?
                template_wrapped_arg = AnsibleTaggedObject.tag_copy(raw_var_arg, "{{" + raw_var_arg + "}}")

                try:
                    template_result = self._templar.template_with_result(template_wrapped_arg, undefined_behavior=best_effort)
                except AnsibleValueOmittedError:
                    results = repr(Omit)
                    result.setdefault('warnings', []).append(
                        f"The result of expression {raw_var_arg!r} could not be omitted; a placeholder was used instead.")
                except Exception as ex:
                    return dict(
                        # FIXME: better error message and location?
                        msg=NotATemplate().tag(f'Error while templating variable expression {raw_var_arg!r}: {ex}'),
                        exception=NotATemplate().tag(str(traceback.format_exc())),
                        failed=True,
                    )
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
