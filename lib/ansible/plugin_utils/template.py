from __future__ import annotations

import datetime
import os
import pwd
import time

from ansible import constants as C
from ansible.module_utils.common.text.converters import to_bytes, to_text, to_native


def generate_ansible_template_vars(path, fullpath=None, dest_path=None):

    if fullpath is None:
        b_path = to_bytes(path)
    else:
        b_path = to_bytes(fullpath)

    try:
        template_uid = pwd.getpwuid(os.stat(b_path).st_uid).pw_name
    except (KeyError, TypeError):
        template_uid = os.stat(b_path).st_uid

    temp_vars = {
        'template_host': to_text(os.uname()[1]),
        'template_path': path,
        'template_mtime': datetime.datetime.fromtimestamp(os.path.getmtime(b_path)),
        'template_uid': to_text(template_uid),
        'template_run_date': datetime.datetime.now(),
        'template_destpath': to_native(dest_path) if dest_path else None,
    }

    if fullpath is None:
        temp_vars['template_fullpath'] = os.path.abspath(path)
    else:
        temp_vars['template_fullpath'] = fullpath

    managed_default = C.DEFAULT_MANAGED_STR
    managed_str = managed_default.format(
        host=temp_vars['template_host'],
        uid=temp_vars['template_uid'],
        file=temp_vars['template_path'].replace('%', '%%'),
    )
    temp_vars['ansible_managed'] = time.strftime(to_native(managed_str), time.localtime(os.path.getmtime(b_path)))

    return temp_vars
