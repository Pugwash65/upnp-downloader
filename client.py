#!/usr/bin/python

import upnpclient
import xml.etree.ElementTree as ET
import requests
import logging
import time
import sys
import re


class UPNPBrowserException(Exception):
    pass


class UPNPBrowserNoMoreData(Exception):
    pass


class UPNPBrowser:
    UPNP_DEVICES = {
        'humax': {
            'url': 'http://192.168.1.5:50001',
            'dirs': ['My Contents', 'Recordings']
        },
        'frodo': {
            'url': 'http://192.168.1.8:8200/rootdesc.xml'
        }
    }

    @staticmethod
    def set_log_handler():
        """ Ensure that ssdp errors have some where to go """

        log = logging.getLogger('ssdp')
        log.addHandler(logging.NullHandler())
        return True

    @staticmethod
    def find_devices():
        """ Perform UPNP discovery """

        print('Discovering devices...'),
        sys.stdout.flush()

        UPNPBrowser.set_log_handler()
        devices = upnpclient.discover()

        print

        for d in devices:
            print('{0} ({1})'.format(d.friendly_name, d.location))
        return True

    @staticmethod
    def extract_namespaces(content):
        """

        Determine the namespaces to use in finds

        @param content: Raw upnp xml content
        @return ns: namsepace hash{id, xmlns}
        @raises UPNPBrowserException: Raises the module exception

        """

        ns = {}

        m = re.match('^<([^>]+)>', content)
        if not m:
            raise UPNPBrowserException('Invalid XML recevied: Unable to extract root element')

        entry = m.group(1)

        for e in entry.split():

            if not e.startswith('xmlns'):
                continue

            m = re.match('^(xmlns.*)="(.*)"$', e)
            if not m:
                raise UPNPBrowserException('{0}: Invalid namespace entry'.format(e))

            key = m.group(1)
            text = m.group(2)

            if ':' not in key:
                if 'default' in ns:
                    raise UPNPBrowserException('{0}: Default namespace already defined'.format(e))
                ns['default'] = text
            else:
                (tag, xmlns) = key.split(':')
                ns[xmlns] = text

        return ns

    def __init__(self, device_name):

        UPNPBrowser.set_log_handler()

        if device_name is None or device_name == '':
            raise ValueError('Missing device name')

        if device_name not in self.UPNP_DEVICES:
            raise ValueError('{0}: Unknown upnp device'.format(device_name))

        self.device_name = device_name
        self.device_url = self.UPNP_DEVICES[device_name]['url']

        try:
            self.device = upnpclient.Device(self.device_url)
        except requests.exceptions.ConnectionError as exp:
            raise UPNPBrowserException('{0}: Unable to connect to device'.format(device_name))

        return None

    def browse_device(self, upnp_dir, object_id=None, result=None):

        if object_id is None:
            object_id = '0'

        # If results already has data then start with the next block of content
        if result is not None:
            index = len(result) + 1
        else:
            index = 0
            result = {}

        d = self.device
        res = d['ContentDirectory']['Browse'](
            ObjectID=object_id,
            BrowseFlag='BrowseDirectChildren',
            Filter='*',
            StartingIndex=str(index),
            RequestedCount='0',
            SortCriteria=''
        )

        if 'NumberReturned' not in res or 'Result' not in res:
            raise UPNPBrowserException('Browse content failed')

        num = res['NumberReturned']

        if num == 0:
            raise UPNPBrowserNoMoreData

        content = res['Result']

        xml = ET.fromstring(content)

        ns = UPNPBrowser.extract_namespaces(content)

        # Find the elements

        if upnp_dir is None:
            tag = 'default:item'
        else:
            tag = 'default:container'

        elements = xml.findall(tag, ns)
        if not elements:
            raise UPNPBrowserException('{0}: Unable to locate element'.format(tag))

        for e in elements:
            if 'id' not in e.attrib:
                raise UPNPBrowserException('Element has no \'id\' attribute')

            id = e.attrib['id']

            titles = e.findall('dc:title', ns)

            title = titles[0]
            if title is None:
                raise UPNPBrowserException('Unable to locate entry title in content')

            if upnp_dir is None:
                result[id] = title

            elif title.text == upnp_dir:
                result[id] = title
                break

        return result

    def find_content(self, targets, start_date):

        # Convert start date to epoch time

        pattern = '%Y%m%d'
        start_date = int(time.mktime(time.strptime(start_date, pattern)))

        dirs = self.UPNP_DEVICES[self.device_name]['dirs']

        upnp_id = None
        for target_dir in dirs:
            result = self.browse_device(target_dir, upnp_id)
            if not result:
                raise UPNPBrowserException('{0}: Unable to locate directory'.format(target_dir))
            upnp_id = result.keys()[0]

        print('Browsing content.'),
        sys.stdout.flush()

        result = None
        while True:
            try:
                result = self.browse_device(None, upnp_id, result)
                print('.'),
                sys.stdout.flush()
            except UPNPBrowserNoMoreData:
                break
        print('done')

        candidates = {}

        for target in targets:
            for item_id, item in result.items():
                if target in item.text:
                    m = re.match('^.*_(\d{8})_\d{4}$', item.text)
                    if not m:
                        raise UPNPBrowserException('{0}: Unable to extract data'.format(item.text))
                    t = int(time.mktime(time.strptime(m.group(1), pattern)))
                    if t < start_date:
                        continue

                    candidates[item_id] = item
        return candidates


def main(targets, start_date):
    if len(sys.argv) == 1:
        UPNPBrowser.find_devices()
        return True

    upnp = UPNPBrowser(sys.argv[1])
    candidates = upnp.find_content(targets, start_date)

    if not candidates:
        print('No videos found matching criteria')
        return True

    print upnp.device.services

    c = candidates.keys()[0]
    print c


    return True


if __name__ == '__main__':

    target_date = '20181120'

    target_list = [
        # 'Doctor Who',
        # 'Castle',
        # 'The First',
        'The Big Bang Theory'
    ]

    try:
        main(target_list, target_date)
        sys.exit(0)
    except (ValueError, UPNPBrowserException) as exp:
        print exp
        sys.exit(1)
