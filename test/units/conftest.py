# Try to globally patch Templar trust check failures to be fatal for all unit tests
import pytest
import sys
import types


try:
    from ansible.template.templar import Templar
except ImportError:
    # likely doing only module_utils testing; ignore here and rely on test_templar::test_trust_fail_raises_in_tests to ensure the right behavior
    pass
else:
    Templar._raise_on_trust_check_fail = True


@pytest.fixture
def inject_collection_root_package_stub():
    module_name = 'ansible_collections.ansible.builtin'

    module = types.ModuleType(module_name)
    module.__file__ = '<bogus>'
    module.__path__ = []
    module.__package__ = module_name

    sys.modules[module_name] = module
