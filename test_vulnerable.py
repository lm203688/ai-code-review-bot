import os
import pickle

def process_input(user_input):
    result = eval(user_input)  # 危险：代码注入
    password = "hardcoded_secret_123"  # 硬编码密码
    os.system("echo " + user_input)  # 命令注入风险
    data = pickle.loads(user_input)  # pickle反序列化风险
    return result

try:
    data = process_input(input_data)
except:  # 空except
    pass
