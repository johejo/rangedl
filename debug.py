import argparse
from rangedl import RangeDownloader
from logging import basicConfig, getLogger, DEBUG

logger = getLogger(__name__)
basicConfig(level=DEBUG)


def set_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('URL', help='target URL')
    parser.add_argument('-n', '--num', nargs='?', default=5, const=5, help='num of TCP connection', type=int)
    parser.add_argument('-s', '--size', nargs='?', default=0, const=0, help='split size (MB)', type=int)
    parser.add_argument('-sk', '--size-kb', nargs='?', default=0, const=0, help='split size (KB)', type=int)
    parser.add_argument('-sg', '--size-gb', nargs='?', default=0, const=0, help='split size (GB)', type=int)
    parser.add_argument('-p', '--non-progress', action='store_false', help='disable progress bar using \'tqdm\'')
    args = parser.parse_args()
    return args


def main():
    args = set_args()
    part_size = (args.size * 1000 + args.size_kb + args.size_gb * 1000 * 1000) * 1000
    if part_size == 0:
        part_size = 1000 * 1000

    rd = RangeDownloader(args.URL, args.num, part_size, args.non_progress, logger=logger)
    rd.download()


if __name__ == '__main__':
    main()