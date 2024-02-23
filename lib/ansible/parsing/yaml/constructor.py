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

from yaml.constructor import SafeConstructor, ConstructorError
from yaml.nodes import MappingNode

from ansible import constants as C
from ansible.module_utils.common.text.converters import to_native, to_text
from ansible.module_utils.datatag import (AnsibleSourcePosition, AnsibleTaggedObject, UndecryptableVaultedValue,
                                          TrustedAsTemplate, NotATemplate, VaultedValue)
from ansible.parsing.vault import VaultLib
from ansible.utils.display import Display

display = Display()


class AnsibleConstructor(SafeConstructor):
    def __init__(self, file_name=None, vault_secrets=None, trusted_as_template=False):
        self._ansible_file_name = str(file_name)  # ensure we don't have a PathLike or tagged string that will upset AnsibleSourcePosition
        super(AnsibleConstructor, self).__init__()
        self._vaults = {}
        self.vault_secrets = vault_secrets or []
        self._vaults['default'] = VaultLib(secrets=self.vault_secrets)
        self._trusted_as_template = trusted_as_template

        # volatile state var used during recursive construction of a value tagged unsafe
        self._unsafe_depth = 0

    def construct_yaml_map(self, node):
        data = self._node_position_info(node).tag({})  # always an ordered dictionary on py3.7+
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_mapping(self, node, deep=False):
        # Most of this is from yaml.constructor.SafeConstructor.  We replicate
        # it here so that we can warn users when they have duplicate dict keys
        # (pyyaml silently allows overwriting keys)
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None,
                                   "expected a mapping node, but found %s" % node.id,
                                   node.start_mark)
        self.flatten_mapping(node)
        mapping = self._node_position_info(node).tag({})

        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError("while constructing a mapping", node.start_mark,
                                       "found unacceptable key (%s)" % exc, key_node.start_mark)

            if key in mapping:
                pos = AnsibleSourcePosition.get_tag(mapping)
                msg = (f'While constructing a mapping from {pos.src}, line {pos.line}, column {pos.col}, found a '
                       f'duplicate dict key ({key}). Using last defined value only.')
                if C.DUPLICATE_YAML_DICT_KEY == 'warn':
                    display.warning(msg)
                elif C.DUPLICATE_YAML_DICT_KEY == 'error':
                    raise ConstructorError(context=None, context_mark=None,
                                           problem=to_native(msg),
                                           problem_mark=node.start_mark,
                                           note=None)
                else:
                    # when 'ignore'
                    display.debug(msg)

            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value

        return mapping

    def construct_yaml_int(self, node):
        value = super().construct_yaml_int(node)
        return self._node_position_info(node).tag(value)

    def construct_yaml_float(self, node):
        value = super().construct_yaml_float(node)
        return self._node_position_info(node).tag(value)

    def construct_yaml_timestamp(self, node):
        value = super().construct_yaml_timestamp(node)
        return self._node_position_info(node).tag(value)

    def construct_yaml_omap(self, node):
        src_pos = self._node_position_info(node)
        display.deprecated(
            # FIXME: another source position use case, this time without value
            msg=f'YAML !!omap tag found at {str(src_pos)!r} is deprecated. Use a standard mapping instead, as key order is always preserved.',
            version='2.21',
        )
        items = list(super().construct_yaml_omap(node))[0]
        items = [src_pos.tag(item) for item in items]
        yield src_pos.tag(items)

    def construct_yaml_pairs(self, node):
        src_pos = self._node_position_info(node)
        display.deprecated(
            # FIXME: another source position use case, this time without value
            msg=f'YAML !!pairs tag found at {str(src_pos)!r} is deprecated.',
            version='2.21',
        )
        items = list(super().construct_yaml_pairs(node))[0]
        items = [src_pos.tag(item) for item in items]
        yield src_pos.tag(items)

    def construct_yaml_str(self, node):
        # Override the default string handling function
        # to always return unicode objects
        # FIXME: is this still necessary under Py3?
        value = to_text(self.construct_scalar(node))

        # FIXME: factor out this shared code among the various constructor methods
        tags = [self._node_position_info(node)]

        if self._unsafe_depth:
            tags.append(NotATemplate())
        elif self._trusted_as_template:
            tags.append(TrustedAsTemplate())

        # FIXME: optimize this to support non-conditional list construction and a shared instance of TrustedAsTemplate
        return AnsibleTaggedObject.tag(value, tags)

    def construct_yaml_binary(self, node):
        value = super().construct_yaml_binary(node)

        return AnsibleTaggedObject.tag(value, self._node_position_info(node))

    def construct_yaml_set(self, node):
        data = AnsibleTaggedObject.tag(set(), self._node_position_info(node))
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_vault_encrypted_unicode(self, node):
        ciphertext = self.construct_scalar(node)

        # FIXME: ffs, vault-id support was never implemented for these- try all with secrets iteratively?
        vault = self._vaults['default']
        if vault.secrets is None:
            # FIXME: do we want to conditionally fail on the absence of a special control context (eg, set by ansible-inventory), or ?
            raise ConstructorError(context=None, context_mark=None,
                                   problem="found !vault but no vault password provided",
                                   problem_mark=node.start_mark,
                                   note=None)

        # always include the source position, and tag ciphertext so we can round-trip re-serialize
        tags = [self._node_position_info(node), VaultedValue(ciphertext=ciphertext)]

        if self._unsafe_depth:
            tags.append(NotATemplate())
        elif self._trusted_as_template:
            tags.append(TrustedAsTemplate())

        # FIXME: check the vault ID upfront?

        try:
            value = to_text(vault.decrypt(ciphertext))
        except Exception as ex:
            value = ciphertext
            tags.append(UndecryptableVaultedValue())  # specially tag things we aren't able to decrypt (cheaper than a flag in VaultedValue)

        value = AnsibleTaggedObject.tag(value, tags)
        return value

    def construct_yaml_seq(self, node):
        data = self._node_position_info(node).tag([])
        yield data
        data.extend(self.construct_sequence(node))

    def construct_yaml_unsafe(self, node):
        try:
            constructor = getattr(node, 'id', 'object')
            if constructor is not None:
                constructor = getattr(self, 'construct_%s' % constructor)
        except AttributeError:
            constructor = self.construct_object

        self._unsafe_depth += 1

        try:
            if node.id == 'scalar':
                result = constructor(node)
            else:
                # non-deferred construction of hierarchical nodes so our stateful unsafe propagation behavior works
                result = constructor(node, deep=True)
        finally:
            self._unsafe_depth -= 1

        return result

    def _node_position_info(self, node):
        # the line number where the previous token has ended (plus empty lines)
        # Add one so that the first line is line 1 rather than line 0
        column = node.start_mark.column + 1
        line = node.start_mark.line + 1

        # in some cases, we may have pre-read the data and then
        # passed it to the load() call for YAML, in which case we
        # want to override the default datasource (which would be
        # '<string>') to the actual filename we read in
        datasource = self._ansible_file_name or node.start_mark.name

        return AnsibleSourcePosition(src=datasource, line=line, col=column)


AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:map',
    AnsibleConstructor.construct_yaml_map)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:python/dict',
    AnsibleConstructor.construct_yaml_map)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:str',
    AnsibleConstructor.construct_yaml_str)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:binary',
    AnsibleConstructor.construct_yaml_binary)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:set',
    AnsibleConstructor.construct_yaml_set)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:omap',
    AnsibleConstructor.construct_yaml_omap)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:pairs',
    AnsibleConstructor.construct_yaml_pairs)

# FIXME: do we actually want to tag int/float/etc?
AnsibleConstructor.add_constructor(
    'tag:yaml.org,2002:int',
    AnsibleConstructor.construct_yaml_int)

AnsibleConstructor.add_constructor(
    'tag:yaml.org,2002:float',
    AnsibleConstructor.construct_yaml_float)

AnsibleConstructor.add_constructor(
    'tag:yaml.org,2002:timestamp',
    AnsibleConstructor.construct_yaml_timestamp)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:python/unicode',
    AnsibleConstructor.construct_yaml_str)

AnsibleConstructor.add_constructor(
    u'tag:yaml.org,2002:seq',
    AnsibleConstructor.construct_yaml_seq)

AnsibleConstructor.add_constructor(
    u'!unsafe',
    AnsibleConstructor.construct_yaml_unsafe)

AnsibleConstructor.add_constructor(
    u'!vault',
    AnsibleConstructor.construct_vault_encrypted_unicode)

# FIXME: did we ever need this?
# AnsibleConstructor.add_constructor(u'!vault-encrypted', AnsibleConstructor.construct_vault_encrypted_unicode)
