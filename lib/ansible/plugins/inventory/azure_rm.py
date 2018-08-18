# Copyright (c) 2018 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)

__metaclass__ = type

import json

try:
    from queue import Queue, Empty
except ImportError:
    from Queue import Queue, Empty

from collections import namedtuple
from ansible import release
from azure.common.credentials import get_azure_cli_credentials
from msrest import ServiceClient, Serializer, Deserializer
from msrestazure import AzureConfiguration
from msrestazure.polling.arm_polling import ARMPolling


class AzureRMRestConfiguration(AzureConfiguration):
    def __init__(self, credentials, subscription_id, base_url=None):

        if credentials is None:
            raise ValueError("Parameter 'credentials' must not be None.")
        if subscription_id is None:
            raise ValueError("Parameter 'subscription_id' must not be None.")
        if not base_url:
            base_url = 'https://management.azure.com'

        super(AzureRMRestConfiguration, self).__init__(base_url)

        self.add_user_agent('ansible-dynamic-inventory/{}'.format(release.__version__))

        self.credentials = credentials
        self.subscription_id = subscription_id


UrlAction = namedtuple('UrlAction', ['url', 'api_version', 'handler'])


class InventoryClient(object):
    def __init__(self, config):
        self._config = config
        self._serializer = Serializer()
        self._deserializer = Deserializer()
        self._hosts = []

        self._clientconfig = AzureRMRestConfiguration(config['credentials'], config['subscription_id'], config.get('base_url'))
        self._client = ServiceClient(self._clientconfig.credentials, self._clientconfig)

        # TODO: use API profiles with defaults
        self._compute_api_version = '2017-03-30'
        self._network_api_version = '2015-06-15'

        self._default_header_parameters={'Content-Type': 'application/json; charset=utf-8'}

        self._request_queue = Queue()

    def _enqueue_get(self, url, api_version, handler):
        self._request_queue.put_nowait(UrlAction(url=url, api_version=api_version, handler=handler))

    def _enqueue_vm_list(self, rg='*'):
        if not rg or rg == '*':
            url = '/subscriptions/{subscriptionId}/providers/Microsoft.Compute/virtualMachines'
        else:
            url = '/subscriptions/{subscriptionId}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines'

        url = url.format(subscriptionId=self._clientconfig.subscription_id, rg=rg)
        self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vm_page_response)

    def _enqueue_vmss_list(self, rg=None):
        if not rg or rg == '*':
            url = '/subscriptions/{subscriptionId}/providers/Microsoft.Compute/virtualMachineScaleSets'
        else:
            url = '/subscriptions/{subscriptionId}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachineScaleSets'

        url = url.format(subscriptionId=self._clientconfig.subscription_id, rg=rg)
        self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vmss_page_response)

    def get_hosts(self, batch=True):
        for vm_rg in self._config.get('include_vm_resource_groups', ['*']):
            self._enqueue_vm_list(vm_rg)

        for vmss_rg in self._config.get('include_vmss_resource_groups', []):
            self._enqueue_vmss_list(vmss_rg)

        if batch:
            self._process_queue_batch()
        else:
            self._process_queue_serial()

        return self._hosts

    def _process_queue_serial(self):
        # FUTURE: parallelize fetch with worker threads?
        try:
            while True:
                item = self._request_queue.get_nowait()
                resp = self.send_request(item.url, item.api_version)
                item.handler(resp)
        except Empty:
            pass

    def _on_vm_page_response(self, response):
        next_link = response.get('nextLink')

        if next_link:
            self._enqueue_get(url=next_link, api_version=self._compute_api_version, handler=self._on_vm_page_response)

        for h in response['value']:
            self._hosts.append(Host(h, self))

    def _on_vmss_page_response(self, response):
        next_link = response.get('nextLink')

        if next_link:
            self._enqueue_get(url=next_link, api_version=self._compute_api_version, handler=self._on_vmss_page_response)

        # TODO: filter VMSSs by config
        for vmss in response['value']:
            print("vmss yay")
            url = '{0}/virtualMachines'.format(vmss['id'])
            # VMSS instances look close enough to regular VMs that we can share the handler impl...
            self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vm_page_response)

    def _process_queue_batch(self):
        while True:
            batch_requests = []
            batch_item_index = 0
            batch_response_handlers = []
            try:
                while batch_item_index < 500:
                    item = self._request_queue.get_nowait()

                    query_parameters = {'api-version': item.api_version}
                    req = self._client.get(item.url, query_parameters)

                    batch_requests.append(dict(httpMethod="GET", url=req.url))
                    batch_response_handlers.append(item.handler)
                    batch_item_index += 1
            except Empty:
                pass

            if not batch_requests:
                break

            batch_resp = self._send_batch(batch_requests)

            # TODO: validate batch response count matches request count

            for idx, r in enumerate(batch_resp['responses']):
                # TODO: check individual response codes
                batch_response_handlers[idx](r['content'])

    def _send_batch(self, batched_requests):
        url = '/batch'
        query_parameters = {'api-version': '2015-11-01'}

        body_obj = dict(requests=batched_requests)

        body_content = self._serializer.body(body_obj, 'object')

        request = self._client.post(url, query_parameters)
        initial_response = self._client.send(request, self._default_header_parameters, body_content)

        # TODO: configurable timeout?
        poller = ARMPolling(timeout=2)
        poller.initialize(client=self._client,
                          initial_response=initial_response,
                          deserialization_callback=lambda r: self._deserializer('object', r))

        poller.run()

        return poller.resource()

    def send_request(self, url, api_version):
        query_parameters = { 'api-version': api_version }
        req = self._client.get(url, query_parameters)
        resp = self._client.send(req, self._default_header_parameters, stream=False)

        if resp.status_code != 200:
            # TODO: error handler
            raise Exception("bang")
        content = resp.content

        return json.loads(content)


