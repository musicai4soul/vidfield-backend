import json, urllib.request

req = urllib.request.Request("https://backboard.railway.app/graphql/v2",
    headers={"Authorization": "Bearer XLi_GDPCsy4URrhLdQbMq7E4Xk8fUsyZZtuJcBbi1Yl", "Content-Type": "application/json"},
    data=json.dumps({"query": "query { me { workspaces { edges { node { id name } } } } }"}).encode("utf-8"))

try:
    response = urllib.request.urlopen(req)
    print(response.read().decode("utf-8"))
except Exception as e:
    print(e)
