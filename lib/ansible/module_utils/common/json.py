# -*- coding: utf-8 -*-
# Copyright (c) 2019 Ansible Project
# Simplified BSD License (see licenses/simplified_bsd.txt or https://opensource.org/licenses/BSD-2-Clause)

from __future__ import annotations

import json

import datetime

from ansible.module_utils.six.moves.collections_abc import Mapping
from ansible.module_utils.datatag import AnsibleSerializable
from ansible.module_utils.datatag.access import AnsibleAccessContext


def json_dump(structure):
    return json.dumps(structure, cls=AnsibleJSONEncoder, sort_keys=True, indent=4)


# FIXME: use a frozen dataclass if it's not more expensive
class _WrappedValue:
    __slots__ = tuple(('wrapped',))

    def __init__(self, wrapped):
        self.wrapped = wrapped


class AnsibleJSONEncoder(json.JSONEncoder):
    '''
    Simple encoder class to deal with JSON encoding of Ansible internal types
    '''

    _wrap_container_types = (list, set, tuple, dict)

    def __init__(self, preprocess_unsafe=False, vault_to_text=False, preserve_datatags=False, **kwargs):
        self._wrap_types = self._wrap_container_types + (AnsibleSerializable,)
        self._preprocess_unsafe = preprocess_unsafe
        self._vault_to_text = vault_to_text
        self._preserve_datatags = preserve_datatags

        super(AnsibleJSONEncoder, self).__init__(**kwargs)

    def encode(self, o):
        o = _WrappedValue(o)

        return super(AnsibleJSONEncoder, self).encode(o)

    # NOTE: ALWAYS inform AWS/Tower when new items get added as they consume them downstream via a callback
    def default(self, o):
        # To control serialization of subclasses of builtin types, we have to wrap their values (and one level
        # of sub-values, for containers) in a non-serializable wrapper (_WrappedValue). The wrapper forces the values back
        # through this method on future iterations; without this, or a pre-flight copy, objects that are subclasses of
        # native types get short-circuited through their default representation by the serializer.
        if type(o) is _WrappedValue:  # pylint: disable=unidiomatic-typecheck
            o = o.wrapped
        # managed access; allows external access audit and/or replacement of values
        o = AnsibleAccessContext.current().access(o)

        # FIXME: optimize this; maybe direct-dispatch from a type mapping instead of using isinstance?
        if self._preserve_datatags and isinstance(o, AnsibleSerializable):
            o = o.serialize()
        # FIXME: remove?
        # elif getattr(o, '__ENCRYPTED__', False):
        #     # vault object
        #     if self._vault_to_text:
        #         value = to_text(o, errors='surrogate_or_strict')
        #     else:
        #         value = {'__ansible_vault': to_text(o._ciphertext, errors='surrogate_or_strict', nonstring='strict')}
        # start a new if parade to ensure we handle eg, nested lists of custom types from as_dict
        if isinstance(o, Mapping):
            value = {str(k): _WrappedValue(v) if isinstance(v, self._wrap_types) else v for k, v in o.items()}
        elif isinstance(o, self._wrap_container_types):  # FIXME: others, maybe sequence? can't use Iterable...
            value = [_WrappedValue(v) if isinstance(v, self._wrap_types) else v for v in o]
        elif isinstance(o, (datetime.date, datetime.datetime)):
            # date object
            value = o.isoformat()
        # FIXME: we could've theoretically wrapped or returned just about any builtin; cheap way to catch all builtins?
        elif isinstance(o, (str, float, int, bool, type(None))):
            value = o
        elif isinstance(o, bytes):
            value = o.decode()  # only reachable on Python 3.x due to str being in the type list for the previous conditional
        else:
            # use default encoder, which will likely result in an exception
            value = super(AnsibleJSONEncoder, self).default(o)
        return value


class AnsibleJSONDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        kwargs['object_hook'] = self.object_hook
        super(AnsibleJSONDecoder, self).__init__(*args, **kwargs)

    # FIXME: remove?
    @classmethod
    def set_secrets(cls, secrets):
        pass

    def object_hook(self, pairs):
        value = AnsibleSerializable.deserialize(pairs)

        if value is not None:
            return value

        return pairs

        # FIXME: FDI017 ditch this? only reason to keep them is stale/mismatched Tower inventory, which we need to solve another way anyway
        # for key in pairs:
        #     value = pairs[key]
        #
        #     if key == '__ansible_vault':
        #         value = AnsibleVaultEncryptedUnicode(value)
        #         if self._vaults:
        #             value.vault = self._vaults['default']
        #         return value
        #     elif key == '__ansible_unsafe':
        #         return UntrustedAsTemplate().tag(value)
        #
        # return pairs
