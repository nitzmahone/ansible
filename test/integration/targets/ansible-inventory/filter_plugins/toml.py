# (c) 2017, Matt Martz <matt@sivel.net>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import collections.abc as c
import functools

from ansible.plugins.inventory.toml import HAS_TOML, toml_dumps

try:
    from ansible.plugins.inventory.toml import toml
except ImportError:
    pass

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_text


def _check_toml(func):
    @functools.wraps(func)
    def inner(o):
        if not HAS_TOML:
            raise AnsibleError('The %s filter plugin requires the python "toml" library' % func.__name__)
        return func(o)
    return inner


@_check_toml
def from_toml(o):
    if not isinstance(o, str):
        raise AnsibleError('from_toml requires a string, got %s' % type(o))
    return toml.loads(to_text(o, errors='surrogate_or_strict'))


@_check_toml
def to_toml(o):
    if not isinstance(o, c.MutableMapping):
        raise AnsibleError('to_toml requires a dict, got %s' % type(o))
    return to_text(toml_dumps(o), errors='surrogate_or_strict')


class FilterModule(object):
    def filters(self):
        return {
            'to_toml': to_toml,
            'from_toml': from_toml
        }
