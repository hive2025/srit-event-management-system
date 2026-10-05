import sys, base64
s = sys.argv[1]
s += '=' * (-len(s) % 4)
data = base64.b64decode(s).decode('utf-8')
with open(r'd:/srit event managemet (3)/srit event managemet/app_step4.py', 'a', encoding='utf-8') as out:
    out.write(data)
print('Appended', len(data), 'chars')
