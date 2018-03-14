from yarl import URL
from argparse import ArgumentParser
from aiosphttp.downloader import Downloader
from tqdm import tqdm


def set_args():
    p = ArgumentParser()
    p.add_argument('URL', help='target URL')
    p.add_argument('-n', '--num', nargs='?', default=5, const=5, type=int,
                   help='num of TCP sessions')
    return p.parse_args()


def main():
    args = set_args()

    urls = [args.URL for _ in range(args.num)]
    d = Downloader(urls, dynamic_block_num_selection=False)
    bar = tqdm(total=len(d))

    with open(URL(args.URL).name, 'wb') as f:
        for p in d.generator():
            f.write(p)
            bar.update(len(p))


if __name__ == '__main__':
    main()
