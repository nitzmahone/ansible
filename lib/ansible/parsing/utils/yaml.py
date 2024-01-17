# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
# Copyright: (c) 2017, Ansible Project
# Copyright: (c) 2018, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import json

from yaml import YAMLError

from ansible.errors import AnsibleParserError
from ansible.errors.yaml_strings import YAML_SYNTAX_ERROR
from ansible.module_utils.common.text.converters import to_native
from ansible.module_utils.datatag import AnsibleSourcePosition
from ansible.parsing.yaml.loader import AnsibleLoader
from ansible.module_utils.common.json import AnsibleJSONDecoder


__all__ = ('from_yaml',)


def _handle_error(json_exc, yaml_exc, file_name, show_content):
    '''
    Optionally constructs a dummy object to encapsulate the file name/position where a YAML exception occurred, and
    raises an AnsibleParserError to display the syntax exception information.
    '''

    # if the YAML exception contains a problem mark, use it to construct
    # a dummy object the error class can use to display the faulty line
    err_obj = None
    if hasattr(yaml_exc, 'problem_mark'):
        err_pos = AnsibleSourcePosition(src=file_name, line=yaml_exc.problem_mark.line + 1, col=yaml_exc.problem_mark.column + 1)
        err_obj = err_pos.tag('')

    n_yaml_syntax_error = YAML_SYNTAX_ERROR % to_native(getattr(yaml_exc, 'problem', u''))
    n_err_msg = 'We were unable to read either as JSON nor YAML, these are the errors we got from each:\n' \
                'JSON: %s\n\n%s' % (to_native(json_exc), n_yaml_syntax_error)

    raise AnsibleParserError(n_err_msg, obj=err_obj, show_content=show_content, orig_exc=yaml_exc)


def _safe_load(stream, file_name=None, vault_secrets=None, trusted_as_template=False):
    ''' Implements yaml.safe_load(), except using our custom loader class. '''

    loader = AnsibleLoader(stream, file_name, vault_secrets, trusted_as_template=trusted_as_template)
    try:
        return loader.get_single_data()
    finally:
        try:
            # FIXME: this seems redundant- why? (also, even if not, try/except should no longer be necessary)
            loader.dispose()
        except AttributeError:
            pass  # older versions of yaml don't have dispose function, ignore


def from_yaml(data, file_name='<string>', show_content=True, vault_secrets=None, json_only=False, trusted_as_template=False):
    '''
    Creates a python datastructure from the given data, which can be either
    a JSON or YAML string.
    '''
    new_data = None

    # FIXME: this whole two-step sucks, implement this natively in the YAML decoder or delegate?
    try:
        # in case we have to deal with vaults
        # FIXME: this sucks- use an instance
        AnsibleJSONDecoder.set_secrets(vault_secrets)

        # we first try to load this data as JSON.
        # Fixes issues with extra vars json strings not being parsed correctly by the yaml parser
        # FIXME: FDI029 - can/should we handle inline source position/TrustedAsTemplate tagging here?
        new_data = json.loads(data, cls=AnsibleJSONDecoder)
    except Exception as json_exc:

        if json_only:
            raise AnsibleParserError(to_native(json_exc), orig_exc=json_exc)

        # must not be JSON, let the rest try
        try:
            new_data = _safe_load(data, file_name=file_name, vault_secrets=vault_secrets, trusted_as_template=trusted_as_template)
        except YAMLError as yaml_exc:
            _handle_error(json_exc, yaml_exc, file_name, show_content)

    return new_data
