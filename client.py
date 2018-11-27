#!/usr/bin/python

import upnpclient
import xml.etree.ElementTree as ET
import argparse
import datetime
import requests
import logging
import math
import time
import sys
import re

# TODO - store/read timestamp of last execution


class UPNPBrowserException(Exception):
    pass


class UPNPBrowserNoMoreData(Exception):
    pass


class UPNPFile:

    def __init__(self, title, size, url):

        self.title = title
        self.size = size
        self.url = url


class UPNPBrowser:
    UPNP_DEVICES = {
        'humax2': {
            'url': 'http://192.168.1.5:55200',
            'dirs': ['My Contents', 'Recordings']
        },
        'humax3': {
            'url': 'http://192.168.1.5:56790',
            'dirs': ['My Contents', 'Recordings']
        },
        'humax': {
            'url': 'http://192.168.1.5:50001',
            'dirs': ['My Contents', 'Recordings']
        },
        'frodo': {
            'url': 'http://192.168.1.8:8200/rootdesc.xml'
        }
    }

    PROGRESS_LEN = 50

    @staticmethod
    def set_log_handler():
        """ Ensure that ssdp errors have some where to go """

        log = logging.getLogger('ssdp')
        log.addHandler(logging.NullHandler())
        return True

    @staticmethod
    def convert_size(size):

        if size is None:
            return 'Unknown'

        if size == 0:
            return "0B"

        names = ("B", "KB", "MB", "GB")

        i = int(math.floor(math.log(size, 1024)))
        units = names[i]

        p = math.pow(1024, i)
        s = round(size / p, 2)
        return '{0}{1}'.format(s, units)

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

    @staticmethod
    def list_candidates(candidates, download=False, force=False):

        to_download = []

        if not candidates:
            print('No videos found matching criteria')
            return to_download

        for upnp_id in candidates:
            (title, res) = candidates[upnp_id]

            if res is None:
                print "Unable to locate res element"
                continue

            url = res.text
            title = title.text
            size = int(res.attrib['size'])
            duration = res.attrib['duration']

            size_str = UPNPBrowser.convert_size(size)
            print '{0} ({1}) {2}'.format(title, duration, size_str),

            if not download:
                continue

            if force:
                print
                ans = True
            else:
                print ' - '

                while True:
                    ans = raw_input('Download (y/n)? ')
                    if ans in ('y', 'Y', 'n', 'N'):
                        ans = ans.lower() == 'y'
                        break

            if ans:
                candidate = UPNPFile(title, size, url)
                to_download.append(candidate)

        return to_download

    @staticmethod
    def download(candidates):

        if not candidates:
            print('No videos to download')
            return True

        for candidate in candidates:

            title = candidate.title
            size = candidate.size
            src = candidate.url

            dst = '{0}.mp4'.format(title)

            print 'Downloading: {0}'.format(title)

            start = datetime.datetime.now()

            downloaded = 0

            r = requests.get(src, stream=True)
            with open(dst, 'wb') as f:
                for chunk in r.iter_content(chunk_size=4096):
                    if not chunk:
                        break

                    f.write(chunk)

                    downloaded += len(chunk)
                    progress = int(UPNPBrowser.PROGRESS_LEN * downloaded / size)

                    sys.stdout.write("\r[{0}{1}]".format('=' * progress, ' ' * (UPNPBrowser.PROGRESS_LEN-progress)))
                    sys.stdout.flush()

            f.close()

            end = datetime.datetime.now()
            duration = end - start
            duration -= datetime.timedelta(microseconds=duration.microseconds)

            speed = float(size) / duration.seconds / 1024 / 1024
            print ' Time: {0} ({1}MB/s)'.format(duration, round(speed,2))

            return True

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

        # print content

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
            if upnp_dir is None:
                title = e.find('dc:title', ns)
                if title is None:
                    print "{0}: Unable to locate title".format(id)
                    continue
                res = e.find('default:res', ns)
                if res is None:
                    print "{0}: Unable to locate res".format(id)
                    continue

                result[id] = (title, res)
                continue

            titles = e.findall('dc:title', ns)

            title = titles[0]
            if title is None:
                raise UPNPBrowserException('Unable to locate entry title in content')

            if title.text == upnp_dir:
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
                (title, res) = item

                if title is None:
                    print "No title element: Skipping item"
                    continue

                if target in title.text:
                    m = re.match('^.*_(\d{8})_\d{4}$', title.text)
                    if not m:
                        raise UPNPBrowserException('{0}: Unable to extract data'.format(title.text))
                    t = int(time.mktime(time.strptime(m.group(1), pattern)))
                    if t < start_date:
                        continue

                    candidates[item_id] = item
        return candidates


def main(targets, start_date):

    parser = argparse.ArgumentParser(description='Browse and  download UPNP files')
    parser.add_argument('-l', '--list', action='store_true')
    parser.add_argument('-d', '--download', action='store_true')
    parser.add_argument('-f', '--force', action='store_true')
    parser.add_argument('device_name', nargs='?')
    args = parser.parse_args()

    if args.list:
        if args.device_name:
            raise ValueError('Cannot supply device name with list option')
        if args.download or args.force:
            raise ValueError('Option not permitted with list option')

        UPNPBrowser.find_devices()
        return True

    download = args.download
    force = args.force

    upnp = UPNPBrowser(args.device_name)

    candidates = upnp.find_content(targets, start_date)

    to_download = upnp.list_candidates(candidates, download, force)

    if to_download:
        upnp.download(to_download)

    return True


if __name__ == '__main__':

    target_date = '20181120'
    target_date = '20180401'

    target_list = [
        # 'Doctor Who',
        # 'Castle',
        # 'The First',
        # 'The Big Bang Theory',
        # 'Mrs Wilson'
        'Tiny Tumble'
    ]

    try:
        main(target_list, target_date)
        sys.exit(0)
    except (ValueError, UPNPBrowserException) as exp:
        print exp
        sys.exit(1)
