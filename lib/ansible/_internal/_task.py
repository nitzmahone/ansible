from __future__ import annotations

import dataclasses
import typing as t

from collections import abc as c

from ansible import constants
from ansible._internal._templating import _engine
from ansible._internal._templating._chain_templar import ChainTemplar
from ansible._internal._templating._engine import TemplateEngine
from ansible.errors import AnsibleError, AnsibleUndefinedVariable
from ansible.module_utils._internal._ambient_context import AmbientContextBase
from ansible.module_utils.common.text.converters import to_text
from ansible.module_utils.datatag import native_type_name
from ansible.parsing import vault as _vault
from ansible.utils.display import Display

if t.TYPE_CHECKING:
    from ansible.playbook.task import Task


@dataclasses.dataclass
class TaskContext(AmbientContextBase):
    """Ambient context that wraps task execution on workers. It provides access to the currently executing task."""

    task: Task

    @property
    def is_loop(self) -> bool:
        return self._loop_items is not None

    @property
    def current_item(self) -> object:
        return self._item

    @property
    def current_item_label(self) -> object:
        return self.task.loop_control.label or self.task_templar.variable_name_as_template(self._loop_var)

    # these values are updated as items are processed and need to be grafted back onto the final task fields for backward compatibility
    _ignore_errors: bool = False
    _ignore_unreachable: bool = False

    _raw_loop_results: list[dict[str, t.Any]] = dataclasses.field(default_factory=list)
    _loop_items: list[object] | None = None
    _loop_var: str | None = None
    _item: object | None = None
    _item_index: int | None = None
    _index_var: str | None = None
    _task_vars: dict | None = None
    _templar: "TemplateEngine | None" = None

    @property
    def loop_result(self) -> dict[str, object]:
        return self._task_result_from_loop_results(self._raw_loop_results)

    def start_loop(self):
        task_vars = self._task_vars
        self._loop_var = loop_var = self.task.loop_control.loop_var
        index_var = self.task.loop_control.index_var
        extended = self.task.loop_control.extended
        extended_allitems = self.task.loop_control.extended_allitems
        items = self._loop_items
        items_len = len(items)

        for item_index, item in enumerate(self._loop_items):
            self._templar = None  # we're changing the values used to calculate the templar, null it out so the next requestor re-creates it

            self._item = item
            self._item_index = item_index

            task_vars['ansible_loop_var'] = loop_var

            task_vars[loop_var] = item
            if index_var:
                task_vars['ansible_index_var'] = index_var
                task_vars[index_var] = item_index

            if extended:
                task_vars['ansible_loop'] = {
                    'index': item_index + 1,
                    'index0': item_index,
                    'first': item_index == 0,
                    'last': item_index + 1 == items_len,
                    'length': items_len,
                    'revindex': items_len - item_index,
                    'revindex0': items_len - item_index - 1,
                }
                if extended_allitems:
                    task_vars['ansible_loop']['allitems'] = items
                try:
                    task_vars['ansible_loop']['nextitem'] = items[item_index + 1]
                except IndexError:
                    pass
                if item_index - 1 >= 0:
                    task_vars['ansible_loop']['previtem'] = items[item_index - 1]

            yield item_index, item

    @property
    def task_templar(self) -> TemplateEngine:
        if not self._templar:
            self._templar = TemplateEngine(loader=self.task._loader, variables=self._task_vars)

        return self._templar

    @property
    def most_recent_result_FIXME(self) -> dict[str, object]:
        return self._raw_loop_results[-1]

    def _record_result(self, result: dict[str, object]) -> None:
        if not TaskContext.current().is_loop:
            self._raw_loop_results.append(result)
            return

        # now update the result with the item info, and append the result
        # to the list of results
        result[self._loop_var] = self._item
        result['ansible_loop_var'] = self._loop_var
        if self._index_var:
            result[self._index_var] = self._item_index

            result['ansible_index_var'] = self._index_var
        if self.task.loop_control.extended:
            result['ansible_loop'] = self._task_vars['ansible_loop']

        result['_ansible_item_result'] = True
        result['_ansible_ignore_errors'] = self.task.ignore_errors  # FIXME: ensure that the task object the TaskContext sees is the post-validated/templated one
        result['_ansible_ignore_unreachable'] = self.task.ignore_unreachable  # FIXME: ensure that the task object the TaskContext sees is the post-validated/templated one

        # update the local copy of vars with the registered value, if specified,
        # or any facts which may have been generated by the module execution
        if self.task.register:
            from ansible.executor.task_executor import TaskExecutor  # FIXME: circular import?
            self._task_vars.update(TaskExecutor._project(self.task, self.task_templar, result))

        # gets templated here unlike rest of loop_control fields, depends on loop_var above
        try:
            result['_ansible_item_label'] = self.task_templar.template(self.task.loop_control.label or self.task_templar.variable_name_as_template(self._loop_var))
        except AnsibleUndefinedVariable as e:
            result.update({
                'failed': True,
                # FIXME: handle this error correctly
                'msg': 'Failed to template loop_control.label: %s' % to_text(e)
            })

        self._raw_loop_results.append(result)


    def _task_result_from_loop_results(self, item_results: list[dict[str, object]]) -> dict[str, object]:
        # create the overall result item
        res = dict(results=item_results)

        # loop through the item results and set the global changed/failed/skipped result flags based on any item.
        res['skipped'] = True
        for item in item_results:
            if item.get('_ansible_no_log'):
                res.update(_ansible_no_log=True)  # ensure no_log processing recognizes at least one item needs to be censored

            if 'changed' in item and item['changed'] and not res.get('changed'):
                res['changed'] = True
            if res['skipped'] and ('skipped' not in item or ('skipped' in item and not item['skipped'])):
                res['skipped'] = False
            # FIXME: normalize `failed` to a bool, warn if the action/module used non-bool
            # FIXME: document the "last failing item's value for ignore_errors/ignore_unreachable becomes the task's value
            if 'failed' in item and item['failed']:
                # FIXME: move a single copy of this state to the outer task dataclass and update the outer task's value using this logic inline during record_result
                item_ignore = item.get('_ansible_ignore_errors')  # the post-templated value of ignore_errors for this item
                if not res.get('failed'):
                    res['failed'] = True
                    res['msg'] = 'One or more items failed'
                    self._ignore_errors = item_ignore
                elif self._ignore_errors and not item_ignore:
                    self._ignore_errors = item_ignore
            if 'unreachable' in item and item['unreachable']:
                item_ignore_unreachable = item.get('_ansible_ignore_unreachable')  # the post-templated value of ignore_unreachable for this item
                if not res.get('unreachable'):
                    res['unreachable'] = True
                    self._ignore_unreachable = item_ignore_unreachable
                elif self._ignore_unreachable and not item_ignore_unreachable:
                    self._ignore_unreachable = item_ignore_unreachable

            # ensure to accumulate these
            for array in ['warnings', 'deprecations']:
                if array in item and item[array]:
                    if array not in res:
                        res[array] = []
                    if not isinstance(item[array], list):
                        item[array] = [item[array]]
                    res[array] = res[array] + item[array]
                    del item[array]

        # FIXME: convert some magic keys to mutable mapping-backed interface over TaskResult dataclass
        if not res.get('failed', False):
            res['msg'] = 'All items completed'
        if res['skipped']:
            res['msg'] = 'All items skipped'

        return res



