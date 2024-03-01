# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
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

# FIXME: consider using AnsibleSerializable to register known types automatically?
from ansible.module_utils.datatag import AnsibleTaggedObject, Tripwire, VaultedValue
from ansible.module_utils.datatag.access import AnsibleAccessContext
from ansible.module_utils.common.yaml import SafeDumper
from ansible.vars.hostvars import HostVars, HostVarsVars


class AnsibleDumper(SafeDumper):
    """A simple stub class that allows us to add representers for our custom types."""

    def __init__(self, *args, dump_vault_tags: bool | None = None, **kwargs):
        super().__init__(*args, **kwargs)

        self._dump_vault_tags = dump_vault_tags


def represent_hostvars(self, data):
    # FIXME: probably not correct
    return self.represent_dict(dict(data))


def represent_ansible_tagged_object(self, data):
    data = AnsibleAccessContext.current().access(data)

    if (vv := VaultedValue.get_tag(data)) and self._dump_vault_tags is not False:
        # deprecated: description='enable the deprecation warning below' core_version='2.21'
        # if self._dump_vault_tags is None:
        #     Display().deprecated(
        #         msg="Implicit YAML dumping of vaulted value ciphertext is deprecated. Set `dump_vault_tags` to explicitly specify the desired behavior",
        #         version="2.25",
        #     )

        return self.represent_scalar(u'!vault', vv.ciphertext, style='|')

    # access might change it to a non-tagged type, account for that...
    if isinstance(data, AnsibleTaggedObject):
        return self.represent_data(data.native_copy())

    return self.represent_data(data)


def represent_tripwire(self, data: Tripwire) -> t.NoReturn:
    data.trip()


AnsibleDumper.add_representer(
    HostVars,
    represent_hostvars,
)

AnsibleDumper.add_representer(
    HostVarsVars,
    represent_hostvars,
)

AnsibleDumper.add_multi_representer(Tripwire, represent_tripwire)

# FIXME: do we actually need knobs to allow re-serialization of !!unsafe
# FIXME: how do we want to handle this for lazy containers, for cases like using the to_yaml filter in templates?
AnsibleDumper.add_multi_representer(AnsibleTaggedObject, represent_ansible_tagged_object)
