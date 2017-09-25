import selectors
import socket
import os
import sys
import gc
import time
import statistics as st
from urllib.parse import urlparse
from logging import getLogger, NullHandler, StreamHandler, DEBUG
from tqdm import tqdm
from .exceptions import SeparateHeaderError, GetOrderError, HttpResponseError, HeadResponseError, AcceptRangeError
from .utils import get_length, separate_header, get_order

MAX_NUM_OF_CONNECTION = 10
DEFAULT_WEIGHT = 10
DEFAULT_TIMEOUT = 5
DEFAULT_ALGORITHM = 'stack_count'
TIMEOUT_ALGORITHM = 'timeout'

local_logger = getLogger(__name__)
local_logger.addHandler(NullHandler())


class RangeDownloader(object):
    def __init__(self, urls, num, part_size, progress=True, debug=False):
        self._urls = [urlparse(url) for url in urls]
        self._debug = debug
        self._logger = local_logger

        if self._debug:
            handler = StreamHandler()
            handler.setLevel(DEBUG)
            self._logger.setLevel(DEBUG)
            self._logger.addHandler(handler)
            self._logger.propagate = False

        if num > MAX_NUM_OF_CONNECTION:
            num = MAX_NUM_OF_CONNECTION

        self._num = num

        self._part_size = part_size

        if self._part_size == 0:
            self._part_size = 1000 * 1000

        try:
            self._length = get_length(self._urls[0])

        except AcceptRangeError:
            exit(1)

        except HeadResponseError:
            exit(1)
        
        self._start_time = 0
        self._end_time = 0

        self._check_size = self._length // self._num
        if self._check_size > self._part_size:
            self._chunk_size = self._part_size
        else:
            self._chunk_size = self._check_size

        self._req_num = self._length // self._chunk_size
        self._reminder = self._length % self._chunk_size

        if self._urls[0].port is None:
            self._port = '80'
        else:
            self._port = self._urls[0].port

        self._address = (socket.gethostbyname(self._urls[0].hostname), self._port)
        self._sockets = {}
        for i in range(self._num):
            sock = socket.create_connection(self._address)
            sock.setblocking(False)
            self._sockets[sock.fileno()] = sock

        self._sel = selectors.DefaultSelector()
        self._filename = os.path.basename(self._urls[0].path)
        f = open(self._filename, 'wb')
        f.close()

        self._sock_buf = {}
        self._stack = {}
        self._request_buf = {}
        for s in self._sockets.values():
            self._sel.register(s, selectors.EVENT_READ)
            tmp = {'data': bytearray(), 'timeout': 0, 'timeout_begin': time.time()}
            self._sock_buf[s.fileno()] = tmp
            self._stack[s.fileno()] = 0
            self._request_buf[s.fileno()] = ''

        self._begin = self._i = self._total = self._ri = self._wi = self._last_fd = 0

        self._write_list = [b'' for i in range(self._req_num + 1)]
        self._num_of_blocks_at_writing = []

        self._progress = progress

        self._STACK_THRESHOLD = self._num * DEFAULT_WEIGHT
        self._TIMEOUT = DEFAULT_TIMEOUT
        self._ALGORITHM = DEFAULT_ALGORITHM

        if self._progress:
            self._progress_bar = None

    def _initial_request(self):
        for key in self._sockets.keys():
            self._request(key, 'GET',
                          headers='Range: bytes={0}-{1}'.format(self._begin, self._begin + self._chunk_size - 1))
            self._begin += self._chunk_size
            self._i += 1

    def _request(self, key, method, *, headers=None, logger=None):
        logger = logger or self._logger
        message = '{0} {1} HTTP/1.1\r\nHost: {2}\r\n'.format(method, self._urls[0].path, self._urls[0].hostname)
        if headers is not None:
            message += headers + '\r\n'

        message += '\r\n'
        self._request_buf[key] = message

        logger.debug('Send request part ' + str(self._i) + '\n' +
                     'fd ' + str(self._sockets[key].fileno()) +
                     ' send times ' + str(self._i) + '\n' +
                     message
                     )

        self._sockets[key].sendall(message.encode())

    def _check_stack(self, *, logger=None):
        logger = logger or self._logger
        s = sum(self._stack.values())
        if s > self._STACK_THRESHOLD:
            target_key = max(self._stack.items(), key=lambda x: x[1])[0]
            new_key = self._re_establish_connection(target_key)
            self._re_request(new_key)
            logger.debug('fd ' + str(target_key) + ' is not good connection. ' +
                         ' re-establish new connection fd ' + str(new_key))

    def _count_stack(self, key, logger=None):
        logger = logger or self._logger

        for k in self._stack.keys():
            if k != key:
                self._stack[k] += 1
            else:
                self._stack[k] = 0
            logger.debug('fd ' + str(k) + ' stack ' + str(self._stack[k]))

    def _re_establish_connection(self, old_key):
        new_socket = socket.create_connection(self._address)
        new_socket.setblocking(0)
        new_key = new_socket.fileno()

        self._sockets[new_key] = new_socket
        tmp = {'data': bytearray(), 'timeout': 0, 'timeout_begin': time.time()}
        self._sock_buf[new_key] = tmp
        self._stack[new_key] = 0
        self._request_buf[new_key] = self._request_buf[old_key]
        self._sel.register(new_socket, selectors.EVENT_READ)

        self._sel.unregister(self._sockets[old_key])
        self._sockets[old_key].close()
        del self._sockets[old_key], self._sock_buf[old_key], self._stack[old_key], self._request_buf[old_key]

        return new_key

    def _re_request(self, key, *, logger=None):
        logger = logger or self._logger
        self._sockets[key].sendall(self._request_buf[key].encode())
        logger.debug('Send re-request part ' + str(self._i) + '\n' +
                     'fd ' + str(self._sockets[key].fileno()) +
                     ' send times ' + str(self._i) + '\n' +
                     self._request_buf[key]
                     )

    def _write_block(self, file, *, logger=None):
        logger = logger or self._logger
        current = self._wi
        count = 0
        while current < len(self._write_list):
            if self._write_list[current] != b'':
                file.write(self._write_list[current])
                count += 1
                self._write_list[current] = b''
                self._wi += 1
                logger.debug('part ' + str(current) + ' has written to the file')
            else:
                break
            current += 1

        if count != 0:
            self._num_of_blocks_at_writing.append(count)

    def _fin(self):
        self._end_time = time.time()

        for s in self._sockets.values():
            s.close()

        self._sel.close()

        if self._progress:
            self._progress_bar.close()

        self.print_result()

    def _check_timeout(self, *, logger=None):
        logger = logger or self._logger
        for key, buf in self._sock_buf.items():
            if buf['timeout'] > self._TIMEOUT:
                target_key = key
                new_key = self._re_establish_connection(target_key)
                logger.debug('KEY ' + str(key) + ' TIMEOUT ' + str(buf['timeout']))
                logger.debug('fd ' + str(target_key) + ' is not good connection. ' +
                             ' re-establish new connection fd ' + str(new_key))

                self._re_request(new_key)

    def _duplicate_request_func(self):
        if self._ALGORITHM == DEFAULT_ALGORITHM:
            self._check_stack()
        elif self._ALGORITHM == TIMEOUT_ALGORITHM:
            self._check_timeout()

    def print_info(self):
        self._logger.debug('URL ' + self._urls[0].scheme + '://' + self._urls[0].netloc + self._urls[0].path + '\n' +
                           'file size ' + str(self._length) + ' bytes' + '\n' +
                           'connection num' + str(self._num) + '\n' +
                           'chunk_size' + str(self._chunk_size) + ' bytes' + '\n' +
                           'req_num' + str(self._req_num + 1) + '\n'
                           )

    def print_result(self):
        self._logger.debug('\n' +
                           'Total file size ' + str(self._total) + ' bytes' + '\n' +
                           'Time ' + str(self._end_time - self._start_time) + ' sec' + '\n' +
                           'Throughput ' +
                           str(self._total / (self._end_time - self._start_time) * 8 / 1000 / 1000) + ' Mb/s' + '\n' +
                           'Number of blocks at writing' + '\n' +
                           str(self._num_of_blocks_at_writing) + '\n' +
                           'MAX : ' + str(max(self._num_of_blocks_at_writing)) + '\n' +
                           'AVE : ' + str(st.mean(self._num_of_blocks_at_writing)) + '\n' +
                           'SD : ' + str(st.stdev(self._num_of_blocks_at_writing)) + '\n'
                           )

    def set_threshold(self, val):
        self._STACK_THRESHOLD = val * self._num

    def set_timeout_algorithm(self, timeout=DEFAULT_TIMEOUT):
        self._ALGORITHM = TIMEOUT_ALGORITHM
        self._TIMEOUT = timeout

    def download(self, *, logger=None):
        logger = logger or self._logger

        self.print_info()

        if self._progress:
            self._progress_bar = tqdm(total=self._length)

        self._start_time = time.time()
        self._initial_request()

        with open(self._filename, 'ab') as f:
            while self._total < self._length:
                events = self._sel.select()

                for key, mask in events:
                    raw = key.fileobj.recv(32 * 1024)
                    self._sock_buf[key.fd]['data'] += raw

                for key, buf in self._sock_buf.items():
                    if len(buf['data']) >= self._reminder:
                        try:
                            header, body = separate_header(buf['data'])

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
                            if self._debug:
                                print('', file=sys.stderr)

                        self._sock_buf[key]['timeout_begin'] = time.time()
                        logger.debug('ZERO ' + str(key) + ' ' + str(self._sock_buf[key]['timeout']))

                        logger.debug('Received part ' + str(order) +
                                     ' from fd ' + str(key) +
                                     ' len body ' + str(len(body)) +
                                     ' total ' + str(self._total) +
                                     ' receive times ' + str(self._ri) + '\n' +
                                     str(header.decode()))

                        self._write_list[order] = body
                        self._total += len(body)
                        if self._total >= self._length:
                            break

                        self._ri += 1
                        self._sock_buf[key]['data'] = b''
                        self._count_stack(key)

                        if self._i >= self._req_num and self._reminder != 0:
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

                    else:
                        self._sock_buf[key]['timeout'] = time.time() - self._sock_buf[key]['timeout_begin']

                    if self._total >= self._length:
                        break

                    self._duplicate_request_func()
                self._write_block(f)
                gc.collect()
        self._fin()
