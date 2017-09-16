import selectors
import socket
import os
import sys
import gc
from urllib.parse import urlparse
from logging import getLogger, NullHandler
from tqdm import tqdm
from .exception import SeparateHeaderError, GetOrderError, HttpResponseError
from .utils import get_length, separate_header, get_order

MAX_NUM_OF_CONNECTION = 10
DEFAULT_WEIGHT = 10
local_logger = getLogger(__name__)
local_logger.addHandler(NullHandler())


class RangeDownloader(object):

    def __init__(self, url, num, part_size, progress=True, *, logger=local_logger):
        self._url = urlparse(url)
        self.logger = logger
        if num > MAX_NUM_OF_CONNECTION:
            num = MAX_NUM_OF_CONNECTION

        self._num = num

        self._part_size = part_size

        if self._part_size == 0:
            self._part_size = 1000 * 1000

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
        self._request_buf = {}
        for s in self._sockets.values():
            self._sel.register(s, selectors.EVENT_READ)
            self._buf[s.fileno()] = bytearray()
            self._stack[s.fileno()] = 0
            self._request_buf[s.fileno()] = ''

        self._begin = self._i = self._total = self._ri = self._wi = self._last_fd = 0

        self._write_list = [b'' for i in range(self._req_num + 1)]

        self._progress = progress

        self._STACK_THRESHOLD = self._num * DEFAULT_WEIGHT

        if self._progress:
            self._progress_bar = None

    def _initial_request(self):
        for key in self._sockets.keys():
            self._request(key, 'GET',
                          headers='Range: bytes={0}-{1}'.format(self._begin, self._begin + self._chunk_size - 1))
            self._begin += self._chunk_size
            self._i += 1

    def _request(self, key, method, *, headers, logger=None):
        logger = logger or local_logger
        message = '{0} {1} HTTP/1.1\r\nHost: {2}\r\n'.format(method, self._url.path, self._url.hostname)
        if headers is not None:
            # for header in headers:
            #     message += '{0}\r\n'.format(header)
            message += headers + '\r\n'

        message += '\r\n'
        self._request_buf[key] = message

        # logger.debug('Send request part', self._i, self._begin, "to", headers, "fd", self._sockets[key].fileno(),
        #              'send times', self._i, '\n')

        self._sockets[key].sendall(message.encode())

    def _check_stack(self, *, logger=None):
        logger = logger or local_logger
        s = sum(self._stack.values())
        if s > self._STACK_THRESHOLD:
            target_key = max(self._stack.items(), key=lambda x: x[1])[0]
            new_key = self._re_establish_connection(target_key)
            self._re_request(new_key)
            # logger.debug('fd', target_key, 'is not good connection.', 're-establish new connection fd', new_key)

    def _count_stack(self, key, logger=None):
        logger = logger or local_logger
        for k in self._stack.keys():
            if k != key:
                self._stack[k] += 1
            else:
                self._stack[k] = 0

            # logger.debug(k, '\t', self._stack[k])

    def _re_establish_connection(self, old_key):
        new_socket = socket.create_connection(self._address)
        new_socket.setblocking(0)
        new_key = new_socket.fileno()

        self._sockets[new_key] = new_socket
        self._buf[new_key] = bytearray()
        self._stack[new_key] = 0
        self._request_buf[new_key] = self._request_buf[old_key]
        self._sel.register(new_socket, selectors.EVENT_READ)

        self._sel.unregister(self._sockets[old_key])
        self._sockets[old_key].close()
        del self._sockets[old_key], self._buf[old_key], self._stack[old_key], self._request_buf[old_key]

        return new_key

    def _re_request(self, key):
        self._sockets[key].sendall(self._request_buf[key].encode())

    def _write_block(self, file, *, logger=None):
        logger = logger or local_logger
        current = self._wi
        while current < len(self._write_list):
            if self._write_list[current] != b'':
                file.write(self._write_list[current])
                self._write_list[current] = b''
                self._wi += 1
                # logger.debug('part', current, 'has written to the file', '\n')
            else:
                break
            current += 1

    def _fin(self):
        for s in self._sockets.values():
            s.close()

        self._sel.close()
        
        if self._progress:
            self._progress_bar.close()

        self.print_result()

    def print_info(self):
        print('URL', self._url.scheme + '://' + self._url.netloc + self._url.path + '\n'
              'file size', str(self._length) + 'bytes' + '\n'
              'connection num', str(self._num) + '\n'
              'chunk_size', str(self._chunk_size) + ' bytes' + '\n'
              'req_num', str(self._req_num + 1) + '\n'
              )

    def print_result(self):
        print('\nTotal file size', self._total, 'bytes')

    def set_threshold(self, val):
        self._STACK_THRESHOLD = val * self._num

    def download(self, *, logger=None):
        logger = logger or local_logger

        self.print_info()

        if self._progress:
            self._progress_bar = tqdm(total=self._length, file=sys.stdout)

        self._initial_request()

        with open(self._filename, 'ab') as f:
            while self._total < self._length:
                events = self._sel.select()

                for key, mask in events:
                    raw = key.fileobj.recv(32 * 1024)
                    self._buf[key.fd] += raw

                for key, buf in self._buf.items():
                    if len(buf) >= self._reminder:
                        try:
                            header, body = separate_header(buf)

                        except SeparateHeaderError:
                            continue

                        if key == self._last_fd:
                            if len(body) < self._reminder:
                                continue

                        else:
                            if len(body) < self._chunk_size:
                                continue

                        order = 0
                        try:
                            order = get_order(header, self._chunk_size)

                        except GetOrderError:
                            continue

                        except HttpResponseError as e:
                            print('\n' + str(e), file=sys.stderr)
                            f.close()
                            os.remove(self._filename)
                            exit(1)

                        if self._progress:
                            self._progress_bar.update(len(body))

                        # logger.debug('Received part', order, 'from fd', key, len(body), 'total', self._total,
                        #              'receive times', self._ri, '\n')

                        self._write_list[order] = body
                        self._total += len(body)
                        self._ri += 1
                        self._buf[key] = b''
                        self._count_stack(key)

                        if self._i <= self._req_num:
                            if self._i == self._req_num and self._reminder != 0:
                                self._last_fd = key
                                self._request(key, 'GET',
                                              headers='Range: bytes={0}-{1}'
                                              .format(self._begin, self._begin + self._reminder - 1))
                            else:
                                self._request(key, 'GET',
                                              headers='Range: bytes={0}-{1}'
                                              .format(self._begin, self._begin + self._chunk_size - 1))
                            self._begin += self._chunk_size
                            self._i += 1

                    if self._total >= self._length:
                        break

                    self._check_stack()
                self._write_block(f)
                gc.collect()
        self._fin()
