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
from ansible.module_utils.datatag import AnsibleTaggedObject
from ansible.module_utils.datatag.access import AnsibleAccessContext
from ansible.module_utils.common.yaml import SafeDumper
from ansible.template.jinja_bits import AnsibleUndefined
from ansible.vars.hostvars import HostVars, HostVarsVars


class AnsibleDumper(SafeDumper):
    """A simple stub class that allows us to add representers for our custom types."""


def represent_hostvars(self, data):
    # FIXME: probably not correct
    return self.represent_dict(dict(data))


# Note: only want to represent the encrypted data
def represent_vault_encrypted_unicode(self, data):
    # FIXME: not currently used
    return self.represent_scalar(u'!vault', data._ciphertext.decode(), style='|')


def represent_ansible_tagged_object(self, data):
    data = AnsibleAccessContext.current().access(data)
    # access might change it to a non-tagged type, account for that...
    if isinstance(data, AnsibleTaggedObject):
        return self.represent_data(data.native_copy())

    return self.represent_data(data)


def represent_undefined(self, data: AnsibleUndefined) -> t.NoReturn:
    data.trip()


AnsibleDumper.add_representer(
    HostVars,
    represent_hostvars,
)

AnsibleDumper.add_representer(
    HostVarsVars,
    represent_hostvars,
)

# FIXME: special-case dumper support for VaultedValue-tagged objects?

AnsibleDumper.add_multi_representer(AnsibleUndefined, represent_undefined)

# FIXME: do we actually need knobs to allow re-serialization of !!unsafe or !!vault?
# FIXME: how do we want to handle this for lazy containers, for cases like using the to_yaml filter in templates?
AnsibleDumper.add_multi_representer(AnsibleTaggedObject, represent_ansible_tagged_object)
