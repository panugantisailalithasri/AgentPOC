import json
data = json.load(open("reports/deployment-verification.json", encoding="utf-8"))
print("=== DEPLOYMENT HEALTHY:", data["DeploymentHealthy"])
print("=== COVERAGE COMPLETE:", data["CoverageComplete"])
print()
for sr in data["ServiceResults"]:
    stype = sr["service_type"]
    name  = sr["name"]
    print(f"  [{stype:12}] {name}")
    for c in sr["checks"]:
        st = c["status"]
        print(f"             {st:20} {c['check']}")
print()
print("FAILED CHECKS  :", len(data["FailedChecks"]))
print("MISSING CHECKS :", len(data["MissingChecks"]))
print("NOT FOUND      :", len(data["ResourceNotFound"]))
