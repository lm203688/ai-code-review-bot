import os
import pickle

password = "admin123"
api_key = "sk-proj-abc123"

def execute(cmd):
    os.system(cmd)
    eval(cmd)

def load(data):
    return pickle.loads(data)

def debug():
    print("debug info")
