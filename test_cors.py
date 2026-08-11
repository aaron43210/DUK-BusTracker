import urllib.request

req = urllib.request.Request("http://localhost:8000/api/v1/stops", method="OPTIONS")
req.add_header("Origin", "https://legendary-gaufre-1dc00c.netlify.app")
req.add_header("Access-Control-Request-Method", "GET")

try:
    with urllib.request.urlopen(req) as response:
        print(f"Status: {response.status}")
        print(f"Body: {response.read().decode('utf-8')}")
except urllib.error.HTTPError as e:
    print(f"Status: {e.code}")
    print(f"Body: {e.read().decode('utf-8')}")
except Exception as e:
    print(e)
