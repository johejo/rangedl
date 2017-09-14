import selectors
import socket
import os
import sys
from urllib.parse import urlparse
from tqdm import tqdm
from .exception import SeparateHeaderError, GetOrderError
from .utils import get_length, separate_header, get_order


class RangeDownload(object):

    def __init__(self, url, num, part_size, debug=False):
        self._url = urlparse(url)
        self._num = num

        self._part_size = part_size
        if self._part_size == 0:
            self._part_size = 1000 * 1000

        self._debug = debug

        self._length = get_length(self._url)

        self._check_size = self._length // self._num
        if self._check_size > self._part_size:
            self._chunk_size = self._part_size
        else:
            self._chunk_size = self._check_size

        self._req_num = self._length // self._chunk_size
        self._reminder = self._length % self._chunk_size

        if self._url.port is None:
            self._port = '80'
        else:
            self._port = self._url.port

        self._address = (socket.gethostbyname(self._url.hostname), self._port)
        self._sockets = {}
        for i in range(self._num):
            sock = socket.create_connection(self._address)
            sock.setblocking(0)
            self._sockets[sock.fileno()] = sock

        self._sel = selectors.DefaultSelector()
        self._filename = os.path.basename(self._url.path)
        f = open(self._filename, 'wb')
        f.close()

        self._buf = {}
        self._stack = {}
        for s in self._sockets.values():
            self._sel.register(s, selectors.EVENT_READ)
            self._buf[s.fileno()] = bytearray()
            self._stack[s.fileno()] = 0

        self._begin = self._i = self._total = self._ri = self._wi = self._last_fd = 0

        self._write_list = [b'' for i in range(self._req_num + 1)]

        self._progress = None

    def _initial_request(self):
        for sock in self._sockets.values():
            self._request(sock, 'GET',
                          'Range: bytes={0}-{1}'.format(self._begin, self._begin + self._chunk_size - 1))
            self._begin += self._chunk_size
            self._i += 1

    def _request(self, sock, method, *headers):
        message = '{0} {1} HTTP/1.1\r\nHost: {2}\r\n'.format(method, self._url.path, self._url.hostname)
        if headers is not None:
            for header in headers:
                message += '{0}\r\n'.format(header)

        message += '\r\n'
        if self._debug:
            print("Send request part", self._i, self._begin, "to", headers, "fd", sock.fileno(),
                  'send times', self._i, '\n')
        sock.sendall(message.encode())

    def _count_stack(self, key):
        for k in self._stack.keys():
            if k != key:
                self._stack[k] += 1
            else:
                self._stack[k] = 0

            if self._debug:
                print(k, self._stack[k])

        if self._debug:
            print()

    def _write_block(self, file):
        current = self._wi
        while current < len(self._write_list):
            if self._write_list[current] != b'':
                file.write(self._write_list[current])
                self._write_list[current] = b''
                if self._debug:
                    print('part', current, 'has written to the file', '\n')
                self._wi += 1
            else:
                break
            current += 1

    def _fin(self):
        for s in self._sockets.values():
            s.close()

        self._sel.close()

    def print_info(self):
        print('URL', self._url.scheme + '://' + self._url.netloc + self._url.path + '\n'
              'file size', str(self._length) + '\n'
              'connection num', str(self._num) + '\n'
              'chunk_size', str(self._chunk_size) + ' bytes' + '\n'
              )

    def print_result(self):
        print('\nTotal file size', self._total, 'bytes')

    def download(self):
        if self._debug is False:
            self._progress = tqdm(total=self._length, file=sys.stdout)

        self._initial_request()
        x = 0

        with open(self._filename, 'ab') as f:
            while self._total < self._length:
                events = self._sel.select()

                for key, mask in events:
                    raw = key.fileobj.recv(32 * 1024)
                    self._buf[key.fd] += raw
                    x += len(raw)
                    if self._debug is False:
                        self._progress.update(len(raw))

                for key, value in self._buf.items():
                    if len(value) >= self._reminder:

                        try:
                            header, body = separate_header(value)
                        except SeparateHeaderError:
                            continue

                        if key == self._last_fd:
                            if len(body) < self._reminder:
                                continue

                        else:
                            if len(body) < self._chunk_size:
                                continue

                        try:
                            order = get_order(header, self._chunk_size)
                        except GetOrderError:
                            continue

                        if self._debug:
                            print('Received part', order, 'from fd', key, len(body), 'total', self._total,
                                  'receive times', self._ri, '\n')
                        self._write_list[order] = body
                        self._total += len(body)
                        self._ri += 1
                        self._buf[key] = b''
                        self._count_stack(key)

                        if self._i <= self._req_num:
                            if self._i == self._req_num and self._reminder != 0:
                                self._last_fd = key
                                self._request(self._sockets[key], 'GET',
                                              'Range: bytes={0}-{1}'
                                              .format(self._begin, self._begin + self._reminder - 1))
                            else:
                                self._request(self._sockets[key], 'GET',
                                              'Range: bytes={0}-{1}'
                                              .format(self._begin, self._begin + self._chunk_size - 1))
                            self._begin += self._chunk_size
                            self._i += 1

                    if self._total >= self._length:
                        break

                self._write_block(f)

        self._fin()
        print(x)
