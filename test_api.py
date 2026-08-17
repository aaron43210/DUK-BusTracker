import urllib.request
import json

try:
    req = urllib.request.Request("http://127.0.0.1:5004/api/v1/trip_state")
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print(json.dumps(data, indent=2))
except Exception as e:
    print("Error:", e)
