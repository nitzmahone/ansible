# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
# Copyright: (c) 2017, Ansible Project
# Copyright: (c) 2018, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import json
import typing as t

from ansible.errors import AnsibleJSONParserError
from ansible.errors.utils import RedactAnnotatedSourceContext
from ansible.parsing.vault import VaultSecret
from ansible.parsing.yaml.loader import AnsibleLoader
from ansible.parsing.yaml.errors import AnsibleYAMLParserError
from ansible.utils.serialization import legacy


def from_yaml(
    data: str,
    file_name: str = '<string>',  # DTFIX-MERGE: do something better here, we don't want placeholders making their way into AnsibleSourcePosition
    show_content: bool = True,  # deprecated: description='deprecate show_content in favor of RedactAnnotatedSourceContext' core_version='2.22'
    vault_secrets: list[tuple[str, VaultSecret]] | None = None,
    json_only: bool = False,
    trusted_as_template: bool = False,
) -> t.Any:
    """Creates a Python data structure from the given data, which can be either a JSON or YAML string."""
    with RedactAnnotatedSourceContext.maybe(create=not show_content):
        # TEMPFIX: this whole two-step should be unnecessary, implement this natively in the YAML decoder or delegate?
        try:
            # we first try to load this data as JSON.
            # Fixes issues with extra vars json strings not being parsed correctly by the yaml parser
            return json.loads(data, cls=legacy.Decoder, file_name=file_name, vault_secrets=vault_secrets)
        except Exception as json_ex:
            if json_only:
                AnsibleJSONParserError.handle_exception(json_ex, src=file_name)

            # YAML loading is intentionally nested inside the JSON exception handler so the JSON traceback is preserved.
            # The JSON error is not included in the YAML error, since it's usually noise, but it will be visible when showing the traceback.

            try:
                return AnsibleLoader(data, file_name=file_name, vault_secrets=vault_secrets, trusted_as_template=trusted_as_template).get_single_data()
            except Exception as yaml_ex:
                # DTFIX-MERGE: how can we indicate in AnsibleSourcePosition that the data is in-memory only, to support context information -- is that useful?
                #        we'd need to pass data to handle_exception so it could be used as the content instead of reading from disk
                AnsibleYAMLParserError.handle_exception(yaml_ex, src=file_name)
