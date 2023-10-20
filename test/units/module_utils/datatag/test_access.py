import abc
import typing as t

from dataclasses import dataclass

# noinspection PyProtectedMember
from ansible.module_utils.datatag import AnsibleSourcePosition, NotATemplate, AnsibleTaggedObject
# noinspection PyProtectedMember
from ansible.module_utils.datatag.access import AnsibleAccessContext, _NotifiableAccessContextBase, _MutatingAccessContextBase

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


class AnsibleSourcePositionAccessNotifier(LoggingTagAccessNotifier):
    _tag_type_interest = frozenset([AnsibleSourcePosition])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        return o


class NotATemplateAccessNotifier(LoggingTagAccessNotifier):
    _tag_type_interest = frozenset([NotATemplate])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        return o


# returns the value of TestInstanceTag
class AnsibleSourcePositionAccessMutator(LoggingTagAccessNotifier, _MutatingAccessContextBase):
    _tag_type_interest = frozenset([AnsibleSourcePosition])

    def _notify(self, o: t.Any) -> t.Any:
        super()._log(o)  # get parent logging behavior
        return AnsibleSourcePosition.get_tag(o).src


def test_ansibleaccesscontext_untagged():
    # accessing untagged objects should always succeed, be a no-op, and return the original value
    for v in untagged_values:
        res = AnsibleAccessContext.current().access(v)
        assert res is v


def test_ansibleaccesscontext_notify():
    tagged_values = [AnsibleTaggedObject.tag(v, [NotATemplate(), AnsibleSourcePosition(src='replacement')]) for v in untagged_values]

    instance_access_list = []
    singleton_access_list = []

    with AnsibleSourcePositionAccessNotifier(instance_access_list):
        with NotATemplateAccessNotifier(singleton_access_list):
            for tv in tagged_values:
                res = AnsibleAccessContext.current().access(tv)
                assert res is tv

    assert [v.obj for v in instance_access_list] == [v.obj for v in singleton_access_list] == tagged_values


def test_ansibleaccesscontext_mutate():
    tagged_values = [AnsibleTaggedObject.tag(v, [AnsibleSourcePosition(src='replacement')]) for v in untagged_values]

    instance_access_list = []

    with AnsibleSourcePositionAccessMutator(instance_access_list):
        for tv in tagged_values:
            res = AnsibleAccessContext.current().access(tv)
            assert res == 'replacement'

    assert [v.obj for v in instance_access_list] == tagged_values
