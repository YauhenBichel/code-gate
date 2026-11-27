import hashlib
import pickle
import subprocess

import requests

API_KEY = "example-example-example-1234"


def fetch_status(url):
    return requests.get(url).json()


def run_report(name):
    subprocess.run("generate-report " + name, shell=True)


def load_cache(blob):
    return pickle.loads(blob)


def checksum(text):
    return hashlib.md5(text.encode()).hexdigest()
