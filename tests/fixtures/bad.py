import os
import pickle
import subprocess
import requests

API_KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"


def find_user(db, name):
    return db.execute("SELECT * FROM users WHERE name = '" + name + "'")


def run(cmd):
    subprocess.run("ls " + cmd, shell=True)
    return eval(cmd)


def load(blob):
    return pickle.loads(blob)


def fetch(url):
    return requests.get(url, verify=False)


def common(a, b):
    out = []
    for x in a:
        if x in b:
            out.append(x)
    for i in a:
        for j in b:
            for k in a:
                print(i, j, k)
    return out


def save(items=[]):
    try:
        f = open("out.txt", "w")
        f.write(str(items))
    except:
        pass


def todo_later():
    # TODO: implement
    pass
