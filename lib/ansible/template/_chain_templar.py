from __future__ import annotations

import collections.abc as c
import itertools
import typing as t

from ansible.errors import AnsibleValueOmittedError, AnsibleError
from ansible.template.templar import Templar


class ChainTemplar:
    # DTFIX-PR: most code involving chain maps doesn't (and can't currently) use ChainTemplar, so omit generally becomes obliterate instead
    #          rather than making task args (the only usage of this class) support true omit, just have it obliterate as well
    #          that at least gives us consistent results, and shouldn't vary much from what devel does, except for template before merge cases
    #          those template before merge cases should largely disappear with our improved chain maps and increased lazification of templating
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
                continue
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
