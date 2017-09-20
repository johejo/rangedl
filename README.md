# rangedl

## Overview
HTTP Split Downloader Module written in Python.

## Description
An HTTP module for downloading large files more quickly using the HTTP Range header.
This module does not have many functions like some other HTTP libraries, but it makes efficient use of multiple connections.

## Requirements
Python3, tqdm, requests

See requirements.txt.


## Usage

```bash
$ pip install git+http://github.com/johejo/rangedl.git
```

or

If you want to simply use GNU Wget or cURL as a download tool from the command line, execute the attached http_download.py.

```bash
$ git clone https://github.com/johejo/rangedl.git
$ cd rangedl
$ pip install -r requirements.txt
$ python http_download.py http://ftp.jaist.ac.jp/pub/Linux/ubuntu-releases/17.04/ubuntu-17.04-desktop-amd64.iso -n 10 -s 1
```

### Sample
```python
from rangedl import RangeDownloader

rd = RangeDownloader(url='http://ftp.jaist.ac.jp/pub/Linux/ubuntu-releases/17.04/ubuntu-17.04-desktop-amd64.iso', 
                     num=10, 
                     part_size=1000000
                     )
rd.download()
```

## Other
Please wait for API document and more information.