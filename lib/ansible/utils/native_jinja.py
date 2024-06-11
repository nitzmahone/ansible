# Copyright: (c) 2023, Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import annotations


# deprecated: description="deprecate NativeJinjaText" core_version="2.19"
# This wrapper is no longer required, and is temporarily preserved for backward compatibility of user plugins that
# may be importing it. This can be deprecated once the last version that requires it (2.16) has gone EOL upstream.
class NativeJinjaText(str):
    def __new__(cls, value):
        return value
