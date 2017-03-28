import json
import os
import re
import sys
import string
import nltk
import random
import tensorflow as tf
from wheel.signatures.djbec import q

from tensorflow.python.platform import gfile

sys.setrecursionlimit(10000)

def create_set(directory):
    train_code_file = open(directory + '/train.token.code', 'rb')
    train_nl_file = open(directory + '/train.token.nl', 'rb')
    test_code_file = open(directory + '/test.token.code', 'rb')
    test_nl_file = open(directory + '/test.token.nl', 'rb')
    valid_code_file = open(directory + '/val.token.code', 'rb')
    valid_nl_file = open(directory + '/val.token.nl', 'rb')
    
    train_code_lines = train_code_file.readlines()
    train_nl_lines = train_nl_file.readlines()
    test_code_lines = test_code_file.readlines()
    test_nl_lines = test_nl_file.readlines()
    valid_code_lines = valid_code_file.readlines()
    valid_nl_lines = valid_nl_file.readlines()

    rid = 1

    with gfile.GFile(directory + '/acl/dev.txt', mode="w") as dev_file:
        with gfile.GFile(directory + '/acl/ref.txt', mode="w") as ref_file:
            for i in range(100):
                nl = train_nl_lines[i]
                code = train_code_lines[i]
                nl.replace('\r', ' ')
                nl.replace('\n', ' ')
                code.replace('\r', ' ')
                code.replace('\n', ' ')
                line = str(rid) + '\t' + str(rid) + '\t' + nl.strip() + '\t' + code.strip() + '\t'+ '0' + '\n'
                dev_file.write(line)
                ref_file.write(str(rid) + '\t' + nl.strip() + '\n')
                rid += 1

    with gfile.GFile(directory + '/acl/eval.txt', mode="w") as dev_file:
        with gfile.GFile(directory + '/acl/eref.txt', mode="w") as ref_file:
            for i in range(100, 200):
                nl = train_nl_lines[i]
                code = train_code_lines[i]
                nl.replace('\r', ' ')
                nl.replace('\n', ' ')
                code.replace('\r', ' ')
                code.replace('\n', ' ')
                line = str(rid) + '\t' + str(rid) + '\t' + nl.strip() + '\t' + code.strip() + '\t'+ '0' + '\n'
                dev_file.write(line)
                ref_file.write(str(rid) + '\t' + nl.strip() + '\n')
                rid += 1

    with gfile.GFile(directory + '/acl/train.txt', mode="w") as train_file:
        for i in range(200, len(train_nl_lines)):
            nl = train_nl_lines[i]
            code = train_code_lines[i]
            nl.replace('\r', ' ')
            nl.replace('\n', ' ')
            code.replace('\r', ' ')
            code.replace('\n', ' ')
            if random.random() > 0.75:
                line = str(rid) + '\t' + str(rid) + '\t' + nl.strip() + '\t' + code.strip() + '\t'+ '0' + '\n'
                train_file.write(line)
            rid += 1

    with gfile.GFile(directory + '/acl/valid.txt', mode="w") as valid_file:
        for i in range(len(valid_nl_lines)):
            nl = train_nl_lines[i]
            code = train_code_lines[i]
            nl.replace('\r', ' ')
            nl.replace('\n', ' ')
            code.replace('\r', ' ')
            code.replace('\n', ' ')
            if random.random() > 0.75:
                line = str(rid) + '\t' + str(rid) + '\t' + nl.strip() + '\t' + code.strip() + '\t'+ '0' + '\n'
                valid_file.write(line)
            rid += 1

    with gfile.GFile(directory + '/acl/test.txt', mode="w") as test_file:
        for i in range(len(test_nl_lines)):
            nl = train_nl_lines[i]
            code = train_code_lines[i]
            nl.replace('\r', ' ')
            nl.replace('\n', ' ')
            code.replace('\r', ' ')
            code.replace('\n', ' ')
            if random.random() > 0.75:
                line = str(rid) + '\t' + str(rid) + '\t' + nl.strip() + '\t' + code.strip() + '\t'+ '0' + '\n'
                test_file.write(line)
            rid += 1

    print rid

if __name__ == "__main__":
    create_set('data')