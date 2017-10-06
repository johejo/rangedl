import requests
import socket
from .exceptions import (
    SeparateHeaderError, GetOrderError, HttpResponseError, AcceptRangeError, HeadResponseError, RedirectionError
)


def get_length(url):
    hr = requests.head(url.scheme + '://' + url.netloc + url.path)

    if hr.status_code == 302 or hr.status_code == 303 or hr.status_code == 307:
        raise RedirectionError((hr.headers['Location']))

    elif hr.status_code != 200:
        raise HeadResponseError('STATUS CODE ' + str(hr.status_code))

    try:
        hr.headers['Accept-Ranges']
    except KeyError:
        raise AcceptRangeError('Server does not accept Range-header.')

    return int(hr.headers['content-length'])


def separate_header(resp):
    index = resp.find(b'\r\n\r\n')

    if index < 0:
        raise SeparateHeaderError('Cannot separate header.')

    header = resp[:index]
    body = resp[index + len(b'\r\n\r\n'):]

    return header, body


def get_order(header, chunk_size):
    check_status_code(header)

    index = header.rfind(b'content-range: bytes ')

    if index < 0:
        index = header.rfind(b'Content-Range: bytes ')

    if index < 0:
        raise GetOrderError('Cannot get order.')

    tmp = header[index + len(b'Content-Range: bytes '):]
    index = tmp.find(b'-')

    if index < 0:
        raise GetOrderError('Cannot get order.')

    order = int(tmp[:index]) // chunk_size
    return order


def check_status_code(header):
    index = header.find(b'\r\n')
    request_line = header[:index]

    index = request_line.find(b'HTTP/1.1 206 Partial Content')
    if index < 0:
        raise HttpResponseError('HttpResponseError\n' + 'Response-Line: ' + request_line.decode())

    else:
        return True


def addr2sock(address):
    sock = socket.create_connection(address)
    sock.setblocking(False)
    return sock


def map_all(es):
    return all([e == es[0] for e in es[1:]]) if es else False
