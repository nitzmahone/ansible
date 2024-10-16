from __future__ import annotations

import collections.abc as c
import itertools
import typing as t

from ansible.errors import AnsibleValueOmittedError, AnsibleError
from ansible.template.templar import Templar


class ChainTemplar:
    """A basic variable layering mechanism that supports templating and obliteration of `omit` values."""
    def __init__(self, *sources: c.Mapping, templar: Templar) -> None:
        self.sources = sources
        self.templar = templar

    def template(self, key: t.Any, value: t.Any) -> t.Any:
        return self.templar.template(value)

    def get(self, key: t.Any) -> t.Any:
        for source in self.sources:
            if key not in source:
                continue

            value = source[key]

            try:
                return self.template(key, value)
            except AnsibleValueOmittedError:
                break  # omit == obliterate - matches historical behavior where dict layers were squashed before templating was applied
            except Exception as ex:
                raise AnsibleError(f'Error while resolving value for {key!r}.', obj=value) from ex

        raise KeyError(key)

    def keys(self) -> t.Iterable[t.Any]:
        return sorted(set(itertools.chain.from_iterable(self.sources)))

    def items(self) -> t.Iterable[t.Tuple[t.Any, t.Any]]:
        for key in self.keys():
            try:
                yield key, self.get(key)
            except KeyError:
                pass

    def as_dict(self) -> dict[t.Any, t.Any]:
        return dict(self.items())
