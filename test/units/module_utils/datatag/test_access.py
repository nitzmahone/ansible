from __future__ import annotations

import abc
import typing as t

from dataclasses import dataclass

# noinspection PyProtectedMember
from ansible.module_utils.datatag import AnsibleTaggedObject, AnsibleTagHelper
# noinspection PyProtectedMember
from ansible.module_utils.datatag.access import AnsibleAccessContext, _NotifiableAccessContextBase, _MutatingAccessContextBase

from ..datatag.test_datatag import ExampleSingletonTag, ExampleTagWithContent

untagged_values = (123, 123.45, 'dude', ['dude'], dict(dude='mar'))


@dataclass(frozen=True)
class LoggedAccess:
    ctx: _NotifiableAccessContextBase
    obj: AnsibleTaggedObject


class LoggingTagAccessNotifier(_NotifiableAccessContextBase, metaclass=abc.ABCMeta):
    def __init__(self, access_list: list):
        self._access_list: list = access_list

    def _log(self, o: t.Any) -> t.Any:
        self._access_list.append(LoggedAccess(ctx=self, obj=o))


class ExampleTagWithContentAccessNotifier(LoggingTagAccessNotifier):
    _tag_type_interest = frozenset([ExampleTagWithContent])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        return o


class ExampleSingletonTagAccessNotifier(LoggingTagAccessNotifier):
    _tag_type_interest = frozenset([ExampleSingletonTag])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        return o


# returns the value of TestInstanceTag
class ExampleTagWithContentAccessMutator(LoggingTagAccessNotifier, _MutatingAccessContextBase):
    _tag_type_interest = frozenset([ExampleTagWithContent])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        tag = ExampleTagWithContent.get_tag(o)
        assert tag
        return tag.content_str


def test_ansibleaccesscontext_untagged():
    # accessing untagged objects should always succeed, be a no-op, and return the original value
    for v in untagged_values:
        res = AnsibleAccessContext.current().access(v)
        assert res is v


def test_ansibleaccesscontext_notify():
    tagged_values = [AnsibleTagHelper.tag(v, [ExampleSingletonTag(), ExampleTagWithContent(content_str='replacement')]) for v in untagged_values]

    instance_access_list = []
    singleton_access_list = []

    with ExampleTagWithContentAccessNotifier(instance_access_list):
        with ExampleSingletonTagAccessNotifier(singleton_access_list):
            for tv in tagged_values:
                res = AnsibleAccessContext.current().access(tv)
                assert res is tv

    assert [v.obj for v in instance_access_list] == [v.obj for v in singleton_access_list] == tagged_values


def test_ansibleaccesscontext_mutate():
    tagged_values = [AnsibleTagHelper.tag(v, [ExampleTagWithContent(content_str='replacement')]) for v in untagged_values]

    instance_access_list = []

    with ExampleTagWithContentAccessMutator(instance_access_list):
        for tv in tagged_values:
            res = AnsibleAccessContext.current().access(tv)
            assert res == 'replacement'

    assert [v.obj for v in instance_access_list] == tagged_values
