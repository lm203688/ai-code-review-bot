import os

password = "hardcoded_secret_123"
api_key = "sk-abc123def456"

def run_command(user_input):
    os.system(user_input)
    eval(user_input)
    return True

def load_config(data):
    import pickle
    return pickle.loads(data)

def read_file(path):
    f = open(path)
    data = f.read()
    return data
