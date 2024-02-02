#!/usr/bin/env python3
import sys
import os
import threading
import argparse
from multiprocessing.dummy import Pool
from io import BytesIO
import requests
from tqdm import tqdm

MAX_RETRIES = 5

class PortManager:
    def __init__(self, ports):
        self.ports = ports
        self.lock = threading.Lock()

        for port in self.ports:
            port.busy = False

    def get_port(self):
        with self.lock:
            while True:
                for port in self.ports:
                    if not port.busy:
                        port.busy = True
                        return port

    def return_port(self, port):
        with self.lock:
            port.busy = False

class Port:
    def __init__(self, value):
        self.value = value
        self.busy = False

def get_file_size(url, port_manager):
    buffer = BytesIO()

    port = port_manager.get_port()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/116.0'
    }
    proxies = {
        'http': f'socks5h://127.0.0.1:{port.value}',
        'https': f'socks5h://127.0.0.1:{port.value}'
    }

    port_manager.return_port(port) # not mult-threaded

    try:
        # try HEAD
        response = requests.head(url, headers=headers, proxies=proxies, timeout=30)
        response.raise_for_status()
        return int(response.headers.get('content-length', 0))
    except requests.exceptions.RequestException as head_error:
        print(f"HEAD request failed. Attempting GET request: {head_error}")

        try:
            # If try normal GET but with range 0-0
            response = requests.get(url, headers=headers | {'Range': 'bytes=0-0'}, proxies=proxies, timeout=30)
            response.raise_for_status()
            content_range = response.headers.get('content-range')

            if content_range:
                content_length = int(content_range.split('/')[-1])
                return content_length
            else:
                print("Error: Content-Range header not found in the response.")
                return None
        except requests.exceptions.RequestException as get_error:
            print(f"Error: {get_error}")
            return None

def download_chunk(url, byterange, progress_file, port_manager):
    port = port_manager.get_port()

    buffer = BytesIO()

    headers = {
        'Range': f'bytes={byterange[0]}-{byterange[1]}',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/116.0'
    }
    proxies = {
        'http': f'socks5h://127.0.0.1:{port.value}',
        'https': f'socks5h://127.0.0.1:{port.value}'
    }

    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.get(url, headers=headers, proxies=proxies, timeout=60)
            response.raise_for_status()

            data = response.content

            if len(data) == byterange[1] - byterange[0] + 1:
                with open(progress_file, 'wb') as file:
                    file.write(data)

                port_manager.return_port(port)
                return byterange[0], data

            print(f"Retrieved the incorrect amount of bytes. Received: {len(data)}, expected {byterange[1] - byterange[0] + 1}")
            retries += 1
        except requests.exceptions.RequestException as e:
            print(f"Error downloading chunk: {e}")
            retries += 1

    print(f"Failed to download chunk after {MAX_RETRIES} retries.")
    port_manager.return_port(port)
    return None

def main():
    parser = argparse.ArgumentParser(description="Download a single file using multiple SOCKS proxies in parallel. Think aria2 but with SOCKS support.")

    parser.add_argument("--url", help="URL of the file to be downloaded.")
    parser.add_argument("--filename", help="Output filename for the downloaded file.")
    parser.add_argument("--sport", type=int, help="Starting port for proxying.")
    parser.add_argument("--threads", type=int, help="Number of threads and total proxy ports. This assumes that every proxy is in numerical order from the starting port ('--sport 9000 --threads 2' means ports 9000 and 9001 are proxies).")

    args = parser.parse_args()

    if not args.url or not args.filename or args.sport is None or args.threads is None:
        parser.error("Invalid or missing arguments. Use --help for usage information.")

    start_port = int(args.sport)
    thread_count = int(args.threads)

    url = args.url
    filename = args.filename

    ports = [Port(start_port + i) for i in range(thread_count)]
    port_manager = PortManager(ports)

    size = get_file_size(url, port_manager)

    if not size or size <= 0:
        print("Cannot determine file size")
        sys.exit(1)

    progress_file_base = "/tmp/" + filename + ".progress"

    pool = Pool(thread_count)

    for i in range(thread_count):
        ports[i].busy = False

    chunksize = 1 << 20

    num_chunks = (size + chunksize - 1) // chunksize

    with tqdm(total=size//1000000, desc="Downloading", unit="MB") as progress_bar:
        for i in range(num_chunks):
            progress_file = f"{progress_file_base}.{i}"
            if os.path.exists(progress_file) and os.path.getsize(progress_file) == min(chunksize, size - i * chunksize):
                progress_bar.update(min(chunksize, size - i * chunksize)//1000000)
                continue

            byterange = (i * chunksize, min((i + 1) * chunksize - 1, size - 1))
            result = pool.apply_async(download_chunk, (url, byterange, progress_file, port_manager))
            result.get()
            progress_bar.update(min(chunksize, size - i * chunksize)//1000000)

    for i in range(num_chunks):
        progress_file = f"{progress_file_base}.{i}"
        if not os.path.exists(progress_file) or os.path.getsize(progress_file) != min(chunksize, size - i * chunksize):
            print(f"Error: Progress file {progress_file} is missing or has incorrect size.")
            sys.exit(1)

    with open(filename, 'wb') as output_file:
        for i in range(num_chunks):
            progress_file = f"{progress_file_base}.{i}"
            with open(progress_file, 'rb') as input_file:
                output_file.write(input_file.read())
            os.remove(progress_file)

if __name__ == "__main__":
    main()