# VM list (all, N resource groups): VM -> InstanceView, N NICs, N PublicIPAddress)
# VMSS VMs (all SS, N specific SS, N resource groups?): SS -> VM -> InstanceView, N NICs, N PublicIPAddress)

class Host(object):
    def __init__(self, vm_model, inventory_client):
        self._inventory_client = inventory_client

        # determine if this is a VMSS instance
        self.is_vmss_instance = (vm_model['type'] == 'Microsoft.Compute/virtualMachineScaleSets/virtualMachines')

        inventory_client._enqueue_get(url="{0}/instanceView".format(vm_model['id']), api_version=self._inventory_client._compute_api_version, handler=self._on_instanceview_response)

        for nic in vm_model['properties']['networkProfile']['networkInterfaces']:
            inventory_client._enqueue_get(url=nic['id'], api_version=self._inventory_client._network_api_version, handler=self._on_nic_response)

        if self.is_vmss_instance:
            print("vmss instance yay")
        else:
            print("host yay")

    def _on_instanceview_response(self, vm_instanceview_model):
        print("instanceview yay")

    def _on_nic_response(self, nic_model):
        print("nic yay")
        for ipc in nic_model['properties']['ipConfigurations']:
            pip = ipc['properties'].get('publicIPAddress')
            if pip:
                self._inventory_client._enqueue_get(url=pip['id'], api_version=self._inventory_client._network_api_version, handler=self._on_pip_response)

    def _on_pip_response(self, pip_model):
        print("pip yay: %s" % pip_model['properties']['ipAddress'])

def main():
    config = dict(
        include_vm_resource_groups=['mdavistest', 'mdavistest2'],
        include_vmss_resource_groups=['mdavistest2'],
#        include_vm_resource_groups=["*"],
#        include_vmss_resource_groups=["*"],
    )

    credentials, subscription_id = get_azure_cli_credentials()
    config['credentials'] = credentials
    config['subscription_id'] = subscription_id

    ic = InventoryClient(config=config)

    h = ic.get_hosts(batch=True)

    pass


if __name__ == '__main__':
    main()
