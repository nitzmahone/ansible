# Try to globally patch Templar trust check failures to be fatal for all unit tests

try:
    from ansible.template.old_init import Templar
except ImportError:
    # likely doing only module_utils testing; ignore here and rely on test_templar::test_trust_fail_raises_in_tests to ensure the right behavior
    pass
else:
    Templar._raise_on_trust_check_fail = True
