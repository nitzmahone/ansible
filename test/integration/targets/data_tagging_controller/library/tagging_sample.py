from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.datatag import Deprecated, SensitiveData


def main():
    mod = AnsibleModule(argument_spec={
        'sensitive_module_arg': dict(default='THIS VALUE WAS NO_LOG IN A MODULE AND SHOULD NOT BE SEEN', type='str', no_log=True),
    })

    sensitive_value = 'a sensitive value we should not display'
    # SensitiveData is a simple tag w/ no args; tag the value and store it (no mutation!)
    sensitive_value = SensitiveData().tag(sensitive_value)

    something_old_value = 'an old thing'
    # Deprecated needs args; tag the value and store it
    something_old_value = Deprecated(msg="`something_old` is deprecated, don't use it!", removal_version='1.2.3').tag(something_old_value)

    result = {
        'something_old': something_old_value,
        'secret_thing': sensitive_value,
        # send the module param back to core; since it was no log, AnsibleModule tagged it with SensitiveData
        'sensitive_module_arg': mod.params['sensitive_module_arg'],
        # rendering templates from modules is a no-no, core does not trust anything by default
        'untrusted_template': '{{ ["me", "see", "not", "should"] | sort(reverse=true) | join(" ") }}',
        'changed': False
    }

    mod.exit_json(**result)


if __name__ == '__main__':
    main()
