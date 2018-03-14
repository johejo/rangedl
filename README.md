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

and

If you want to use it as a command line tool like GNU Wget, please run the attached script.

old version
```bash
$ rangedl http://ftp.jaist.ac.jp/pub/Linux/ubuntu-releases/17.04/ubuntu-17.04-server-amd64.iso -n 10
```

new version
```bash
$ rngdl http://ftp.jaist.ac.jp/pub/Linux/ubuntu-releases/17.04/ubuntu-17.04-server-amd64.iso -n 10
```

### Sample
Use from Python

```python
from rangedl import RangeDownloader

rd = RangeDownloader(url='http://ftp.jaist.ac.jp/pub/Linux/ubuntu-releases/17.04/ubuntu-17.04-desktop-amd64.iso', 
                     num=10, 
                     part_size=1000000
                     )
rd.download()
```