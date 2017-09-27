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
from .exceptions import (
    SeparateHeaderError, GetOrderError, HttpResponseError, HeadResponseError, AcceptRangeError, FileSizeError
)
from .utils import (
    get_length, get_order, separate_header, addr2sock, map_all
)

MAX_NUM_OF_CONNECTION = 10
V1_DEFAULT_WEIGHT = 10
V2_DEFAULT_WEIGHT = 5
DEFAULT_TIMEOUT = 5
STACK_ALGORITHM_V1 = 'STACK_ALGORITHM_V1'
STACK_ALGORITHM_V2 = 'STACK_ALGORITHM_V2'
TIMEOUT_ALGORITHM = 'TIMEOUT_ALGORITHM'


local_logger = getLogger(__name__)
local_logger.addHandler(NullHandler())


class RangeDownloader(object):
    def __init__(self, urls, num, part_size, progress=True, debug=False):
        urls = [urlparse(url) for url in urls]
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

        self._connection_num = num

        self._part_size = part_size

        if self._part_size == 0:
            self._part_size = 1000 * 1000

        try:
            length_list = [get_length(url) for url in urls]
            if map_all(length_list) is False:
                raise FileSizeError('The size of the target file differs for each mirror')
            self._length = length_list[0]

        except AcceptRangeError as e:
            print(e, file=sys.stderr)
            exit(1)

        except HeadResponseError as e:
            print(e, file=sys.stderr)
            exit(1)

        except FileSizeError as e:
            print(e, file=sys.stderr)
            exit(1)
        
        self._start_time = 0
        self._end_time = 0

        self._check_size = self._length // self._connection_num
        if self._check_size > self._part_size:
            self._chunk_size = self._part_size
        else:
            self._chunk_size = self._check_size

        self._req_num = self._length // self._chunk_size
        self._reminder = self._length % self._chunk_size

        if urls[0].port is None:
            port = '80'
        else:
            port = urls[0].port

        self._sockets = {}

        conn_num_per_a_address = int(self._connection_num // len(urls))
        conn_reminder = int(self._connection_num % len(urls))

        for url in urls:
            address = (socket.gethostbyname(url.hostname), port)
            for i in range(conn_num_per_a_address):
                sock = addr2sock(address)
                self._sockets[sock.fileno()] = {'socket': sock, 'address': address, 'url': url}
            if conn_reminder > 0:
                sock = addr2sock(address)
                self._sockets[sock.fileno()] = {'socket': sock, 'address': address, 'url': url}
                conn_reminder -= 1

        self._sel = selectors.DefaultSelector()
        self._filename = os.path.basename(urls[0].path)
        f = open(self._filename, 'wb')
        f.close()

        self._sock_buf = {}
        self._stacks = {}
        self._request_buf = {}
        for s in self._sockets.values():
            self._sel.register(s['socket'], selectors.EVENT_READ)
            self._sock_buf[s['socket'].fileno()] = {'data': bytearray(),
                                                    'timeout': 0,
                                                    'time_begin': time.time(),
                                                    'total': 0,
                                                    'throughput': 0,
                                                    'thp_begin': time.time()
                                                    }
            self._stacks[s['socket'].fileno()] = 0
            self._request_buf[s['socket'].fileno()] = ''

        self._begin = self._i = self._total = self._ri = self._wi = self._last_fd = 0

        self._write_list = [b'' for i in range(self._req_num + 1)]
        self._num_of_blocks_at_writing = []

        self._progress = progress

        self._timeout = None
        self._algorithm = None
        self._v1_threshold = None
        self._v2_weight = None
        self.set_stack_v1()

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
        message = '{0} {1} HTTP/1.1\r\nHost: {2}\r\n'.format(method,
                                                             self._sockets[key]['url'].path,
                                                             self._sockets[key]['url'].hostname
                                                             )
        if headers is not None:
            message += headers + '\r\n'

        message += '\r\n'
        self._request_buf[key] = message

        logger.debug('Send request part ' + str(self._i) + '\n' +
                     'fd ' + str(key) + ' send times ' + str(self._i) + '\n' +
                     message
                     )

        self._sockets[key]['socket'].sendall(message.encode())

    def _check_stack_v1(self):
        s = sum(self._stacks.values())
        if s > self._v1_threshold:
            target_key = max(self._stacks.items(), key=lambda x: x[1])[0]
            self._duplicate_request_func(key=target_key)

    def _check_stack_v2(self):
        ave = st.mean(self._stacks.values())
        for key, stack in self._stacks.items():
            if stack >= ave * self._v2_weight:
                self._duplicate_request_func(key=key)

    def _count_stack(self, key, logger=None):
        logger = logger or self._logger

        for k in self._stacks.keys():
            if k != key:
                self._stacks[k] += 1
            else:
                self._stacks[k] = 0
            logger.debug('fd ' + str(k) + ' ' + self._sockets[k]['url'].hostname + ' stack ' + str(self._stacks[k]))

    def _re_establish_connection(self, old_key):
        new_socket = addr2sock(self._sockets[old_key]['address'])
        new_key = new_socket.fileno()

        self._sockets[new_key] = {'socket': new_socket,
                                  'address': self._sockets[old_key]['address'],
                                  'url': self._sockets[old_key]['url']
                                  }
        self._sock_buf[new_key] = {'data': bytearray(), 
                                   'timeout': 0, 
                                   'time_begin': time.time(),
                                   'thp_begin': time.time(),
                                   'total': 0,
                                   'throughput': 0
                                   }
        self._stacks[new_key] = 0
        self._request_buf[new_key] = self._request_buf[old_key]
        self._sel.register(new_socket, selectors.EVENT_READ)

        self._sel.unregister(self._sockets[old_key]['socket'])
        self._sockets[old_key]['socket'].close()
        del self._sockets[old_key], self._sock_buf[old_key], self._stacks[old_key], self._request_buf[old_key]

        return new_key

    def _re_request(self, key, *, logger=None):
        logger = logger or self._logger
        self._sockets[key]['socket'].sendall(self._request_buf[key].encode())
        logger.debug('Send re-request part ' + str(self._i) + '\n' +
                     'fd ' + str(key) + ' send times ' + str(self._i) + '\n' +
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
            s['socket'].close()

        self._sel.close()

        if self._progress:
            self._progress_bar.close()

        self.print_result()

    def _check_timeout(self):
        for key, buf in self._sock_buf.items():
            if buf['timeout'] > self._timeout:
                self._duplicate_request_func(key=key)

    def _duplicate_request_func(self, key, *, logger=None):
        logger = logger or self._logger
        target_key = key
        logger.debug('fd ' + str(target_key) + ' ' +
                     str(self._sockets[target_key]['url'].hostname) + ' is not good connection.')
        new_key = self._re_establish_connection(target_key)
        self._re_request(new_key)
        logger.debug('Re-establish new connection fd ' + str(new_key))

    def print_info(self):
        self._logger.debug('URL')
        for s in self._sockets.values():
            self._logger.debug(s['url'].scheme + '://' + s['url'].netloc + s['url'].path)
        self._logger.debug('file size ' + str(self._length) + ' bytes' + '\n' +
                           'connection num ' + str(self._connection_num) + '\n' +
                           'chunk_size ' + str(self._chunk_size) + ' bytes' + '\n' +
                           'req_num ' + str(self._req_num + 1)
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
        self._logger.debug('THROUGHPUT')
        for key, buf in self._sock_buf.items():
            self._logger.debug('fd ' + str(key) + ' ' +
                               'host ' + self._sockets[key]['url'].hostname + ' ' +
                               str(buf['throughput'] * 8 / 1000 / 1000) + ' Mb/s')

    def set_stack_v1(self, weight=V1_DEFAULT_WEIGHT):
        self._v1_threshold = weight * self._connection_num
        self._algorithm = STACK_ALGORITHM_V1

    def set_timeout_algorithm(self, timeout=DEFAULT_TIMEOUT):
        self._algorithm = TIMEOUT_ALGORITHM
        self._timeout = timeout

    def set_stack_v2(self, weight=V2_DEFAULT_WEIGHT):
        self._algorithm = STACK_ALGORITHM_V2
        self._v2_weight = weight

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

                        self._sock_buf[key]['total'] += len(body)
                        self._sock_buf[key]['throughput'] = buf['total'] / (time.time() - buf['thp_begin'])

                        self._sock_buf[key]['time_begin'] = time.time()
                        logger.debug('TIMEOUT ' + str(key) + ' ' + str(self._sock_buf[key]['timeout']))

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
                        self._sock_buf[key]['timeout'] = time.time() - self._sock_buf[key]['time_begin']

                    if self._total >= self._length:
                        break

                    if self._algorithm == STACK_ALGORITHM_V1:
                        self._check_stack_v1()
                    elif self._algorithm == STACK_ALGORITHM_V2:
                        self._check_stack_v2()
                    elif self._algorithm == TIMEOUT_ALGORITHM:
                        self._check_timeout()
                self._write_block(f)
                gc.collect()
        self._fin()
