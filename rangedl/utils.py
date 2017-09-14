import requests
from .exception import SeparateHeaderError, GetOrderError


def get_length(url):
    hr = requests.head(url.scheme + '://' + url.netloc + url.path)
    return int(hr.headers['content-length'])


def separate_header(resp):
    index = resp.find(b'\r\n\r\n')

    if index < 0:
        raise SeparateHeaderError('Cannot separate header.')

    header = resp[:index]
    body = resp[index + len(b'\r\n\r\n'):]

    return header, body


def get_order(header, chunk_size):
    index = header.rfind(b'Content-Range: bytes ')

    if index < 0:
        raise GetOrderError('Cannot get order.')

    tmp = header[index + len(b'Content-Range: bytes '):]
    index = tmp.find(b'-')

    if index < 0:
        raise GetOrderError('Cannot get order.')

    order = int(tmp[:index])
    return order // chunk_size
