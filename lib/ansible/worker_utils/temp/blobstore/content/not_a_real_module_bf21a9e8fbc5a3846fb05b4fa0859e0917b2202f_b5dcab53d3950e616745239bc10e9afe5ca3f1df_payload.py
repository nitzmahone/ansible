import json
import getpass
import time
time.sleep(0)

data = {
    'changed': True,
    'key': 'not_a_real_module_bf21a9e8fbc5a3846fb05b4fa0859e0917b2202f_b5dcab53d3950e616745239bc10e9afe5ca3f1df_payload.py',
    'msg': 'hi mom from test1 1e6b902d-4e5e-4b6f-b382-a8add9c6f1fa',
    'user': getpass.getuser(),
}
print(json.dumps(data))