import base64
import itertools
import pathlib


def main():
    large_payload = pathlib.Path(__file__).parent / 'large_payload.py'
    with open(large_payload, 'rb') as fd:
        payload_raw = fd.read()

    payload_encoded = base64.b64encode(payload_raw).decode()

    # for i in range(0, len(payload_encoded), 1024):
    #     print(payload_encoded[i:i + 1024])

    for chunk in itertools.count():
        output = payload_encoded[chunk*1024:(chunk+1)*1024]

        if not output:
            break

        print(output)
    print()



if __name__ == '__main__':
    main()