TaskArgsFinalizerCallback = t.Callable[[str, t.Any, _engine.TemplateEngine, t.Any], t.Any]
"""Type alias for the shape of the `ActionBase.finalize_task_arg` method."""


class TaskArgsChainTemplar(ChainTemplar):
    """
    A ChainTemplar that carries a user-provided context object, optionally provided by `ActionBase.get_finalize_task_args_context`.
    TaskArgsFinalizer provides the context to each `ActionBase.finalize_task_arg` call to allow for more complex/stateful customization.
    """

    def __init__(self, *sources: c.Mapping, templar: _engine.TemplateEngine, callback: TaskArgsFinalizerCallback, context: t.Any) -> None:
        super().__init__(*sources, templar=templar)

        self.callback = callback
        self.context = context

    def template(self, key: t.Any, value: t.Any) -> t.Any:
        return self.callback(key, value, self.templar, self.context)


class TaskArgsFinalizer:
    """Invoked during task args finalization; allows actions to override default arg processing (e.g., templating)."""

    def __init__(self, *args: c.Mapping[str, t.Any] | str | None, templar: _engine.TemplateEngine) -> None:
        self._args_layers = [arg for arg in args if arg is not None]
        self._templar = templar

    def finalize(self, callback: TaskArgsFinalizerCallback, context: t.Any) -> dict[str, t.Any]:
        resolved_layers: list[c.Mapping[str, t.Any]] = []

        for layer in self._args_layers:
            if isinstance(layer, (str, _vault.EncryptedString)):  # EncryptedString can hide a template
                if constants.config.get_config_value('INJECT_FACTS_AS_VARS'):
                    Display().warning(
                        "Using a template for task args is unsafe in some situations "
                        "(see https://docs.ansible.com/ansible/devel/reference_appendices/faq.html#argsplat-unsafe).",
                        obj=layer,
                    )

                resolved_layer = self._templar.resolve_to_container(layer, options=_engine.TemplateOptions(value_for_omit={}))
            else:
                resolved_layer = layer

            if not isinstance(resolved_layer, dict):
                raise AnsibleError(f'Task args must resolve to a {native_type_name(dict)!r} not {native_type_name(resolved_layer)!r}.', obj=layer)

            resolved_layers.append(resolved_layer)

        ct = TaskArgsChainTemplar(*reversed(resolved_layers), templar=self._templar, callback=callback, context=context)

        return ct.as_dict()
