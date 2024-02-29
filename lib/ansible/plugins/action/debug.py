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

from ansible.errors import AnsibleValueOmittedError
from ansible.module_utils.common.validation import check_type_str_no_conversion
from ansible.module_utils.datatag import AnsibleTaggedObject, NotATemplate
from ansible.plugins.action import ActionBase
from ansible.template.templar import TemplateOptions
from ansible.template.utils import Omit
from ansible.template.undefined_behaviors import ReplaceUndefined, FAIL_ON_UNDEFINED


class ActionModule(ActionBase):
    ''' Print statements during execution '''

    TRANSFERS_FILES = False
    _requires_connection = False
    FIXME_DOES_OWN_TEMPLATING = True

    def run(self, tmp=None, task_vars=None):
        # FIXME: we need more consistent error handling, either all failures should be ignored or none of them
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
                return dict(
                    # FIXME: better error message and location?
                    msg=NotATemplate().tag(f'Error while templating arg {arg_name!r} containing {arg!r}: {ex}'),
                    exception=NotATemplate().tag(str(traceback.format_exc())),
                    failed=True,  # FIXME: should debug fail in this case?
                )

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
                    results = self._templar.template(template_wrapped_arg, options=TemplateOptions(undefined_behavior=replace_undefined))
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

                # FIXME: how should debug handle the case of var being a template?
                #        if the template results in an undefined value, the ReplaceUndefined behavior makes the result even more confusing
                #        it seems like at a minimum, a warning about not using templates for `var` would be appropriate
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
            if replace_undefined.has_warnings:
                result.setdefault('warnings', []).extend(replace_undefined.warnings())
        else:
            result['skipped_reason'] = "Verbosity threshold not met."
            result['skipped'] = True

        result['failed'] = False

        return result
