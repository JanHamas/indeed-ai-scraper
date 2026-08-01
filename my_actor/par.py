import urllib.request
import ssl

proxy = 'http://brd-customer-hl_dbeb3119-zone-web_unlocker1:rbw9khccsoqe@brd.superproxy.io:44445'
url = 'https://www.indeed.com/jobs?q=machine+learning+engineer&start=10'

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({'https': proxy, 'http': proxy}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
)

try:
    print(opener.open(url).read().decode())
except Exception as e:
    print(f"Error: {e}")
