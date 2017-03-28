import json 
import os

c = 0
wfile = open('../data/java/1516/ast_nl.json', 'w')
for rt, dirs, files in os.walk(r'../out/'):
	for f in files:
		file = open(os.path.join(rt, f) , 'r')
		data = file.read()
		lines = data.split('\n')
		for line in lines:
			try:
				job = json.loads(line)
			except Exception as e:
				continue
			c += 1
			if c % 10000 == 0:
				print c
			newjob = {}
			newjob['pre'] = job['pre']
			newjob['back'] = job['back']
			newjob['bfs'] = job['bfs']
			newjob['mz'] = job['mz']
			newjob['nl'] = job['comment']
			newline = json.dumps(newjob) + '\n'
			wfile.write(newline)
print c